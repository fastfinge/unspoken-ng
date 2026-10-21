"""Speech router tests: routing, utterance boundaries and fallbacks, off NVDA.

The router patches whatever wave-player class it is handed, so each test hands
it a fresh fake with nvwave.WavePlayer's five playback methods and the
attributes the router reads. What the fakes record is what reached nvwave --
the original player, its stereo companion, or the Sound Player's speech
stream -- which is the router's whole observable behaviour.
"""

import ctypes
import struct
from types import SimpleNamespace

import pytest

import speechrouter
import spatial


LEFT = (-1.0, 0.0, 0.0)
RIGHT = (1.0, 0.0, 0.0)


def pcm(*samples):
    return struct.pack(f"<{len(samples)}h", *samples)


def make_wave_player_class():
    """A fresh class per test: the router patches the class, not instances."""

    class FakeWavePlayer:
        def __init__(self, channels=1, samplesPerSec=22050, bitsPerSample=16, purpose="SPEECH"):
            self.channels = channels
            self.samplesPerSec = samplesPerSec
            self.bitsPerSample = bitsPerSample
            self._purpose = SimpleNamespace(name=purpose)
            self._audioDucker = None
            self._enableTrimmingLeadingSilence = False
            self._isLeadingSilenceInserted = False
            self.calls = []
            self.volumes = []

        def feed(self, data, size=None, onDone=None):
            self.calls.append(("feed", speechrouter.as_bytes(data, size), onDone))

        def stop(self):
            self.calls.append(("stop",))

        def sync(self):
            self.calls.append(("sync",))

        def idle(self):
            self.calls.append(("idle",))

        def pause(self, switch):
            self.calls.append(("pause", switch))

        def setVolume(self, *, all=None, left=None, right=None):
            self.volumes.append((left, right))

    return FakeWavePlayer


class FakeStream:
    def __init__(self, rate):
        self.rate = rate
        self.calls = []
        self.positions = []
        self.closed = False

    def feed(self, data, on_done=None):
        self.calls.append(("feed", data, on_done))

    def stop(self):
        self.calls.append(("stop",))

    def sync(self):
        self.calls.append(("sync",))

    def pause(self, switch):
        self.calls.append(("pause", switch))

    def set_position(self, position):
        self.positions.append(position)

    def close(self):
        self.closed = True


class FakeDucker:
    def __init__(self):
        self.states = []

    def enable(self):
        self.states.append(True)

    def disable(self):
        self.states.append(False)


class Rig:
    """One router over one fake class, with every collaborator recorded."""

    def __init__(self, mode="none", stream_available=True, companion_fails=False):
        self.cls = make_wave_player_class()
        self.mode = mode
        self.companions = []
        self.streams = []
        self.companion_attempts = 0
        self.stream_available = stream_available
        self.companion_fails = companion_fails
        self.router = speechrouter.SpeechRouter(
            self.cls,
            mode=lambda: self.mode,
            make_companion=self._make_companion,
            open_speech_stream=self._open_stream,
        )
        self.router.install()

    def _make_companion(self, source):
        self.companion_attempts += 1
        if self.companion_fails:
            raise OSError("no device")
        companion = self.cls(
            channels=2, samplesPerSec=source.samplesPerSec, bitsPerSample=source.bitsPerSample
        )
        self.companions.append(companion)
        return companion

    def _open_stream(self, rate):
        if not self.stream_available:
            return None
        stream = FakeStream(rate)
        self.streams.append(stream)
        return stream


@pytest.fixture
def rig():
    built = []

    def factory(**kwargs):
        made = Rig(**kwargs)
        built.append(made)
        return made

    yield factory
    for made in built:
        made.router.uninstall()


# --------------------------------------------------------------------------
# the three routes
# --------------------------------------------------------------------------


def test_none_leaves_speech_on_the_players_own_methods(rig):
    r = rig(mode="none")
    synth = r.cls()
    synth.feed(pcm(1, 2, 3))
    synth.idle()
    assert synth.calls == [("feed", pcm(1, 2, 3), None), ("idle",)]
    assert r.companions == [] and r.streams == []


def test_panning_upmixes_onto_a_companion_panned_to_the_last_arrival(rig):
    r = rig(mode="panning")
    r.router.position = LEFT
    synth = r.cls()
    done = object()
    synth.feed(pcm(100, -200), onDone=done)

    assert synth.calls == [], "the mono player must hear nothing"
    (companion,) = r.companions
    assert companion.calls == [("feed", pcm(100, 100, -200, -200), done)]
    assert companion.volumes == [spatial.pan_gains(LEFT)]


def test_hrtf_feeds_the_speech_stream_at_the_last_arrival(rig):
    r = rig(mode="hrtf")
    r.router.position = RIGHT
    synth = r.cls(samplesPerSec=24000)
    synth.feed(pcm(500))

    (stream,) = r.streams
    assert stream.rate == 24000
    assert stream.positions == [RIGHT]
    assert stream.calls == [("feed", pcm(500), None)]
    assert synth.calls == [] and r.companions == []


def test_hrtf_falls_back_to_panning_without_a_speech_stream(rig):
    """A degraded session's silent player has no stream to give."""
    r = rig(mode="hrtf", stream_available=False)
    synth = r.cls()
    synth.feed(pcm(7))
    assert len(r.companions) == 1
    assert r.companions[0].calls[0][1] == pcm(7, 7)


def test_hrtf_falls_back_to_panning_for_speech_that_is_not_16_bit(rig):
    r = rig(mode="hrtf")
    synth = r.cls(bitsPerSample=8)
    synth.feed(b"\x01\x02")
    assert r.streams == []
    assert r.companions[0].calls[0][1] == b"\x01\x01\x02\x02"


def test_a_companion_that_will_not_open_leaves_speech_unplaced_and_is_not_retried(rig):
    r = rig(mode="panning", companion_fails=True)
    synth = r.cls()
    for _ in range(3):
        synth.feed(pcm(1))
        synth.idle()
    assert r.companion_attempts == 1, "a dead device must not be reopened per utterance"
    assert [call[0] for call in synth.calls] == ["feed", "idle"] * 3


@pytest.mark.parametrize(
    "player_kwargs",
    [
        {"channels": 2},  # tones, and the router's own companions
        {"purpose": "SOUNDS"},  # wave files and NVDA's sounds
    ],
)
def test_only_mono_speech_is_ever_routed(rig, player_kwargs):
    r = rig(mode="hrtf")
    other = r.cls(**player_kwargs)
    other.feed(pcm(1, 2))
    other.stop()
    assert other.calls == [("feed", pcm(1, 2), None), ("stop",)]
    assert r.streams == [] and r.companions == []


# --------------------------------------------------------------------------
# utterance boundaries
# --------------------------------------------------------------------------


def test_an_utterance_finishes_on_the_route_it_started_on(rig):
    r = rig(mode="none")
    synth = r.cls()
    synth.feed(pcm(1))
    r.mode = "panning"  # the user changes the setting mid-sentence
    synth.feed(pcm(2))
    assert [call[1] for call in synth.calls] == [pcm(1), pcm(2)]
    assert r.companions == []

    synth.stop()  # the next utterance picks the new setting up
    synth.feed(pcm(3))
    assert r.companions[0].calls == [("feed", pcm(3, 3), None)]


def test_position_is_read_once_per_utterance(rig):
    r = rig(mode="hrtf")
    synth = r.cls()
    r.router.position = LEFT
    synth.feed(pcm(1))
    r.router.position = RIGHT  # an arrival lands while this is still speaking
    synth.feed(pcm(2))
    synth.idle()  # idle ends the utterance, like stop
    synth.feed(pcm(3))
    assert r.streams[0].positions == [LEFT, RIGHT]


def test_control_calls_follow_the_current_route(rig):
    r = rig(mode="panning")
    synth = r.cls()
    synth.feed(pcm(1))
    synth.pause(True)
    synth.pause(False)
    synth.sync()
    synth.stop()
    assert [call[0] for call in r.companions[0].calls] == ["feed", "pause", "pause", "sync", "stop"]
    assert synth.calls == []


def test_a_stop_before_any_audio_goes_to_the_player_itself(rig):
    r = rig(mode="hrtf")
    synth = r.cls()
    synth.stop()
    assert synth.calls == [("stop",)]
    assert r.streams == []


def test_routes_are_kept_across_setting_toggles(rig):
    r = rig(mode="panning")
    synth = r.cls()
    for mode in ("panning", "none", "panning"):
        r.mode = mode
        synth.feed(pcm(1))
        synth.idle()
    assert len(r.companions) == 1, "one companion per player, not per utterance"


# --------------------------------------------------------------------------
# what the HRTF route does in nvwave's place
# --------------------------------------------------------------------------


def test_hrtf_trims_leading_silence_by_nvwaves_rule(rig):
    r = rig(mode="hrtf")
    synth = r.cls()
    synth._enableTrimmingLeadingSilence = True
    threshold = speechrouter.SILENCE_THRESHOLD_16
    done = object()
    synth.feed(pcm(0, threshold, -threshold), onDone=done)  # all silence
    synth.feed(pcm(threshold, threshold + 1, 0))
    synth.feed(pcm(0, 0))  # trimming has stopped: silence inside speech stays

    calls = r.streams[0].calls
    assert calls[0] == ("feed", b"", done), "a silent chunk keeps its callback"
    assert calls[1] == ("feed", pcm(threshold + 1, 0), None)
    assert calls[2] == ("feed", pcm(0, 0), None)


def test_hrtf_keeps_silence_a_break_command_put_there(rig):
    r = rig(mode="hrtf")
    synth = r.cls()
    synth._enableTrimmingLeadingSilence = True
    synth._isLeadingSilenceInserted = True
    synth.feed(pcm(0, 0, 900))
    assert r.streams[0].calls == [("feed", pcm(0, 0, 900), None)]


def test_hrtf_asserts_the_players_own_ducking(rig):
    r = rig(mode="hrtf")
    synth = r.cls()
    synth._audioDucker = FakeDucker()
    synth.feed(pcm(1))
    synth.idle()
    synth.feed(pcm(1))
    synth.stop()
    assert synth._audioDucker.states == [True, False, True, False]


# --------------------------------------------------------------------------
# lifetime
# --------------------------------------------------------------------------


def test_uninstall_restores_the_class_and_closes_every_route(rig):
    r = rig(mode="hrtf")
    patched = {name: r.cls.__dict__[name] for name in speechrouter.PATCHED_METHODS}
    synth = r.cls()
    synth.feed(pcm(1))
    stream = r.streams[0]

    r.router.uninstall()

    assert stream.closed
    for name in speechrouter.PATCHED_METHODS:
        assert r.cls.__dict__[name] is not patched[name], f"{name} is still ours"
    synth.feed(pcm(2))
    assert synth.calls == [("feed", pcm(2), None)], "the player is its own again"


def test_uninstall_leaves_a_patch_someone_put_over_ours(rig):
    r = rig(mode="none")

    def theirs(player, data, size=None, onDone=None):
        pass

    r.cls.feed = theirs
    r.router.uninstall()
    assert r.cls.feed is theirs


def test_install_resolves_every_method_before_patching_any():
    cls = make_wave_player_class()
    del cls.pause
    router = speechrouter.SpeechRouter(
        cls, mode=lambda: "none", make_companion=None, open_speech_stream=None
    )
    with pytest.raises(AttributeError):
        router.install()
    assert cls.__dict__["feed"].__module__ == __name__, "a half-installed router patched feed"


# --------------------------------------------------------------------------
# the pure helpers
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mono", "width", "stereo"),
    [
        (b"\x01\x02", 1, b"\x01\x01\x02\x02"),
        (pcm(1, -2), 2, pcm(1, 1, -2, -2)),
        (b"\x01\x02\x03\x04\x05\x06", 3, b"\x01\x02\x03\x01\x02\x03\x04\x05\x06\x04\x05\x06"),
        (pcm(1) + b"\x09", 2, pcm(1, 1)),  # a torn frame is dropped, never shifted
        (b"", 2, b""),
    ],
)
def test_upmix_duplicates_each_frame_into_both_channels(mono, width, stereo):
    assert speechrouter.upmix_mono(mono, width) == stereo


def test_feed_arguments_arrive_as_bytes_whatever_the_synth_passed():
    raw = pcm(5, 6)
    buffer = ctypes.create_string_buffer(raw, len(raw))
    assert speechrouter.as_bytes(ctypes.c_void_p(ctypes.addressof(buffer)), len(raw)) == raw
    assert speechrouter.as_bytes(None, 0) == b""  # SAPI 5's bookmark feed
    assert speechrouter.as_bytes(raw) == raw


def test_the_silence_threshold_is_nvwaves():
    assert speechrouter.SILENCE_THRESHOLD_16 == 31  # 32767 / 2**10
    assert speechrouter.leading_silence_bytes(pcm(31, -31, 32)) == 4
    assert speechrouter.leading_silence_bytes(pcm(0, 0)) == 4
    assert speechrouter.leading_silence_bytes(pcm(-32)) == 0
