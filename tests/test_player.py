"""Sound Player tests: both adapters, the real bundled soft_oal.dll, no NVDA.

Spec §10 puts the interface on the test surface, so these drive the four seam
methods the way `GlobalPlugin` will. A fake settings provider stands in for
NVDA's config, which is what makes compare-on-play, the device fallback,
reopen-storm prevention and the disconnected drop testable without touching
real device state.

These tests open a real output device. Where a machine has no audio endpoint
they skip rather than fail (spec §11).
"""

import ctypes
import logging
import math
import os
import struct
import time
from pathlib import Path

import pytest

# `conftest.py` puts the addon directory on sys.path, so this is the module
# itself rather than the addon package -- whose `__init__.py` imports NVDA.
# The player deliberately imports nothing from its own package, which is what
# lets it run in plain Python at all.
import player

POSITION = (0.0, 0.0, -1.0)  # dead ahead, on the unit sphere
RAMP_TICK = 0.012  # a little over one worker ramp tick
DEAD_ENDPOINT = "{0.0.0.00000000}.{00000000-dead-dead-dead-000000000000}"


# --------------------------------------------------------------------------
# doubles and helpers
# --------------------------------------------------------------------------


class FakeSettings:
    """The injected settings provider, under the test's control.

    Volume defaults to 0.0 so a test run is silent on the developer's machine
    while still exercising the whole path down to the mixer.
    """

    def __init__(self, device="default", volume=0.0):
        self.device = device
        self.level = volume
        self.device_reads = 0
        self.volume_reads = 0

    @property
    def output_device(self):
        self.device_reads += 1
        return self.device

    @property
    def volume(self):
        self.volume_reads += 1
        return self.level


class AngrySettings:
    """A settings provider that has fallen over. `play` must not care."""

    @property
    def output_device(self):
        raise RuntimeError("config went away")

    @property
    def volume(self):
        raise RuntimeError("config went away")


def tone(milliseconds=150, source_rate=44100, frequency=440.0, amplitude=500):
    """Quiet mono 16-bit PCM, as `SoundThemeLibrary.load()` hands it across."""
    count = int(source_rate * milliseconds / 1000.0)
    samples = [
        int(amplitude * math.sin(2.0 * math.pi * frequency * index / source_rate))
        for index in range(count)
    ]
    return struct.pack(f"<{count}h", *samples), source_rate


def theme(milliseconds=150):
    # Two different true source rates: OpenAL resamples per source, and #23 was
    # exactly the bug where a slot's real rate got ignored.
    return {
        "button": tone(milliseconds, 44100, 440.0),
        "link": tone(milliseconds, 48000, 660.0),
    }


def listener_gain(sound_player):
    dll = sound_player._al
    dll.alGetListenerf.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_float)]
    dll.alGetListenerf.restype = None
    value = ctypes.c_float(-1.0)
    dll.alGetListenerf(player.AL_GAIN, ctypes.byref(value))
    return value.value


def set_listener_gain(sound_player, value):
    sound_player._al.alListenerf(player.AL_GAIN, ctypes.c_float(value))


def playing_voices(sound_player):
    state = ctypes.c_int(0)
    count = 0
    for voice in sound_player._voices:
        sound_player._al.alGetSourcei(voice, player.AL_SOURCE_STATE, ctypes.byref(state))
        if state.value == player.AL_PLAYING:
            count += 1
    return count


def al_error(sound_player):
    return sound_player._al.alGetError()


def wait_until(predicate, timeout=10.0):
    """Poll for a worker-thread outcome. Polling belongs in tests, not the module."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


@pytest.fixture(scope="session")
def audio_endpoint():
    """Decide once whether this machine can play anything at all (spec §11).

    One probe against the *default* device, skipping only on
    `NoAudioEndpointError` -- the single site that means "there is no output
    device here". Every other construction in the suite is then strict, so a
    broken build fails instead of skipping: with the §9.3 named-device
    fallback removed, or the DLL missing, these tests go red, not green.
    """
    try:
        probe = player.OpenALSoundPlayer(FakeSettings())
    except player.NoAudioEndpointError as error:
        pytest.skip(f"no usable audio endpoint on this machine: {error}")
    probe.close()
    return True


@pytest.fixture
def make_player(audio_endpoint):
    """Build OpenAL adapters and always close them. Nothing here is caught."""
    built = []

    def factory(settings=None, **kwargs):
        settings = FakeSettings() if settings is None else settings
        adapter = player.OpenALSoundPlayer(settings, **kwargs)
        built.append(adapter)
        return adapter

    yield factory
    for adapter in reversed(built):
        adapter.close()


@pytest.fixture
def loaded(make_player):
    adapter = make_player()
    adapter.set_theme(theme())
    return adapter


# --------------------------------------------------------------------------
# the silent adapter
# --------------------------------------------------------------------------


def test_silent_adapter_is_four_no_ops_and_never_raises():
    silent = player.SilentSoundPlayer()
    assert silent.play("button", POSITION) is None
    assert silent.play("no-such-slot", (1e9, float("nan"), -0.0)) is None
    assert silent.set_theme(theme()) is None
    assert silent.set_theme({}) is None
    assert silent.set_reverb("hall") is None
    assert silent.set_reverb("nonsense") is None
    assert silent.close() is None
    assert silent.close() is None  # idempotent, like the real one
    assert silent.play("button", POSITION) is None  # still a no-op after close


def test_silent_adapter_has_the_seam_and_nothing_else():
    silent = player.SilentSoundPlayer()
    assert isinstance(silent, player.SoundPlayer)
    public = {name for name in vars(type(silent)) if not name.startswith("_")}
    assert public == {"play", "set_theme", "set_reverb", "close"}, (
        "degradedness lives in the wiring: no is_silent, no fifth method"
    )


def test_openal_adapter_satisfies_the_same_seam(make_player):
    assert isinstance(make_player(), player.SoundPlayer)


# --------------------------------------------------------------------------
# construction order
# --------------------------------------------------------------------------


def test_alsoft_conf_is_written_before_the_dll_is_loaded(make_player, monkeypatch, caplog, tmp_path):
    """Step 1 before step 2, and therefore before the first ALC call.

    OpenAL Soft reads the config lazily on the first ALC call, and every ALC
    call goes through the handle `ctypes.CDLL` returns -- so recording the
    environment at load time is the observable form of "early enough".
    """
    caplog.set_level(logging.DEBUG)
    stale = tmp_path / "someone-elses-alsoft.ini"
    stale.write_text("[general]\nperiods = 5\n", encoding="utf-8")
    monkeypatch.setenv("ALSOFT_CONF", str(stale))

    seen = {}
    real_cdll = ctypes.CDLL

    def recording_cdll(*args, **kwargs):
        seen["ALSOFT_CONF"] = os.environ.get("ALSOFT_CONF")
        return real_cdll(*args, **kwargs)

    monkeypatch.setattr(ctypes, "CDLL", recording_cdll)
    make_player()

    config_path = os.environ["ALSOFT_CONF"]
    assert seen["ALSOFT_CONF"] == config_path
    assert config_path != str(stale), "the prior value must be overwritten, not respected"
    assert Path(config_path).read_text(encoding="utf-8") == player.ALSOFT_CONF_BODY
    assert stale.name in caplog.text, "the prior ALSOFT_CONF value is the tripwire; it must be logged"


def test_alsoft_conf_states_the_rendering_requirements_no_api_call_can_state(make_player):
    """`ALSOFT_CONF` is the highest-priority config source, so these belong here.

    Pinned by value, not by shape: each line is load-bearing and silently
    reversible by a user-level `alsoft.ini` if it goes missing. `stereo-encoding`
    must be the 1.23+ spelling -- `hrtf = true` is deprecated and soft_oal warns
    on it.
    """
    body = player.ALSOFT_CONF_BODY
    assert body.startswith("[general]\n")
    assert "\nperiods = 2\n" in body, "the one latency knob (spec §3)"
    assert "\nstereo-encoding = hrtf\n" in body
    assert "\nhrtf = " not in body, "the pre-1.23 spelling is deprecated"
    assert "\nhrtf-mode = full\n" in body, "per-source HRIR; ambiN blurs elevation and front/back"
    assert "\nchannels = stereo\n" in body, "HRTF is declined outright on a non-stereo device"


def test_hrtf_is_active_on_the_real_device(make_player, caplog):
    """The whole point of the addon, asserted against the bundled soft_oal.

    Skips rather than fails where the machine genuinely cannot render it -- a
    CI runner with no stereo endpoint is not a broken build -- but a *denied*
    request is, because that means our own config stopped restating it.
    """
    caplog.set_level(logging.DEBUG)
    adapter = make_player()

    enabled = ctypes.c_int(0)
    status = ctypes.c_int(0)
    adapter._al.alcGetIntegerv(adapter._device, player.ALC_HRTF_SOFT, 1, ctypes.byref(enabled))
    adapter._al.alcGetIntegerv(
        adapter._device, player.ALC_HRTF_STATUS_SOFT, 1, ctypes.byref(status)
    )
    reason = player.HRTF_STATUS_NAMES.get(status.value, status.value)

    assert status.value != 0x0002, f"HRTF was denied ({reason}); ALSOFT_CONF is not winning"
    if not enabled.value:
        pytest.skip(f"this machine cannot render HRTF: {reason}")
    assert "HRTF active" in caplog.text, "an active HRTF must name its dataset in the log"


def test_a_device_without_hrtf_says_why_rather_than_just_that(make_player, caplog, monkeypatch):
    """The warning has to carry the cause; "not active" alone is unactionable."""
    caplog.set_level(logging.DEBUG)
    adapter = make_player()

    def unsupported_format(device, token, size, values):
        # ALC_HRTF_SOFT -> false, ALC_HRTF_STATUS_SOFT -> the endpoint is not stereo.
        # `values` arrives as a byref object, which has to be cast to be written.
        target = ctypes.cast(values, ctypes.POINTER(ctypes.c_int))
        target[0] = 0x0005 if token == player.ALC_HRTF_STATUS_SOFT else 0
        return None

    monkeypatch.setattr(adapter._al, "alcGetIntegerv", unsupported_format)
    caplog.clear()
    adapter._log_hrtf_state()

    assert "not active" in caplog.text
    assert "not stereo" in caplog.text, "the status must reach the log as a cause, not a number"


def test_reading_the_hrtf_state_can_never_break_construction(make_player, monkeypatch, caplog):
    """Diagnostics only: HRTF being unreadable makes the addon worse, not broken."""
    caplog.set_level(logging.DEBUG)
    adapter = make_player()

    def angry(*args, **kwargs):
        raise OSError("the driver went away mid-query")

    monkeypatch.setattr(adapter._al, "alcGetIntegerv", angry)
    assert adapter._log_hrtf_state() is None
    assert "could not read the HRTF state" in caplog.text


def test_a_named_device_that_will_not_open_falls_back_to_the_default(make_player, caplog):
    caplog.set_level(logging.INFO)
    adapter = make_player(FakeSettings(device=DEAD_ENDPOINT))
    assert adapter._device is not None
    assert adapter._requested_device == DEAD_ENDPOINT, "the last *requested* device is what we compare against"
    assert "falling back to the default device" in caplog.text
    assert adapter._playable


def test_system_events_are_registered_and_unregistered_before_teardown(make_player):
    adapter = make_player()
    assert adapter._events_registered, "soft_oal 1.23+ should offer ALC_SOFT_system_events"
    assert adapter._event_proc is not None, "the ctypes callback needs a strong reference"
    adapter.close()
    assert not adapter._events_registered
    assert adapter._device is None, "the device is only handed back after the callback is gone"


def test_the_device_event_callback_is_total(make_player):
    """It runs on an OpenAL C thread: an exception there kills NVDA."""
    adapter = make_player()

    class ExplodingEvent:
        def set(self):
            raise MemoryError("out of everything")

    real_wake = adapter._wake
    adapter._wake = ExplodingEvent()
    try:
        adapter._on_alc_event(
            player.ALC_EVENT_TYPE_DEVICE_REMOVED_SOFT,
            player.ALC_PLAYBACK_DEVICE_SOFT,
            None,
            0,
            None,
            None,
        )  # must return, not raise
    finally:
        adapter._wake = real_wake  # the worker is waiting on the real one


def test_close_clears_the_callback_even_when_disabling_events_raises(make_player, caplog):
    """The clear is the call that matters: it is what makes OpenAL forget us."""
    caplog.set_level(logging.DEBUG)
    adapter = make_player()
    real_control = adapter._alc_event_control
    real_callback = adapter._alc_event_callback
    cleared = []

    def angry_control(count, types, enable):
        raise RuntimeError("the driver is having a bad day")

    def recording_callback(proc, user_param):
        cleared.append(proc)

    adapter._alc_event_control = angry_control
    adapter._alc_event_callback = recording_callback
    adapter.close()

    assert cleared == [None], "the callback pointer is cleared even when the disable fails"
    assert not adapter._events_registered
    # Our stubs meant OpenAL never really heard any of that; put it back to a
    # safe state before the next test opens a device.
    real_control(len(player._EVENT_TYPES), player._EVENT_TYPES, player.AL_FALSE)
    real_callback(None, None)


def test_a_callback_that_cannot_be_cleared_is_kept_alive(make_player):
    """A collected trampoline OpenAL still points at is a crash, not a leak."""
    adapter = make_player()
    real_control = adapter._alc_event_control
    real_callback = adapter._alc_event_callback
    trampoline = adapter._event_proc

    def angry_callback(proc, user_param):
        raise RuntimeError("cannot clear")

    adapter._alc_event_callback = angry_callback
    before = len(player._ORPHANED_EVENT_PROCS)
    adapter.close()

    assert len(player._ORPHANED_EVENT_PROCS) == before + 1
    assert player._ORPHANED_EVENT_PROCS[-1] is trampoline
    real_control(len(player._EVENT_TYPES), player._EVENT_TYPES, player.AL_FALSE)
    real_callback(None, None)


def test_a_construction_failure_that_is_not_a_missing_endpoint_is_not_skippable(tmp_path):
    """Only "this machine has no audio" may skip; everything else must fail."""
    broken = tmp_path / "not-really-a.dll"
    broken.write_bytes(b"certainly not a PE image")
    with pytest.raises(RuntimeError) as caught:
        player.OpenALSoundPlayer(FakeSettings(), dll_path=str(broken))
    assert not isinstance(caught.value, player.NoAudioEndpointError)


# --------------------------------------------------------------------------
# compare-on-play
# --------------------------------------------------------------------------


def test_volume_change_is_picked_up_by_the_next_play(loaded):
    settings = loaded._settings
    loaded.play("button", POSITION)
    assert listener_gain(loaded) == pytest.approx(0.0)

    settings.level = 0.25
    loaded.play("button", POSITION)
    assert listener_gain(loaded) == pytest.approx(0.25), "volume mismatch is applied inline"


def test_an_unchanged_volume_is_not_rewritten_on_every_play(loaded):
    loaded.play("button", POSITION)
    set_listener_gain(loaded, 0.02)  # behind the player's back
    loaded.play("button", POSITION)
    loaded.play("button", POSITION)
    assert listener_gain(loaded) == pytest.approx(0.02), "play compares before it writes"


def test_a_device_change_is_noticed_by_play_and_handed_to_the_worker(loaded):
    settings = loaded._settings
    attempts = []
    real_reopen = loaded._alc_reopen

    def counting_reopen(device, name, attributes):
        attempts.append(name)
        return real_reopen(device, name, attributes)

    loaded._alc_reopen = counting_reopen

    settings.device = DEAD_ENDPOINT
    started = time.perf_counter()
    loaded.play("button", POSITION)
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    assert elapsed_ms < 25.0, "play hands the reopen to the worker and returns"
    assert loaded._requested_device == DEAD_ENDPOINT

    assert wait_until(lambda: attempts and not loaded._reopening)
    assert attempts[0] == DEAD_ENDPOINT.encode(), "the configured device is tried first"
    assert len(attempts) <= 2, "one named attempt, one default attempt, no more"
    if len(attempts) == 2:
        assert attempts[1] is None

    # The last-requested rule: the request is remembered even though it failed,
    # so a dead configured endpoint cannot start a reopen storm (#26 d7).
    settled = len(attempts)
    for _ in range(6):
        loaded.play("button", POSITION)
        time.sleep(0.01)
    time.sleep(0.2)
    assert len(attempts) == settled, "a dead endpoint must not reopen on every sound"


def test_a_failing_reopen_tries_named_then_default_then_gives_up(loaded, caplog):
    caplog.set_level(logging.DEBUG)
    settings = loaded._settings
    attempts = []

    def always_fails(device, name, attributes):
        attempts.append(name)
        return 0

    loaded._alc_reopen = always_fails

    settings.device = DEAD_ENDPOINT
    loaded.play("button", POSITION)
    assert wait_until(lambda: loaded._disconnected)
    assert attempts == [DEAD_ENDPOINT.encode(), None]

    # No timer, no backoff: nothing retries until the next device event.
    for _ in range(5):
        loaded.play("button", POSITION)
    time.sleep(0.3)
    assert attempts == [DEAD_ENDPOINT.encode(), None]

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING and "dropped" in r.message]
    assert len(warnings) == 1, "one warning on entering disconnected, never one per play"


def fire_event(adapter, event_type):
    adapter._on_alc_event(event_type, player.ALC_PLAYBACK_DEVICE_SOFT, None, 0, None, None)


def test_an_os_default_move_only_reopens_when_we_follow_the_default(loaded):
    """Eager, per spec §9.3 -- but a working pinned device does not care."""
    attempts = []

    def stub_reopen(device, name, attributes):
        attempts.append(name)
        return 1

    loaded._alc_reopen = stub_reopen
    fire_event(loaded, player.ALC_EVENT_TYPE_DEFAULT_DEVICE_CHANGED_SOFT)
    assert wait_until(lambda: attempts == [None]), "following the default means following it eagerly"

    # A pinned device, opened successfully: not on the fallback, not disconnected.
    loaded._settings.device = "pinned-endpoint"
    loaded._requested_device = "pinned-endpoint"
    loaded._on_fallback = False
    fire_event(loaded, player.ALC_EVENT_TYPE_DEFAULT_DEVICE_CHANGED_SOFT)
    time.sleep(0.2)
    assert attempts == [None], "a default move is not our business when a working device is pinned"


def test_an_os_default_move_is_a_way_back_when_we_are_on_the_fallback(make_player):
    """The user pinned a device that failed; the default just moved to a live one."""
    adapter = make_player(FakeSettings(device=DEAD_ENDPOINT))
    assert adapter._on_fallback
    attempts = []

    def stub_reopen(device, name, attributes):
        attempts.append(name)
        return 0 if name is not None else 1

    adapter._alc_reopen = stub_reopen
    fire_event(adapter, player.ALC_EVENT_TYPE_DEFAULT_DEVICE_CHANGED_SOFT)
    assert wait_until(lambda: len(attempts) >= 2)
    assert attempts == [DEAD_ENDPOINT.encode(), None]
    assert adapter._playable


def test_a_device_event_acts_on_the_device_configured_now(loaded):
    """The memo goes stale while disconnected, because play() never refreshes it."""
    loaded._mark_disconnected()
    attempts = []

    def stub_reopen(device, name, attributes):
        attempts.append(name)
        return 1

    loaded._alc_reopen = stub_reopen
    loaded._settings.device = "a-working-device"  # user picks one in NVDA's settings
    fire_event(loaded, player.ALC_EVENT_TYPE_DEVICE_ADDED_SOFT)

    assert wait_until(lambda: loaded._playable), "a disconnected player must be reachable again"
    assert attempts == [b"a-working-device"], "the event acts on current config, not on the memo"
    assert loaded._requested_device == "a-working-device"


def test_a_reopen_that_raises_leaves_the_player_recoverable(loaded, caplog):
    """A raise must land as "disconnected", never as silence with no way back."""
    caplog.set_level(logging.DEBUG)

    def exploding_reopen(device, name, attributes):
        raise OSError("the driver went away mid-call")

    loaded._alc_reopen = exploding_reopen
    loaded._settings.device = DEAD_ENDPOINT
    loaded.play("button", POSITION)

    assert wait_until(lambda: loaded._disconnected)
    assert not loaded._reopening
    assert not loaded._playable

    attempts = []

    def stub_reopen(device, name, attributes):
        attempts.append(name)
        return 1

    loaded._alc_reopen = stub_reopen
    fire_event(loaded, player.ALC_EVENT_TYPE_DEVICE_ADDED_SOFT)
    assert wait_until(lambda: loaded._playable), "the next device event is a genuine retry"
    assert not loaded._disconnected
    assert attempts == [DEAD_ENDPOINT.encode()]


def test_a_device_event_retries_the_configured_device_we_fell_back_from(make_player):
    """The event is the retry: no timer ever asks whether the device came back."""
    adapter = make_player(FakeSettings(device=DEAD_ENDPOINT))
    assert adapter._on_fallback, "construction landed on the default device"
    attempts = []

    def stub_reopen(device, name, attributes):
        attempts.append(name)
        return 0 if name is not None else 1  # still absent; the default still works

    adapter._alc_reopen = stub_reopen
    fire_event(adapter, player.ALC_EVENT_TYPE_DEVICE_ADDED_SOFT)
    assert wait_until(lambda: len(attempts) >= 2)
    assert attempts == [DEAD_ENDPOINT.encode(), None]
    assert not adapter._disconnected, "a working fallback is not a disconnection"
    assert adapter._playable


def test_play_never_raises_when_the_settings_provider_does(make_player, caplog):
    caplog.set_level(logging.DEBUG)
    adapter = make_player(AngrySettings())
    adapter.set_theme(theme())
    for _ in range(5):
        adapter.play("button", POSITION)
    assert len(adapter._active) == 0
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR and "play(" in r.message]
    assert len(errors) == 1, "a broken provider is logged once, not once per sound"


# --------------------------------------------------------------------------
# the disconnected contract
# --------------------------------------------------------------------------


def test_disconnected_drops_plays_and_queues_nothing(loaded, caplog):
    caplog.set_level(logging.DEBUG)
    loaded._mark_disconnected()
    loaded._mark_disconnected()  # a second entry must not log again

    reads_before = loaded._settings.volume_reads
    for _ in range(20):
        loaded.play("button", POSITION)
    assert len(loaded._active) == 0
    assert playing_voices(loaded) == 0
    assert loaded._settings.volume_reads == reads_before, (
        "the boolean is checked ahead of any other work, including compare-on-play"
    )

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING and "dropped" in r.message]
    assert len(warnings) == 1

    # Coming back plays nothing that was dropped -- nothing was queued.
    loaded._disconnected = False
    loaded._update_playable()
    time.sleep(0.05)
    assert playing_voices(loaded) == 0
    loaded.play("button", POSITION)
    assert playing_voices(loaded) == 1


# --------------------------------------------------------------------------
# themes, voices, reverb
# --------------------------------------------------------------------------


def test_play_is_a_no_op_for_unknown_slots_and_before_a_theme_is_set(make_player):
    adapter = make_player()
    adapter.play("button", POSITION)  # no theme yet
    assert len(adapter._active) == 0
    adapter.set_theme(theme())
    adapter.play("treeviewitem", POSITION)  # slot the theme does not provide
    assert len(adapter._active) == 0
    adapter.play("button", POSITION)
    assert len(adapter._active) == 1


def test_play_is_fast_enough_to_sit_on_the_main_thread(loaded):
    loaded.play("button", POSITION)  # first play warms the ctypes call path
    worst = 0.0
    for _ in range(20):
        started = time.perf_counter()
        loaded.play("button", POSITION)
        worst = max(worst, (time.perf_counter() - started) * 1000.0)
    assert worst < 5.0, f"play took {worst:.2f} ms; the whole dispatch budget is ~10 ms"


def test_the_voice_pool_steals_the_oldest_voice_through_a_ramp(make_player, caplog):
    """Fast navigation: sounds arrive while eight are still ringing."""
    caplog.set_level(logging.DEBUG)
    adapter = make_player()
    adapter.set_theme(theme(milliseconds=600))
    for _ in range(15):
        adapter.play("button", POSITION)
        time.sleep(0.020)

    assert len(adapter._active) <= player.VOICE_CAP, "polyphony cap not held"
    assert "without a ramp" not in caplog.text, "steals at navigation speed must all be ramped"
    assert wait_until(
        lambda: len(adapter._idle) + len(adapter._active) == len(adapter._voices)
    ), "voices leaked into the retiring queue"
    assert al_error(adapter) == player.AL_NO_ERROR


def test_a_starved_acquire_evicts_exactly_one_voice(make_player):
    """The hard cut costs one voice, not two (the retire-then-cut double eviction)."""
    adapter = make_player()
    adapter.set_theme(theme(milliseconds=600))
    for _ in range(player.VOICE_CAP + player.RAMP_HEADROOM):
        adapter.play("button", POSITION)
    assert not adapter._idle, "the pool should be starved by now"

    active_before = len(adapter._active)
    retiring_before = len(adapter._retiring)
    adapter.play("button", POSITION)

    # `_active` is the main thread's alone, so this is exact: the double
    # eviction showed up here as one voice fewer. `_retiring` is drained by the
    # worker concurrently, so it can only be asserted in the direction the
    # worker cannot move it -- it never grows behind our back.
    assert len(adapter._active) == active_before, "one voice out, one voice in"
    assert len(adapter._retiring) <= retiring_before, "a starved steal must not also fill the ramp"


def test_a_device_event_is_serviced_while_steals_keep_arriving(make_player):
    """Fast navigation must not defer the device policy until it stops.

    A steal every few ticks is enough to keep the worker inside the ramp loop
    forever if the loop keeps admitting voices, which would turn "the device
    event is the retry" into "the retry happens when the user stops reading".
    """
    adapter = make_player()
    adapter.set_theme(theme(milliseconds=500))
    attempts = []

    def stub_reopen(device, name, attributes):
        attempts.append(name)
        return 1

    adapter._alc_reopen = stub_reopen

    # The event has to land while a ramp is in flight, which is the only state
    # that can trap the worker: fired between worker iterations it reaches
    # _service_devices on its way in and proves nothing. Two things follow.
    # Steals must arrive faster than one envelope (~56 ms), so this drives
    # plays harder than the measured 20/s navigation rate -- at 30 ms the
    # retirement pipeline never empties, which is the condition under test.
    # And the event fires only once that pipeline is saturated, not at the
    # first steal, when the worker has not yet entered the loop.
    fired_at = None
    started = time.monotonic()
    deadline = started + 3.0
    while time.monotonic() < deadline:
        adapter.play("button", POSITION)
        if fired_at is None and time.monotonic() - started > 0.8:
            fire_event(adapter, player.ALC_EVENT_TYPE_DEFAULT_DEVICE_CHANGED_SOFT)
            fired_at = time.monotonic()
        elif fired_at is not None and attempts:
            break
        time.sleep(0.03)

    assert fired_at is not None
    assert len(adapter._active) >= player.VOICE_CAP, "the pool never filled; the fixture is wrong"
    assert attempts, "a device event waited on the ramp loop"
    assert time.monotonic() - fired_at < 0.5, "the device event was serviced late"


def test_a_burst_beyond_the_ramp_headroom_still_plays_and_recycles(make_player):
    """More steals inside one ramp window than the headroom covers.

    The voice is cut without a ramp -- a possible click, which beats dropping
    the sound. Nothing may break, and every voice must come back.
    """
    adapter = make_player()
    adapter.set_theme(theme(milliseconds=600))
    for _ in range(30):
        adapter.play("button", POSITION)

    assert len(adapter._active) <= player.VOICE_CAP
    assert playing_voices(adapter) <= player.VOICE_CAP + player.RAMP_HEADROOM
    assert wait_until(
        lambda: len(adapter._idle) + len(adapter._active) == len(adapter._voices)
    ), "voices leaked into the retiring queue"
    assert al_error(adapter) == player.AL_NO_ERROR


def test_set_theme_replaces_samples_without_touching_a_playing_voice(loaded):
    loaded.play("button", POSITION)
    voice = loaded._active[0]
    before = dict(loaded._buffers)

    loaded.set_theme(theme(milliseconds=200))
    state = ctypes.c_int(0)
    loaded._al.alGetSourcei(voice, player.AL_SOURCE_STATE, ctypes.byref(state))
    assert state.value == player.AL_PLAYING, "a theme change must not interrupt a voice"
    assert set(loaded._buffers) == set(before)
    assert loaded._buffers != before, "new buffers, not overwritten ones"
    assert al_error(loaded) == player.AL_NO_ERROR


def test_set_theme_skips_unusable_slots_without_raising(make_player):
    adapter = make_player()
    adapter.set_theme(
        {
            "button": tone(),
            "empty": (b"", 44100),
            "no-rate": (b"\x00\x01" * 100, 0),
        }
    )
    assert set(adapter._buffers) == {"button"}


def test_every_reverb_preset_applies_immediately(loaded):
    for preset in ("none", "smallRoom", "mediumRoom", "hall", "none", "smallRoom"):
        loaded.set_reverb(preset)
        loaded.play("button", POSITION)
        assert loaded._reverb_preset == preset
        assert al_error(loaded) == player.AL_NO_ERROR, f"preset {preset} left an AL error"


def test_small_room_still_matches_todays_shipped_reverb():
    """Existing users must hear no change (spec §4.4)."""
    small = player.REVERB_PRESETS["smallRoom"]
    assert small.decay_time == pytest.approx(0.1 + (10 / 100.0) * 3.9)  # RoomSize 10
    assert small.gain_hf == pytest.approx(1.0 - (100 / 100.0) * 0.9)  # Damping 100
    assert small.gain == pytest.approx((9 / 100.0) * 0.5)  # WetLevel 9
    assert small.diffusion == pytest.approx(100 / 100.0)  # Width 100
    assert player.REVERB_PRESETS["none"] is None
    assert set(player.REVERB_PRESETS) == {"none", "smallRoom", "mediumRoom", "hall"}


def test_an_unknown_reverb_preset_is_ignored(loaded, caplog):
    caplog.set_level(logging.DEBUG)
    loaded.set_reverb("cathedral")
    assert loaded._reverb_preset == "smallRoom"
    assert "cathedral" in caplog.text


# --------------------------------------------------------------------------
# teardown
# --------------------------------------------------------------------------


def test_close_is_idempotent_and_everything_after_it_is_a_no_op(make_player):
    adapter = make_player()
    adapter.set_theme(theme())
    adapter.play("button", POSITION)
    adapter.close()
    adapter.close()
    assert adapter._worker is None or not adapter._worker.is_alive()
    adapter.play("button", POSITION)
    adapter.set_theme(theme())
    adapter.set_reverb("hall")
    assert not adapter._playable
