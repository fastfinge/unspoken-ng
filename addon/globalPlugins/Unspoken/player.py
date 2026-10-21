"""The Sound Player seam and both of its adapters.

The Sound Player is the only module that knows OpenAL exists. Callers above the
seam hand it a slot name and a listener-relative position and are done; nothing
comes back.

The seam (spec §4.4) is four fire-and-forget methods:

    play(slot, position)    place one voice, return in ~0.1 ms
    set_theme(sounds)       replace the sound theme's samples
    set_reverb(preset)      switch the shared reverb bus to a named preset
    close()                 stop everything and give the device back

plus one deliberate exception, for spatial speech (ADR 0008):

    open_speech_stream(rate)  a synth's speech as a streaming source, or None

It is the one method that returns something, because speech is a stream with a
synth on the other end waiting for it: `SpeechStream` keeps nvwave's blocking
`feed`/`sync` and per-chunk `onDone` contract, which no fire-and-forget command
can. It shares the device, the HRTF and the clock with the role sounds -- that
sharing is the whole reason it lives here -- but not the reverb bus: speech
plays dry, whatever the Reverb setting.

Interface facts a caller must know -- these *are* the interface:

- No method returns a value, invokes a callback, or raises. `play` is safe on
  NVDA's main thread and never blocks; `close` joins the worker thread and is
  not time-critical.
- `play` with an unknown slot, before a theme is set, or while the player is
  disconnected from its output device is a silent no-op. Nothing is queued for
  later -- a dropped role sound stays dropped.
- `position` is the addon's listener-relative unit vector (+x right, +y up,
  -z forward, distance 1). It is applied verbatim; the voice pool is created
  with `AL_SOURCE_RELATIVE` set explicitly, so no listener orientation or
  distance model ever enters the picture.
- `set_theme` and `set_reverb` affect the next voice. A reverb change also
  alters in-flight tails, which is deliberate (#25 d5). Neither interrupts a
  playing voice.
- Construction may raise. After construction the player never escalates a
  failure to the caller: it logs, and if it cannot reach a device it drops
  plays until a device event brings one back (spec §§9.2-9.4).

Threading model (three threads touch this module):

- **NVDA's main thread** calls `play`, `set_theme`, `set_reverb` and `close`.
  On the play path it does compare-on-play, picks a voice and calls
  `alSourcePlay`. It never makes an ALC call that can block: a device change is
  handed to the worker.
- **The worker thread** (one per adapter, daemon) owns everything slow:
  `alcReopenDeviceSOFT` (31-470 ms), the gain ramp that retires a stolen voice,
  and the response to device events. It waits on an `Event` and is woken; it
  never polls and never runs a retry timer -- the device event *is* the retry.
- **An OpenAL C thread** delivers `ALC_SOFT_system_events` callbacks. That
  callback is trivial and total: two attribute stores and one `Event.set()`,
  wrapped so nothing can propagate out of it. An exception on that thread kills
  NVDA.

A speech stream adds two more: the synth's own threads, which call `feed` and
may block in it exactly as they would in nvwave, and one service thread per
stream, which recycles played buffers and fires their `onDone` callbacks. Each
stream's state sits behind its own `Condition`; nothing on the role-sound play
path ever takes it.

OpenAL Soft's API is thread-safe, so the main thread and the worker can both
call into it. Voice ownership is partitioned instead of locked: the main thread
owns the active list, the worker owns voices it is retiring, and the two hand
voices over through `collections.deque` (append/popleft are atomic). The play
path takes no lock of ours; the only lock it can touch is the one inside
`Event.set()`, on the rare steal and device-change branches, held for
nanoseconds and never across an ALC call.
"""

from __future__ import annotations

import ctypes
import os
import tempfile
import threading
import time
import weakref
from collections import deque
from typing import NamedTuple, Protocol, runtime_checkable

try:  # pragma: no cover - depends on whether NVDA is hosting us
    from logHandler import log
except ImportError:  # pragma: no cover - off-NVDA (tests, tooling)
    import logging

    log = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# The seam
# --------------------------------------------------------------------------


class NoAudioEndpointError(RuntimeError):
    """Neither the configured device nor the default device would open.

    The one construction failure that means "this machine has nothing to play
    through" rather than "this build is broken". The wiring (#38) treats every
    construction failure the same way -- degraded mode -- but tests must be
    able to skip on this one and only this one (spec §11).
    """


#: Event-callback trampolines that could not be unregistered. OpenAL may still
#: hold the address, so they are kept alive for the life of the process rather
#: than freed into a callback that would land in freed memory.
_ORPHANED_EVENT_PROCS: list = []


@runtime_checkable
class SoundPlayer(Protocol):
    """Four fire-and-forget commands. See the module docstring for the contract."""

    def play(self, slot: str, position: tuple[float, float, float]) -> None: ...

    def set_theme(self, sounds: dict[str, tuple[bytes, int]]) -> None:
        """`slot -> (mono PCM frames, source_rate)`.

        **Frames are mono 16-bit little-endian PCM.** That width is part of the
        seam, not an implementation detail leaking through it: core OpenAL has
        no 24-bit buffer format, so frames of any other width are uploaded as
        noise and no error is raised. `SoundThemeLibrary.load()` conforms every
        asset to this width; the rate is whatever the file really was, and
        OpenAL resamples per source.
        """
        ...

    def set_reverb(self, preset: str) -> None:
        """One of `"none"`, `"smallRoom"`, `"mediumRoom"`, `"hall"`."""
        ...

    def close(self) -> None: ...

    def open_speech_stream(self, samples_per_sec: int) -> SpeechStream | None:
        """A streaming source for one synth's mono 16-bit speech, or None.

        None means "this player cannot carry speech" -- the silent adapter, a
        closed player, or a source OpenAL would not give us -- and the caller
        keeps speech on nvwave. A stream lives until `close`, or until the
        player that opened it closes, whichever is first.
        """
        ...


@runtime_checkable
class SpeechStream(Protocol):
    """nvwave.WavePlayer's playback contract, on the Sound Player's device.

    The methods and their blocking behaviour are nvwave's, because the synth
    on the other side was written against nvwave:

    - `feed(data, on_done)` queues mono 16-bit PCM and blocks while ~400 ms is
      already queued (NVDA's own WASAPI buffer). `on_done` fires on the
      stream's service thread once everything fed up to and including this
      chunk has played; empty `data` with an `on_done` is a marker, which is
      how SAPI 5 places its bookmarks.
    - `stop()` cuts playback, drops every pending `on_done`, and releases a
      blocked `feed` without queueing its audio.
    - `sync()` blocks until everything fed has played and its callbacks have
      run.
    - `pause(switch)` pauses or resumes.
    - `set_position(position)` places the stream, in the role sounds'
      convention (+x right, +y up, -z forward, distance 1).
    - `close()` is `stop()` plus giving the source back. Idempotent.

    None of them raise. When the device is gone, audio is dropped and its
    callbacks fire at once, so the speech manager is never left waiting for an
    index nobody can hear.
    """

    def feed(self, data: bytes, on_done=None) -> None: ...

    def stop(self) -> None: ...

    def sync(self) -> None: ...

    def pause(self, switch: bool) -> None: ...

    def set_position(self, position: tuple[float, float, float]) -> None: ...

    def close(self) -> None: ...


class SettingsProvider(Protocol):
    """What the player is allowed to know about NVDA's settings.

    The wiring (`GlobalPlugin`) injects an implementation; the player only ever
    reads these two, once each per `play`.

    - `output_device`: NVDA's `[audio] outputDevice` value, passed verbatim to
      OpenAL. `"default"` (or `None`/`""`) means the OS default device.
    - `volume`: the *effective* gain, 0.0-1.0, already folded down from
      `audio.soundVolume` / `audio.soundVolumeFollowsVoice`. The player only
      ever sees a float. It is a role-sound gain, applied per voice: speech
      sharing the device has its volume from the synth and must not be
      scaled by it.

    Both are read on NVDA's main thread inside `play`, so an implementation
    must be cheap (two dict lookups) and must not block.
    """

    @property
    def output_device(self) -> str | None: ...

    @property
    def volume(self) -> float: ...


# --------------------------------------------------------------------------
# The silent adapter
# --------------------------------------------------------------------------


class SilentSoundPlayer:
    """A Sound Player that plays nothing.

    Degraded mode (spec §9.2) and the off-NVDA wiring-test double. It is
    deliberately trivial: degradedness lives in the wiring, which decides which
    adapter to construct and holds the flag. There is no `is_silent`, and no
    fifth method.
    """

    def play(self, slot: str, position: tuple[float, float, float]) -> None:
        pass

    def set_theme(self, sounds: dict[str, tuple[bytes, int]]) -> None:
        pass

    def set_reverb(self, preset: str) -> None:
        pass

    def close(self) -> None:
        pass

    def open_speech_stream(self, samples_per_sec: int) -> None:
        """Speech stays on nvwave in a degraded session."""
        return None


# --------------------------------------------------------------------------
# OpenAL constants (verified against kcat/openal-soft: al.h, alc.h, efx.h)
# --------------------------------------------------------------------------

ALC_NO_ERROR = 0
ALC_CONNECTED = 0x313
ALC_ALL_DEVICES_SPECIFIER = 0x1013

# ALC_SOFT_HRTF
ALC_HRTF_SOFT = 0x1992
ALC_HRTF_STATUS_SOFT = 0x1993
ALC_HRTF_SPECIFIER_SOFT = 0x1995

#: `ALC_HRTF_STATUS_SOFT` values, and what each one means for a user who has
#: just reported "it does not sound spatial". The status is the only thing that
#: distinguishes a request OpenAL Soft ignored from one it could not honour, and
#: the two have different fixes.
HRTF_STATUS_NAMES = {
    0x0000: "disabled",
    0x0001: "enabled",
    # Something outranked our request. In practice that is a user-level
    # alsoft.ini setting `stereo-encoding` to something other than `hrtf`:
    # an explicit config value beats the application's ALC attribute.
    0x0002: "denied (an alsoft.ini is overriding the request)",
    0x0003: "required",
    0x0004: "enabled (headphones detected)",
    # The device is not stereo -- typically an endpoint configured as 5.1/7.1
    # in Windows' sound settings. HRTF only renders to two channels.
    0x0005: "unsupported output format (the endpoint is not stereo)",
}

# ALC_SOFT_system_events (soft_oal 1.23+)
ALC_PLAYBACK_DEVICE_SOFT = 0x19D4
ALC_EVENT_TYPE_DEFAULT_DEVICE_CHANGED_SOFT = 0x19D6
ALC_EVENT_TYPE_DEVICE_ADDED_SOFT = 0x19D7
ALC_EVENT_TYPE_DEVICE_REMOVED_SOFT = 0x19D8

AL_NO_ERROR = 0
AL_NONE = 0
AL_TRUE = 1
AL_FALSE = 0
AL_FORMAT_MONO16 = 0x1101
AL_SOURCE_RELATIVE = 0x202
AL_BUFFER = 0x1009
AL_POSITION = 0x1004
AL_GAIN = 0x100A
AL_REFERENCE_DISTANCE = 0x1020
AL_ROLLOFF_FACTOR = 0x1021
AL_SOURCE_STATE = 0x1010
AL_PLAYING = 0x1012
AL_PAUSED = 0x1013
AL_BUFFERS_QUEUED = 0x1015
AL_BUFFERS_PROCESSED = 0x1016

# EFX
AL_EFFECT_TYPE = 0x8001
AL_EFFECT_NULL = 0x0000
AL_EFFECT_REVERB = 0x0001
AL_REVERB_DIFFUSION = 0x0002
AL_REVERB_GAIN = 0x0003
AL_REVERB_GAINHF = 0x0004
AL_REVERB_DECAY_TIME = 0x0005
AL_EFFECTSLOT_EFFECT = 0x0001
AL_AUXILIARY_SEND_FILTER = 0x20006
AL_FILTER_NULL = 0x0000


# --------------------------------------------------------------------------
# Tuning constants
# --------------------------------------------------------------------------

#: Concurrent audible voices (spec §6: polyphony cap ~8).
VOICE_CAP = 8

#: Extra sources beyond the cap. A stolen voice is unusable until the worker
#: has ramped it down (~59 ms measured, see RAMP_STEPS); these sources are the
#: headroom that lets the *stealing* play take a silent source immediately
#: instead of hard-cutting one. The count is the envelope divided by the
#: shortest steal interval worth covering: four spans steals about 15 ms
#: apart, which is faster than NVDA announces. Past that it degrades to the
#: hard cut.
RAMP_HEADROOM = 4

#: Gain steps a stolen voice is walked down through, and the wait between them.
#: This is an amplitude envelope on the worker thread, not a timer: no device
#: state, retry or recovery depends on it.
#:
#: The step has to outlast a mixer update or the envelope is not one. WASAPI
#: shared mode pins a ~10 ms period (measured: ALC_REFRESH 100 Hz), so a whole
#: ramp faster than that is written and overwritten between two updates: the
#: mixer renders one gain jump and the steal clicks anyway. 8 ms per step
#: spreads five gains over 32 ms -- 3.2 update periods -- and the stop is held
#: off for a further 24 ms (2.4 periods) so the mixer really renders the
#: silence before the voice is cut. A retirement therefore occupies a source
#: for seven ticks: 56 ms nominal, 59 ms measured end to end for one voice.
RAMP_STEPS = (0.6, 0.35, 0.18, 0.07, 0.0)
RAMP_STEP_SECONDS = 0.008

#: Ticks the voice is held at zero gain before it is actually stopped, so the
#: mixer renders the silence instead of cutting in the same update as the last
#: gain write. Three 8 ms ticks (~24 ms) clear two ~10 ms periods.
RAMP_ZERO_HOLD_TICKS = 3

#: How much speech a stream holds before `feed` blocks: NVDA's own WASAPI
#: buffer (`BUFFER_MS = 400` in nvdaHelper's wasapi.cpp), so a synth sees the
#: same backpressure it was written against.
SPEECH_QUEUE_SECONDS = 0.4

#: How often a speaking stream's service thread recycles played buffers and
#: fires their callbacks. Half a WASAPI period: index callbacks land within
#: ~5 ms of the audio, well inside a synth's own index granularity. The thread
#: sleeps on its `Condition` whenever nothing is queued, so a quiet stream
#: costs nothing.
SPEECH_SERVICE_SECONDS = 0.005

#: How long `feed` and `sync` wait between rechecks while blocked. Only a
#: guard: the service thread and `stop` notify, so a wait normally ends early.
SPEECH_WAIT_SECONDS = 0.05

#: Values of NVDA's `[audio] outputDevice` that mean "the OS default device",
#: which OpenAL spells as a NULL device name.
DEFAULT_DEVICE_NAMES = frozenset({None, "", "default"})

#: Everything OpenAL Soft has to be told that no API call can tell it.
#:
#: `periods = 2` is the one latency knob (spec §3): a 22 ms buffer, the WASAPI
#: shared-mode floor.
#:
#: The other three are here because `ALSOFT_CONF` is the *highest-priority*
#: config source on Windows, which makes this file the only place the addon can
#: state its rendering requirements and not be overridden:
#:
#: - `stereo-encoding = hrtf` restates the `ALC_HRTF_SOFT` context attribute in
#:   config form. The attribute alone is not enough: an explicit user-level
#:   setting outranks the application's request, so a stale
#:   `%APPDATA%\alsoft.ini` carrying `stereo-encoding = panpot` would silently
#:   turn the addon's whole point off. (`general/hrtf` is the pre-1.23 spelling
#:   and is deprecated; soft_oal logs a warning if it sees it.)
#: - `hrtf-mode = full` runs a per-source HRIR pair instead of mixing into an
#:   ambisonic bus and decoding that once. The ambisonic modes (`ambi1`..`ambi4`,
#:   default `ambi2`) exist so a game with dozens of sources pays a fixed cost,
#:   and they buy it by blurring exactly the cues this addon sells -- elevation
#:   and front/back. We cap at `VOICE_CAP + RAMP_HEADROOM` short mono voices, so
#:   per-source is affordable and accuracy wins.
#: - `channels = stereo` keeps HRTF reachable at all: it is declined outright on
#:   a non-stereo device, so an endpoint configured as 5.1/7.1 in Windows would
#:   otherwise defeat it. This is our own device, not a shared stream, so
#:   forcing two channels costs the user's surround setup nothing.
ALSOFT_CONF_BODY = (
    "[general]\n"
    "periods = 2\n"
    "stereo-encoding = hrtf\n"
    "hrtf-mode = full\n"
    "channels = stereo\n"
)
ALSOFT_CONF_FILENAME = "unspoken-ng-alsoft.ini"


class _ReverbPreset(NamedTuple):
    """The EFX reverb parameter set behind one preset name.

    An implementation detail: preset *names* are the whole user-visible
    contract (spec §8), so these values are free to be re-tuned -- except
    `smallRoom`, which reproduces today's shipped sound.
    """

    decay_time: float
    gain: float
    gain_hf: float
    diffusion: float


#: `smallRoom` is today's shipped 1.x default, so existing users hear no
#: change. Derived from the old config defaults through the old
#: `set_reverb_settings` mapping: RoomSize 10 -> decay 0.1 + 0.10*3.9 = 0.49;
#: Damping 100 -> gainhf 1.0 - 1.0*0.9 = 0.1; WetLevel 9 -> gain 0.09*0.5 =
#: 0.045; Width 100 -> diffusion 1.0. (The old DryLevel 30 was a *source* gain,
#: not an EFX parameter; loudness now belongs to the settings provider's volume
#: and the per-theme RMS gain of spec §7, so it is not part of a preset --
#: otherwise switching preset would change how loud the addon is.)
REVERB_PRESETS: dict[str, _ReverbPreset | None] = {
    "none": None,
    "smallRoom": _ReverbPreset(decay_time=0.49, gain=0.045, gain_hf=0.10, diffusion=1.0),
    "mediumRoom": _ReverbPreset(decay_time=1.10, gain=0.080, gain_hf=0.25, diffusion=1.0),
    "hall": _ReverbPreset(decay_time=2.20, gain=0.130, gain_hf=0.45, diffusion=1.0),
}


# --------------------------------------------------------------------------
# ctypes plumbing
# --------------------------------------------------------------------------

_ALC_EVENT_PROC = ctypes.CFUNCTYPE(
    None,  # void
    ctypes.c_int,  # ALCenum eventType
    ctypes.c_int,  # ALCenum deviceType
    ctypes.c_void_p,  # ALCdevice *device
    ctypes.c_int,  # ALCsizei length
    # const ALCchar *message -- deliberately NOT c_char_p. ctypes would strlen
    # past the API's own `length` and heap-allocate a bytes object on the
    # OpenAL C thread before the callback's guard can run. We never read it.
    ctypes.c_void_p,
    ctypes.c_void_p,  # void *userParam
)
_ALC_EVENT_CONTROL_PROC = ctypes.CFUNCTYPE(
    ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_int), ctypes.c_int
)
_ALC_EVENT_CALLBACK_PROC = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p)
_ALC_REOPEN_PROC = ctypes.CFUNCTYPE(
    ctypes.c_int, ctypes.c_void_p, ctypes.c_char_p, ctypes.POINTER(ctypes.c_int)
)

_EVENT_TYPES = (ctypes.c_int * 3)(
    ALC_EVENT_TYPE_DEFAULT_DEVICE_CHANGED_SOFT,
    ALC_EVENT_TYPE_DEVICE_ADDED_SOFT,
    ALC_EVENT_TYPE_DEVICE_REMOVED_SOFT,
)


def _bind(dll: ctypes.CDLL) -> ctypes.CDLL:
    """Declare argtypes/restypes for every symbol the adapter calls.

    Pointer-returning calls *must* be declared: without a restype, ctypes
    truncates the pointer to a 32-bit int on x64 and the device handle is
    silently garbage.
    """
    dll.alcOpenDevice.argtypes = [ctypes.c_char_p]
    dll.alcOpenDevice.restype = ctypes.c_void_p
    dll.alcCloseDevice.argtypes = [ctypes.c_void_p]
    dll.alcCloseDevice.restype = ctypes.c_int
    dll.alcCreateContext.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]
    dll.alcCreateContext.restype = ctypes.c_void_p
    dll.alcDestroyContext.argtypes = [ctypes.c_void_p]
    dll.alcDestroyContext.restype = None
    dll.alcMakeContextCurrent.argtypes = [ctypes.c_void_p]
    dll.alcMakeContextCurrent.restype = ctypes.c_int
    dll.alcGetString.argtypes = [ctypes.c_void_p, ctypes.c_int]
    dll.alcGetString.restype = ctypes.c_void_p
    dll.alcGetIntegerv.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.POINTER(ctypes.c_int),
    ]
    dll.alcGetIntegerv.restype = None
    dll.alcGetProcAddress.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    dll.alcGetProcAddress.restype = ctypes.c_void_p
    dll.alcGetError.argtypes = [ctypes.c_void_p]
    dll.alcGetError.restype = ctypes.c_int

    dll.alGenSources.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_uint)]
    dll.alGenSources.restype = None
    dll.alDeleteSources.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_uint)]
    dll.alDeleteSources.restype = None
    dll.alGenBuffers.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_uint)]
    dll.alGenBuffers.restype = None
    dll.alDeleteBuffers.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_uint)]
    dll.alDeleteBuffers.restype = None
    dll.alBufferData.argtypes = [
        ctypes.c_uint,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_int,
    ]
    dll.alBufferData.restype = None
    dll.alSourcei.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_int]
    dll.alSourcei.restype = None
    dll.alSourcef.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_float]
    dll.alSourcef.restype = None
    dll.alSource3f.argtypes = [
        ctypes.c_uint,
        ctypes.c_int,
        ctypes.c_float,
        ctypes.c_float,
        ctypes.c_float,
    ]
    dll.alSource3f.restype = None
    dll.alSource3i.argtypes = [
        ctypes.c_uint,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
    ]
    dll.alSource3i.restype = None
    dll.alSourcePlay.argtypes = [ctypes.c_uint]
    dll.alSourcePlay.restype = None
    dll.alSourceStop.argtypes = [ctypes.c_uint]
    dll.alSourceStop.restype = None
    dll.alSourcePause.argtypes = [ctypes.c_uint]
    dll.alSourcePause.restype = None
    dll.alSourceQueueBuffers.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.POINTER(ctypes.c_uint)]
    dll.alSourceQueueBuffers.restype = None
    dll.alSourceUnqueueBuffers.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.POINTER(ctypes.c_uint)]
    dll.alSourceUnqueueBuffers.restype = None
    dll.alGetSourcei.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
    dll.alGetSourcei.restype = None
    dll.alListenerf.argtypes = [ctypes.c_int, ctypes.c_float]
    dll.alListenerf.restype = None
    dll.alGetError.argtypes = []
    dll.alGetError.restype = ctypes.c_int

    dll.alGenEffects.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_uint)]
    dll.alGenEffects.restype = None
    dll.alDeleteEffects.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_uint)]
    dll.alDeleteEffects.restype = None
    dll.alEffecti.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_int]
    dll.alEffecti.restype = None
    dll.alEffectf.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_float]
    dll.alEffectf.restype = None
    dll.alGenAuxiliaryEffectSlots.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_uint)]
    dll.alGenAuxiliaryEffectSlots.restype = None
    dll.alDeleteAuxiliaryEffectSlots.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_uint)]
    dll.alDeleteAuxiliaryEffectSlots.restype = None
    dll.alAuxiliaryEffectSloti.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_int]
    dll.alAuxiliaryEffectSloti.restype = None
    return dll


def _device_name_arg(configured: str | None) -> bytes | None:
    """NVDA's stored device value as `alcOpenDevice` wants it (`None` = default)."""
    if configured in DEFAULT_DEVICE_NAMES:
        return None
    return str(configured).encode("utf-8")


def _write_alsoft_conf() -> str | None:
    """Step 1 of construction: point OpenAL Soft at our minimal config.

    Set and leave, no restore (spec §9.5). OpenAL Soft reads `ALSOFT_CONF`
    lazily on the first ALC call, so a "restore afterwards" mechanism would
    silently hand back the 32 ms buffer and cost 14 ms of a 52 ms budget. If
    the variable was already set we log the prior value and overwrite -- that
    log line is the tripwire that would justify building a restore mechanism.

    Returns the config path, or None if it could not be written (in which case
    OpenAL Soft uses its own default buffering: audible, not fatal).
    """
    path = os.path.join(tempfile.gettempdir(), ALSOFT_CONF_FILENAME)
    try:
        with open(path, "w", encoding="utf-8", newline="\n") as config_file:
            config_file.write(ALSOFT_CONF_BODY)
    except OSError as error:
        log.warning(f"Unspoken: could not write {path!r} ({error}); OpenAL Soft keeps its own buffering")
        return None
    previous = os.environ.get("ALSOFT_CONF")
    if previous is not None and previous != path:
        log.warning(
            f"Unspoken: ALSOFT_CONF was already set to {previous!r}; overwriting with {path!r}. "
            "Another OpenAL Soft user in this process has just lost its configuration."
        )
    os.environ["ALSOFT_CONF"] = path
    return path


# --------------------------------------------------------------------------
# The OpenAL adapter
# --------------------------------------------------------------------------


class OpenALSoundPlayer:
    """The Sound Player, on an OpenAL Soft device this process owns (ADR 0001).

    Construction order is load-bearing (spec §4.4) and is the order of the
    numbered blocks in `__init__`.
    """

    def __init__(self, settings: SettingsProvider, dll_path: str | None = None) -> None:
        self._settings = settings

        # One boolean the play path drops behind, ahead of any other work.
        self._playable = False
        self._closed = False
        self._disconnected = False
        self._reopening = False
        #: True when a named device was asked for and the default answered.
        self._on_fallback = False

        self._buffers: dict[str, int] = {}
        self._retired_buffers: list[int] = []
        self._voices: tuple[int, ...] = ()
        self._idle: deque[int] = deque()
        self._active: deque[int] = deque()
        self._retiring: deque[int] = deque()
        #: The gain each voice was started at, so a steal ramps down from the
        #: level it was actually playing at. Written by play, read by the
        #: worker when it admits a retiring voice; distinct keys, no lock.
        self._voice_gains: dict[int, float] = {}
        self._state_scratch = ctypes.c_int(0)
        self._speech_streams: weakref.WeakSet[OpenALSpeechStream] = weakref.WeakSet()
        self._speech_streams_lock = threading.Lock()

        self._device = None
        self._context = None
        self._effect = ctypes.c_uint(0)
        self._effect_slot = ctypes.c_uint(0)
        self._reverb_preset = "smallRoom"

        self._wake = threading.Event()
        self._worker: threading.Thread | None = None
        self._stopping = False
        self._reopen_pending = False
        self._default_device_changed = False
        self._device_list_changed = False
        self._logged: set[str] = set()

        self._event_proc = None  # strong ref; the C callback lives as long as we do
        self._events_registered = False
        self._alc_reopen = None
        self._alc_event_control = None
        self._alc_event_callback = None

        # 1. ALSOFT_CONF, before anything can make an ALC call.
        self._alsoft_conf_path = _write_alsoft_conf()

        # 2. The bundled x64 soft_oal.dll.
        if dll_path is None:
            dll_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "soft_oal.dll")
        try:
            self._al = _bind(ctypes.CDLL(dll_path))
        except OSError as error:
            raise RuntimeError(f"Unspoken: cannot load OpenAL Soft from {dll_path!r}: {error}") from error

        # 3. The device the settings provider asks for, else the default.
        self._requested_device = self._read_configured_device()
        self._open_device(self._requested_device)

        try:
            # 4. Context, voice pool, reverb bus, worker thread.
            self._create_context()
            self._create_voice_pool()
            self._create_reverb_bus()
            self._bind_reopen()
            # Unity, and left there: the listener gain scales every source in
            # the context, speech included, and speech volume is the synth's.
            # Role-sound volume is a per-voice gain written by play (ADR 0008).
            self._al.alListenerf(AL_GAIN, ctypes.c_float(1.0))
            self._check_al("initial setup")
            self._worker = threading.Thread(
                target=self._run_worker, name="unspoken-sound-player", daemon=True
            )
            self._worker.start()

            # 5. Device events, last: the callback must not fire into a
            #    half-built adapter.
            self._register_system_events()
        except Exception:
            self.close()
            raise

        # Never a raw store: a device event can land in the construction window
        # and the worker's verdict has to win.
        self._update_playable()
        log.debug(
            f"Unspoken Sound Player ready on {self._device_description()!r} "
            f"({len(self._voices)} sources, cap {VOICE_CAP}, ALSOFT_CONF={self._alsoft_conf_path!r})"
        )

    # -- construction helpers ------------------------------------------------

    def _read_configured_device(self) -> str | None:
        try:
            return self._settings.output_device
        except Exception as error:
            log.error(f"Unspoken: settings provider could not report the output device: {error}")
            return None

    def _open_device(self, configured: str | None) -> None:
        """Open the configured device; on NULL retry once against the default.

        NVDA's own routing falls back silently, so we log rather than announce
        (spec §9.4). Both NULL is a construction failure.
        """
        name = _device_name_arg(configured)
        device = self._al.alcOpenDevice(name)
        if not device and name is not None:
            log.info(
                f"Unspoken: output device {configured!r} did not open; falling back to the default device"
            )
            device = self._al.alcOpenDevice(None)
            self._on_fallback = bool(device)
        if not device:
            raise NoAudioEndpointError(
                "Unspoken: alcOpenDevice returned NULL for both the configured and default device"
            )
        self._device = device

    def _create_context(self) -> None:
        # HRTF is the point of the exercise; ask for it explicitly and keep the
        # attribute list for alcReopenDeviceSOFT, which takes the same one.
        self._attributes = (ctypes.c_int * 3)(ALC_HRTF_SOFT, 1, 0)
        context = self._al.alcCreateContext(self._device, self._attributes)
        if not context:
            raise RuntimeError("Unspoken: alcCreateContext returned NULL")
        self._context = context
        if not self._al.alcMakeContextCurrent(context):
            raise RuntimeError("Unspoken: alcMakeContextCurrent failed")
        self._log_hrtf_state()

    def _log_hrtf_state(self) -> None:
        """Record whether HRTF is rendering and, when it is not, why.

        Diagnostics only, and total: HRTF being off makes the addon worse, not
        broken, so nothing here may fail construction or interrupt the worker's
        device servicing. It runs after every successful reopen as well as at
        construction, because the answer changes with the device -- plugging in
        headphones is the case the user is most likely to ask about.

        `ALC_HRTF_SOFT` is a bare boolean, which was enough to say "not active"
        and nothing a user could act on. `ALC_HRTF_STATUS_SOFT` names the cause,
        and the causes have different fixes (see `HRTF_STATUS_NAMES`).
        """
        try:
            enabled = ctypes.c_int(0)
            status = ctypes.c_int(0)
            self._al.alcGetIntegerv(self._device, ALC_HRTF_SOFT, 1, ctypes.byref(enabled))
            self._al.alcGetIntegerv(self._device, ALC_HRTF_STATUS_SOFT, 1, ctypes.byref(status))
            reason = HRTF_STATUS_NAMES.get(status.value, f"unknown status {status.value:#x}")
            if enabled.value:
                # The specifier names the dataset that actually loaded, which is
                # what distinguishes "HRTF on" from "HRTF on, using the built-in
                # table because a configured one was unreadable".
                dataset = self._alc_string(ALC_HRTF_SPECIFIER_SOFT)
                log.debug(f"Unspoken: HRTF active -- {reason}, dataset {dataset!r}")
            else:
                log.warning(
                    f"Unspoken: HRTF is not active on this device ({reason}); "
                    "role sounds will only be panned"
                )
        except Exception as error:
            self._log_once("hrtf_state", f"Unspoken: could not read the HRTF state: {error}")
        finally:
            # These queries are the only ALC ones whose enum an older soft_oal
            # could reject. ALC errors are sticky per device, so clear rather
            # than leave one for an unrelated reader to find.
            try:
                self._al.alcGetError(self._device)
            except Exception:
                pass

    def _create_voice_pool(self) -> None:
        count = VOICE_CAP + RAMP_HEADROOM
        sources = (ctypes.c_uint * count)()
        self._al.alGenSources(count, sources)
        self._check_al("alGenSources")
        if not all(sources):
            raise RuntimeError("Unspoken: could not create the voice pool")
        self._voices = tuple(int(source) for source in sources)
        for voice in self._voices:
            # Positions cross the seam already listener-relative and on the
            # unit sphere, so no distance model may touch them.
            self._al.alSourcei(voice, AL_SOURCE_RELATIVE, AL_TRUE)
            self._al.alSourcef(voice, AL_REFERENCE_DISTANCE, ctypes.c_float(1.0))
            self._al.alSourcef(voice, AL_ROLLOFF_FACTOR, ctypes.c_float(0.0))
            self._al.alSourcef(voice, AL_GAIN, ctypes.c_float(1.0))
            self._idle.append(voice)
        self._check_al("voice pool setup")

    def _create_reverb_bus(self) -> None:
        self._al.alGenEffects(1, ctypes.byref(self._effect))
        self._check_al("alGenEffects")
        self._al.alEffecti(self._effect.value, AL_EFFECT_TYPE, AL_EFFECT_REVERB)
        self._check_al("alEffecti(AL_EFFECT_REVERB)")
        self._al.alGenAuxiliaryEffectSlots(1, ctypes.byref(self._effect_slot))
        self._check_al("alGenAuxiliaryEffectSlots")
        # One shared bus for every voice: a stolen voice's tail is already in
        # the bus and keeps ringing (spec §6.3 -- the no-cut-off-tails
        # mechanism).
        for voice in self._voices:
            self._al.alSource3i(
                voice, AL_AUXILIARY_SEND_FILTER, self._effect_slot.value, 0, AL_FILTER_NULL
            )
        self._check_al("auxiliary send wiring")
        self._apply_reverb(self._reverb_preset)

    def _bind_reopen(self) -> None:
        """`alcReopenDeviceSOFT` (soft_oal 1.22+), the non-destructive device switch."""
        reopen = self._al.alcGetProcAddress(None, b"alcReopenDeviceSOFT")
        if reopen:
            self._alc_reopen = _ALC_REOPEN_PROC(reopen)
        else:
            log.warning(
                "Unspoken: alcReopenDeviceSOFT is missing (soft_oal 1.22+ expected); "
                "the Sound Player cannot follow device changes this session"
            )

    def _register_system_events(self) -> None:
        """Subscribe to `ALC_SOFT_system_events` (soft_oal 1.23+).

        Optional: without it, config changes still arrive through
        compare-on-play; only the eager default-device follow and the
        disconnect notice are lost.
        """
        control = self._al.alcGetProcAddress(None, b"alcEventControlSOFT")
        callback = self._al.alcGetProcAddress(None, b"alcEventCallbackSOFT")
        if not (control and callback):
            log.warning(
                "Unspoken: ALC_SOFT_system_events is missing (soft_oal 1.23+ expected); "
                "device changes will only be noticed when the configured device changes"
            )
            return
        self._alc_event_control = _ALC_EVENT_CONTROL_PROC(control)
        self._alc_event_callback = _ALC_EVENT_CALLBACK_PROC(callback)
        # Strong reference for the adapter's lifetime: if this is collected
        # while OpenAL still holds the pointer, the next device event calls
        # into freed memory and takes NVDA with it.
        self._event_proc = _ALC_EVENT_PROC(self._on_alc_event)
        self._alc_event_callback(ctypes.cast(self._event_proc, ctypes.c_void_p), None)
        # OpenAL holds the trampoline's address from this line onwards, so
        # close() must unregister from this line onwards: the flag is set
        # before the enable call, not after it.
        self._events_registered = True
        self._alc_event_control(len(_EVENT_TYPES), _EVENT_TYPES, AL_TRUE)

    # -- the play path (NVDA's main thread) ---------------------------------

    def play(self, slot: str, position: tuple[float, float, float]) -> None:
        """Place one voice. Fire-and-forget; returns in ~0.1 ms."""
        if not self._playable:
            # Disconnected, closed, or a reopen is in flight: drop, behind one
            # boolean, ahead of any other work. Nothing is queued to burst
            # later (#18's lesson).
            return
        try:
            buffer_id = self._buffers.get(slot)
            if buffer_id is None:
                return

            # Two reads from the settings provider, ahead of alSourcePlay: the
            # volume this voice starts at, and the device compared below.
            volume = float(self._settings.volume)
            device = self._settings.output_device
            if device != self._requested_device:
                # Compare against what was last *requested*, and update it even
                # though the reopen may fail -- otherwise a dead configured
                # endpoint means a 31-470 ms reopen on every sound (#26 d7).
                self._requested_device = device
                self._reopen_pending = True
                self._wake.set()

            voice = self._acquire_voice()
            if voice is None:
                return
            # From here the voice belongs to no queue, so every path out of
            # this block has to put it in one. A voice dropped here is gone for
            # the session, and enough of them starve the pool into permanent
            # hard cuts behind a single log line.
            placed = False
            try:
                al = self._al
                al.alSourcei(voice, AL_BUFFER, buffer_id)
                al.alSource3f(
                    voice,
                    AL_POSITION,
                    ctypes.c_float(position[0]),
                    ctypes.c_float(position[1]),
                    ctypes.c_float(position[2]),
                )
                # The role-sound volume, per voice rather than on the listener
                # so speech sharing the context is not scaled by it. The same
                # write resets a voice that came back from a steal with a
                # ramped-down gain.
                self._voice_gains[voice] = volume
                al.alSourcef(voice, AL_GAIN, ctypes.c_float(volume))
                al.alSourcePlay(voice)
                self._active.append(voice)
                placed = True
            finally:
                if not placed:
                    try:
                        self._al.alSourceStop(voice)
                    except Exception:
                        pass
                    self._idle.append(voice)
        except Exception as error:  # the play path never escalates
            self._log_once("play", f"Unspoken: play({slot!r}) failed: {error}")

    def _acquire_voice(self) -> int | None:
        """Pick the source this play will use. Main thread only, never blocks."""
        active = self._active
        if len(active) >= VOICE_CAP:
            self._reclaim_finished()
        if self._idle:
            # Exactly one voice is evicted per play, and only when there is a
            # silent source to hand out in its place: retiring the oldest
            # before knowing that would cost a second voice on the hard-cut
            # path below and leave polyphony under the cap for the rest of a
            # burst.
            if len(active) >= VOICE_CAP:
                self._retiring.append(active.popleft())
                self._wake.set()
            return self._idle.popleft()
        # No silent source left: more steals landed inside one ramp window than
        # the headroom covers. Cutting the oldest voice risks a click; dropping
        # the announcement's sound is worse, so cut.
        if not active:
            self._log_once(
                "starved",
                "Unspoken: every voice is mid-retirement; dropping a role sound",
                level="warning",
            )
            return None
        voice = active.popleft()
        self._al.alSourceStop(voice)
        self._log_once(
            "hardsteal",
            "Unspoken: voice stolen without a ramp; a click is possible",
            level="debug",
        )
        return voice

    def _reclaim_finished(self) -> None:
        """Return voices that have finished to the idle pool, preserving age order."""
        active = self._active
        state = self._state_scratch
        for _ in range(len(active)):
            voice = active.popleft()
            self._al.alGetSourcei(voice, AL_SOURCE_STATE, ctypes.byref(state))
            if state.value == AL_PLAYING:
                active.append(voice)
            else:
                self._idle.append(voice)

    # -- theme and reverb (NVDA's main thread) ------------------------------

    def set_theme(self, sounds: dict[str, tuple[bytes, int]]) -> None:
        """Replace the sound theme's samples. Affects the next voice only.

        Each slot is uploaded at its own true source rate -- OpenAL resamples
        per source on its own thread, which is why no resampling happens in
        Python (spec §4.3) and why a 48 kHz sample can no longer play flat
        (#23). New buffers are generated rather than overwritten, so playing
        voices are untouched.
        """
        if self._closed:
            return
        fresh: dict[str, int] = {}
        published = False
        try:
            # alGetError is per-context state the worker's AL calls also write,
            # so a code read here can belong to a ramp rather than to the call
            # above it. Both readers are benign under that: a slot can be
            # dropped or a dead buffer kept for the next attempt, and neither
            # touches a playing voice.
            self._al.alGetError()  # clear
            for slot, sample in sounds.items():
                frames, source_rate = sample
                size = (len(frames) // 2) * 2  # whole 16-bit mono frames only
                if size <= 0 or source_rate <= 0:
                    log.info(f"Unspoken: slot {slot!r} has no usable samples; it will play nothing")
                    continue
                buffer_id = ctypes.c_uint(0)
                self._al.alGenBuffers(1, ctypes.byref(buffer_id))
                self._al.alBufferData(
                    buffer_id.value, AL_FORMAT_MONO16, frames, size, int(source_rate)
                )
                error = self._al.alGetError()
                if error != AL_NO_ERROR:
                    log.warning(f"Unspoken: slot {slot!r} rejected by alBufferData ({error:#x})")
                    self._al.alDeleteBuffers(1, ctypes.byref(buffer_id))
                    self._al.alGetError()
                    continue
                fresh[slot] = buffer_id.value

            previous = self._buffers
            self._buffers = fresh  # one atomic rebind; play reads the new dict next call
            published = True
            # Idle voices still reference the old buffers; detaching lets the
            # old set be deleted. Voices still sounding keep theirs until they
            # are reused, so a buffer that refuses to die is retried after the
            # next theme change and freed at close().
            for voice in tuple(self._idle):
                self._al.alSourcei(voice, AL_BUFFER, AL_NONE)
            self._al.alGetError()
            still_held = self._delete_buffers(self._retired_buffers)
            self._retired_buffers = still_held + list(previous.values())
            log.debug(f"Unspoken: sound theme set, {len(fresh)} slots")
        except Exception as error:
            if not published:
                # Nothing plays these yet, so they are ours to free; leaving
                # them would leak a whole theme's samples per failed attempt.
                self._delete_buffers(list(fresh.values()))
            self._log_once("set_theme", f"Unspoken: set_theme failed: {error}")

    def set_reverb(self, preset: str) -> None:
        """Switch the shared reverb bus. Applies immediately, tails included."""
        if self._closed:
            return
        try:
            self._apply_reverb(preset)
        except Exception as error:
            self._log_once("set_reverb", f"Unspoken: set_reverb({preset!r}) failed: {error}")

    def _apply_reverb(self, preset: str) -> None:
        if preset not in REVERB_PRESETS:
            log.warning(f"Unspoken: unknown reverb preset {preset!r}; keeping {self._reverb_preset!r}")
            return
        self._reverb_preset = preset
        values = REVERB_PRESETS[preset]
        if values is None:
            # "none" runs the bus dry by unloading the effect: the send wiring
            # stays, so switching back needs no per-voice work.
            self._al.alAuxiliaryEffectSloti(
                self._effect_slot.value, AL_EFFECTSLOT_EFFECT, AL_EFFECT_NULL
            )
            self._check_al("reverb none")
            return
        effect = self._effect.value
        self._al.alEffecti(effect, AL_EFFECT_TYPE, AL_EFFECT_REVERB)
        self._al.alEffectf(effect, AL_REVERB_DECAY_TIME, ctypes.c_float(values.decay_time))
        self._al.alEffectf(effect, AL_REVERB_GAIN, ctypes.c_float(values.gain))
        self._al.alEffectf(effect, AL_REVERB_GAINHF, ctypes.c_float(values.gain_hf))
        self._al.alEffectf(effect, AL_REVERB_DIFFUSION, ctypes.c_float(values.diffusion))
        # The slot only picks up parameter changes when the effect is
        # re-attached.
        self._al.alAuxiliaryEffectSloti(self._effect_slot.value, AL_EFFECTSLOT_EFFECT, effect)
        self._check_al(f"reverb {preset}")

    # -- speech (any thread) -------------------------------------------------

    def open_speech_stream(self, samples_per_sec: int) -> OpenALSpeechStream | None:
        """A streaming source for one synth's mono 16-bit speech (ADR 0008).

        Called once per synth wave player, at the first utterance routed here,
        on whatever thread that synth feeds from. Never raises: a stream that
        cannot be built is None, and speech stays on nvwave.
        """
        if self._closed or samples_per_sec <= 0:
            return None
        try:
            stream = OpenALSpeechStream(self, int(samples_per_sec))
        except Exception as error:
            self._log_once("speech_stream", f"Unspoken: could not open a speech stream: {error}")
            return None
        with self._speech_streams_lock:
            if self._closed:
                # close() ran while the stream was being built; it never saw it.
                stream.close()
                return None
            self._speech_streams.add(stream)
        return stream

    # -- the OpenAL C thread ------------------------------------------------

    def _on_alc_event(self, event_type, device_type, device, length, message, user_param) -> None:
        """ALC_SOFT_system_events callback. Runs on an OpenAL C thread.

        Trivial and total: two attribute stores and a signal, nothing that
        allocates, logs, or calls back into OpenAL, and nothing that can
        propagate. An exception raised here kills NVDA.
        """
        try:
            if device_type == ALC_PLAYBACK_DEVICE_SOFT:
                if event_type == ALC_EVENT_TYPE_DEFAULT_DEVICE_CHANGED_SOFT:
                    self._default_device_changed = True
                else:
                    self._device_list_changed = True
                self._wake.set()
        except BaseException:  # noqa: BLE001 - nothing may leave this frame
            pass

    # -- the worker thread --------------------------------------------------

    def _run_worker(self) -> None:
        """Everything slow: device reopens and the voice-steal gain ramp.

        Woken by an Event; never polls, never runs a retry timer. A device
        event is the only retry there is (spec §9.3).
        """
        while True:
            self._wake.wait()
            self._wake.clear()
            try:
                if self._stopping:
                    # Let stolen voices land back in the pool before close()
                    # takes the sources apart. Inside the guard like everything
                    # else: a raise here would kill the thread silently, after
                    # close() has already returned.
                    self._run_ramps()
                    return
                self._service_devices()
                self._run_ramps()
            except Exception as error:  # a dead worker is a dead player
                self._log_once("worker", f"Unspoken: Sound Player worker error: {error}")
                if self._stopping:
                    return

    def _run_ramps(self) -> None:
        """Fade every stolen voice out, all of them at once.

        The envelopes are interleaved rather than run back to back: one worker
        walking one voice at a time would retire a voice per envelope (~59 ms),
        which is slower than fast navigation steals them, and the pool would
        starve into hard cuts no matter how much headroom it had. Interleaved,
        a voice comes back every tick once the pipeline is full, and the whole
        thing still costs one sleep per tick.

        Ramps yield to device work. Sustained stealing produces a steal every
        few ticks, so a loop that kept admitting new voices would never end and
        the worker would never reach `_service_devices` -- "the device event is
        the retry" would quietly become "the retry happens once the user stops
        navigating". Once an event is waiting, no new voice is admitted, the
        envelopes already in flight are allowed to finish, and the worker is
        handed back. Servicing devices from inside the tick loop would be
        worse: a 31-470 ms reopen would freeze those envelopes at an audible
        gain, which is the artefact this design exists to avoid.
        """
        fading: list[list] = []  # [voice, tick, gain the voice started at]
        # Ticks 0..len(RAMP_STEPS)-1 write a gain (the last of them zero), then
        # RAMP_ZERO_HOLD_TICKS pass at zero before the voice is stopped.
        last_tick = len(RAMP_STEPS) - 1 + RAMP_ZERO_HOLD_TICKS
        while True:
            if self._pending_device_work():
                if self._retiring:
                    # Re-arm: what is still queued must not sit there until
                    # something else happens to wake the worker.
                    self._wake.set()
            else:
                while self._retiring:
                    try:
                        voice = self._retiring.popleft()
                    except IndexError:  # pragma: no cover - nobody else pops it
                        break
                    # The steps are fractions of the level the voice is at:
                    # an absolute 0.6 would jump a quiet voice *up* first.
                    fading.append([voice, 0, self._voice_gains.get(voice, 1.0)])
            if not fading:
                return
            still_fading = []
            for entry in fading:
                voice, tick, start_gain = entry
                entry[1] = tick + 1
                try:
                    if tick < len(RAMP_STEPS):
                        self._al.alSourcef(
                            voice, AL_GAIN, ctypes.c_float(RAMP_STEPS[tick] * start_gain)
                        )
                        still_fading.append(entry)
                        continue
                    if tick < last_tick:  # sit at zero while the mixer catches up
                        still_fading.append(entry)
                        continue
                    self._al.alSourceStop(voice)
                    self._al.alSourcei(voice, AL_BUFFER, AL_NONE)
                except Exception as error:
                    self._log_once("ramp", f"Unspoken: retiring a voice failed: {error}")
                    # A voice returned to the pool still sounding would play
                    # over the next role sound at whatever gain it had, so try
                    # to silence it even now -- but never at the cost of the
                    # append below, which is what keeps the pool whole.
                    try:
                        self._al.alSourceStop(voice)
                    except Exception:
                        pass
                    try:
                        self._al.alSourcei(voice, AL_BUFFER, AL_NONE)
                    except Exception:
                        pass
                # However it ended, the voice goes back in the pool.
                self._idle.append(voice)
            fading = still_fading
            if fading:
                time.sleep(RAMP_STEP_SECONDS)

    def _pending_device_work(self) -> bool:
        return self._reopen_pending or self._default_device_changed or self._device_list_changed

    def _service_devices(self) -> None:
        while self._pending_device_work():
            # The memo is otherwise only refreshed by play(), which returns
            # before compare-on-play while the player is disconnected -- so a
            # user picking a working device in NVDA's settings would never be
            # seen. Reading it here means every event acts on the configuration
            # the user has now, without adding anything to the play path.
            self._refresh_requested_device()
            if self._reopen_pending:
                # The user changed NVDA's device: lazy, noticed by
                # compare-on-play, executed here because alcReopenDeviceSOFT
                # blocks for 31-470 ms.
                self._reopen_pending = False
                self._reopen(self._requested_device)
                continue
            if self._default_device_changed:
                self._default_device_changed = False
                # Eager: the user just put headphones on. Our business if we
                # follow the default -- and also if we are sitting on the
                # default because the configured device failed, or if we have
                # nothing at all, since the new default may be the way back.
                if (
                    self._requested_device in DEFAULT_DEVICE_NAMES
                    or self._on_fallback
                    or self._disconnected
                ):
                    self._reopen(self._requested_device)
                continue
            self._device_list_changed = False
            # A device came or went. Lazy: act only if we are broken, or if we
            # are sitting on the default because the configured device was
            # absent -- a returning device is exactly the event that retries
            # both cases, and it is the only retry there is.
            if self._disconnected or self._on_fallback or not self._is_connected():
                self._reopen(self._requested_device)

    def _refresh_requested_device(self) -> None:
        """Re-read the configured device on the worker thread, keeping the old value on failure."""
        try:
            configured = self._settings.output_device
        except Exception as error:
            self._log_once(
                "provider_device",
                f"Unspoken: settings provider could not report the output device: {error}",
            )
            return
        if configured != self._requested_device:
            self._requested_device = configured
            self._reopen_pending = True

    def _is_connected(self) -> bool:
        connected = ctypes.c_int(1)
        self._al.alcGetIntegerv(self._device, ALC_CONNECTED, 1, ctypes.byref(connected))
        return bool(connected.value)

    def _reopen(self, configured: str | None) -> None:
        """One named attempt, one default attempt, then disconnected.

        No timer and no backoff follows a failure: the next device event is the
        retry (spec §9.3).
        """
        if self._alc_reopen is None or self._closed:
            return
        name = _device_name_arg(configured)
        started = time.perf_counter()
        # Drop plays for the duration. OpenAL's own locking means an
        # alSourcePlay landing mid-reopen can wait on the mixer, and a main
        # thread stalled behind a 470 ms device switch is worse than the
        # already-accepted cost of losing the first sound after a device
        # change (spec §13). Same boolean, no extra check on the play path.
        self._reopening = True
        self._update_playable()
        ok = False
        try:
            ok = bool(self._alc_reopen(self._device, name, self._attributes))
            self._on_fallback = False
            if not ok and name is not None:
                log.info(
                    f"Unspoken: output device {configured!r} did not reopen; trying the default device"
                )
                ok = bool(self._alc_reopen(self._device, None, self._attributes))
                self._on_fallback = ok
            ok = ok and self._is_connected()
        except Exception as error:
            # Anything thrown here must land as "disconnected", never as a
            # player that is silent with nothing recording why: disconnected
            # is a state the next device event genuinely retries.
            ok = False
            self._log_once("reopen", f"Unspoken: reopening the output device failed: {error}")
        finally:
            # Both flags, always. Restoring _reopening without recomputing the
            # play gate would leave the player silent for the session.
            self._reopening = False
            self._update_playable()
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        if ok:
            self._mark_connected(elapsed_ms)
        else:
            self._mark_disconnected()

    def _mark_connected(self, elapsed_ms: float) -> None:
        was_disconnected = self._disconnected
        self._disconnected = False
        self._update_playable()
        # A reopen can land on a device that renders HRTF differently, or not at
        # all; the old log line only ever described the device at construction.
        self._log_hrtf_state()
        message = (
            f"Unspoken: Sound Player now on {self._device_description()!r} ({elapsed_ms:.0f} ms)"
        )
        if was_disconnected:
            self._logged.discard("disconnected")
            log.info(message + " -- role sounds are back")
        else:
            log.debug(message)

    def _mark_disconnected(self) -> None:
        """One warning on entry, never one per play (spec §9.4)."""
        self._disconnected = True
        self._update_playable()
        if "disconnected" in self._logged:
            return
        self._logged.add("disconnected")
        log.warning(
            "Unspoken: no usable output device; role sounds are dropped until a device event returns. "
            "Speech is unaffected."
        )

    def _update_playable(self) -> None:
        self._playable = not (self._disconnected or self._closed or self._reopening)

    # -- teardown -----------------------------------------------------------

    def close(self) -> None:
        """Stop the worker, unregister the C callback, hand the device back.

        Not time-critical, and idempotent. Never raises.
        """
        if self._closed:
            return
        self._closed = True
        self._update_playable()
        try:
            # Unregister before anything else: no more callbacks into an
            # adapter that is being taken apart, and none at all by the time
            # the DLL could go away. The two calls get their own try blocks --
            # clearing OpenAL's pointer is the one that actually matters, and
            # it must be attempted even if disabling the event types fails.
            if self._events_registered:
                try:
                    self._alc_event_control(len(_EVENT_TYPES), _EVENT_TYPES, AL_FALSE)
                except Exception as error:
                    log.error(f"Unspoken: could not disable device events: {error}")
                try:
                    self._alc_event_callback(None, None)
                except Exception as error:
                    # OpenAL may still hold the trampoline's address; keep it
                    # alive for the process rather than let it be collected
                    # into a callback landing in freed memory.
                    _ORPHANED_EVENT_PROCS.append(self._event_proc)
                    log.error(
                        f"Unspoken: could not clear the device-event callback ({error}); "
                        "keeping the trampoline alive for the life of the process"
                    )
                self._events_registered = False

            # Speech streams own sources in this context, so they go before
            # the context does. Closing one releases any synth blocked in it.
            with self._speech_streams_lock:
                streams = list(self._speech_streams)
                self._speech_streams.clear()
            for stream in streams:
                stream.close()

            worker = self._worker
            if worker is not None:
                self._stopping = True
                self._wake.set()
                worker.join(timeout=5.0)
                if worker.is_alive():
                    log.error("Unspoken: Sound Player worker did not stop; leaving the device open")
                    return
                self._worker = None

            for voice in self._voices:
                self._al.alSourceStop(voice)
                self._al.alSourcei(voice, AL_BUFFER, AL_NONE)
            if self._voices:
                sources = (ctypes.c_uint * len(self._voices))(*self._voices)
                self._al.alDeleteSources(len(self._voices), sources)
                self._voices = ()
            self._idle.clear()
            self._active.clear()
            self._retiring.clear()
            self._voice_gains.clear()

            if self._effect_slot.value:
                self._al.alAuxiliaryEffectSloti(
                    self._effect_slot.value, AL_EFFECTSLOT_EFFECT, AL_EFFECT_NULL
                )
                self._al.alDeleteAuxiliaryEffectSlots(1, ctypes.byref(self._effect_slot))
            if self._effect.value:
                self._al.alDeleteEffects(1, ctypes.byref(self._effect))
            self._delete_buffers(list(self._buffers.values()) + self._retired_buffers)
            self._buffers = {}
            self._retired_buffers = []
            self._al.alGetError()

            if self._context is not None:
                self._al.alcMakeContextCurrent(None)
                self._al.alcDestroyContext(self._context)
                self._context = None
            if self._device is not None:
                self._al.alcCloseDevice(self._device)
                self._device = None
            log.debug("Unspoken: Sound Player closed")
        except Exception as error:
            log.error(f"Unspoken: Sound Player close failed: {error}")

    def _delete_buffers(self, buffer_ids: list[int]) -> list[int]:
        """Delete what will go; return what a still-sounding voice holds onto."""
        if not buffer_ids:
            return []
        self._al.alGetError()
        kept = []
        for buffer_id in buffer_ids:
            handle = ctypes.c_uint(buffer_id)
            self._al.alDeleteBuffers(1, ctypes.byref(handle))
            if self._al.alGetError() != AL_NO_ERROR:
                kept.append(buffer_id)
        if kept:
            log.debug(f"Unspoken: {len(kept)} sound theme buffers still in use; freeing them later")
        return kept

    # -- odds and ends ------------------------------------------------------

    def _check_al(self, what: str) -> None:
        error = self._al.alGetError()
        if error != AL_NO_ERROR:
            log.warning(f"Unspoken: OpenAL error {error:#x} after {what}")

    def _alc_string(self, token: int) -> str | None:
        pointer = self._al.alcGetString(self._device, token)
        return ctypes.string_at(pointer).decode("utf-8", "replace") if pointer else None

    def _device_description(self) -> str | None:
        return self._alc_string(ALC_ALL_DEVICES_SPECIFIER)

    def _log_once(self, key: str, message: str, level: str = "error") -> None:
        """Log a repeatable condition once; a per-play log line is its own bug."""
        if key in self._logged:
            return
        self._logged.add(key)
        getattr(log, level)(message)


# --------------------------------------------------------------------------
# The speech stream (ADR 0008)
# --------------------------------------------------------------------------


class _Chunk:
    """One fed chunk: its buffer (None for a marker), its length, its callback."""

    __slots__ = ("buffer_id", "frames", "on_done")

    def __init__(self, buffer_id: int | None, frames: int, on_done) -> None:
        self.buffer_id = buffer_id
        self.frames = frames
        self.on_done = on_done


class OpenALSpeechStream:
    """One synth's speech as a streaming source in the Sound Player's context.

    The source is placed exactly like a role-sound voice -- listener-relative,
    no distance model -- so speech and the earcon that preceded it are
    rendered by one HRTF, on one clock. It is deliberately *dry*: no send to
    the shared reverb bus. Reverb helps an earcon's position be heard, and it
    smears speech, which has to be understood. What differs is delivery: chunks are queued as they
    arrive and recycled as they finish, and played chunks' `onDone` callbacks
    are run outside the lock by whichever thread recycled them -- the service
    thread while speech plays out, or a feeding thread, as nvwave's own `feed`
    fires pending callbacks.

    Every piece of state is behind `_cond`. OpenAL calls are made under it --
    they are short, and the order of recycle, queue and play matters -- but
    callbacks never are: a callback may call straight back into `stop`.

    The order matters because of one OpenAL rule: a source that has run dry is
    STOPPED, and a stopped source reports *every* buffer in its queue as
    processed, including any queued after it stopped. So audio is only ever
    queued onto a stopped source after everything played has been recycled,
    and the source is started in the same locked section -- otherwise the
    next recycle would throw unplayed audio away, or `alSourcePlay` would
    replay played audio from the head of the queue. While paused, such a
    source is *held*: its queue is all unplayed, and nothing is recycled from
    it until it is resumed.
    """

    def __init__(self, owner: OpenALSoundPlayer, samples_per_sec: int) -> None:
        self._owner = owner
        self._al = owner._al
        self._rate = samples_per_sec
        self._capacity_frames = int(samples_per_sec * SPEECH_QUEUE_SECONDS)
        self._cond = threading.Condition()
        self._chunks: deque[_Chunk] = deque()
        self._queued_frames = 0
        #: Callbacks recycled but not yet run. `sync` waits for these too: a
        #: synth that idles and then expects its last index to have fired must
        #: not be told the audio is done before it has.
        self._in_flight = 0
        #: Bumped by `stop`, so a `feed` or `sync` that was waiting across a
        #: stop can tell its utterance is gone.
        self._generation = 0
        self._paused = False
        #: The source is stopped and everything queued on it is unplayed.
        self._held = False
        self._closed = False
        self._scratch = ctypes.c_int(0)

        source = ctypes.c_uint(0)
        self._al.alGenSources(1, ctypes.byref(source))
        if not source.value:
            raise RuntimeError("alGenSources returned no speech source")
        self._source = source.value
        al = self._al
        al.alSourcei(self._source, AL_SOURCE_RELATIVE, AL_TRUE)
        al.alSourcef(self._source, AL_REFERENCE_DISTANCE, ctypes.c_float(1.0))
        al.alSourcef(self._source, AL_ROLLOFF_FACTOR, ctypes.c_float(0.0))
        al.alSourcef(self._source, AL_GAIN, ctypes.c_float(1.0))
        al.alSource3f(
            self._source, AL_POSITION, ctypes.c_float(0.0), ctypes.c_float(0.0), ctypes.c_float(-1.0)
        )
        # Dry, stated rather than left to the default: whatever the Reverb
        # setting, speech never reaches the bus.
        al.alSource3i(self._source, AL_AUXILIARY_SEND_FILTER, 0, 0, AL_FILTER_NULL)
        self._thread = threading.Thread(
            target=self._run, name="unspoken-speech-stream", daemon=True
        )
        self._thread.start()

    # -- nvwave's contract --------------------------------------------------

    def feed(self, data: bytes, on_done=None) -> None:
        """Queue one chunk, blocking while the queue is full. Never raises."""
        due = []
        try:
            with self._cond:
                if self._closed:
                    due = self._completed_now(on_done)
                else:
                    generation = self._generation
                    while (
                        self._queued_frames >= self._capacity_frames
                        and not self._closed
                        and generation == self._generation
                    ):
                        self._cond.wait(SPEECH_WAIT_SECONDS)
                    if self._closed:
                        due = self._completed_now(on_done)
                    elif generation == self._generation:
                        due = self._enqueue(data, on_done)
                    # Otherwise a stop landed while this feed waited: the
                    # utterance is gone, and nvwave drops its callbacks too.
        except Exception as error:
            self._owner._log_once("speech_feed", f"Unspoken: feeding speech failed: {error}")
        self._complete(due)

    def stop(self) -> None:
        """Cut playback and drop everything queued, callbacks included."""
        try:
            with self._cond:
                self._generation += 1
                self._paused = False
                if not self._closed:
                    self._flush()
                self._cond.notify_all()
        except Exception as error:
            self._owner._log_once("speech_stop", f"Unspoken: stopping speech failed: {error}")

    def sync(self) -> None:
        """Block until everything fed has played and its callbacks have run."""
        if threading.current_thread() is self._thread:
            return  # a callback asking to wait for itself
        try:
            with self._cond:
                generation = self._generation
                deadline = self._deadline()
                while (
                    (self._chunks or self._in_flight)
                    and not self._closed
                    and generation == self._generation
                ):
                    if self._paused:
                        deadline = self._deadline()
                    elif time.monotonic() >= deadline:
                        # The audio should long since have played. Something
                        # below us is wedged; a synth thread blocked forever is
                        # worse than an early return.
                        self._owner._log_once(
                            "speech_sync", "Unspoken: speech did not drain in time; stopped waiting"
                        )
                        return
                    self._cond.wait(SPEECH_WAIT_SECONDS)
        except Exception as error:
            self._owner._log_once("speech_sync_error", f"Unspoken: syncing speech failed: {error}")

    def pause(self, switch: bool) -> None:
        try:
            with self._cond:
                if self._closed:
                    return
                self._paused = bool(switch)
                if self._paused:
                    # A no-op on a stopped source, which then stays stopped.
                    self._al.alSourcePause(self._source)
                elif self._held:
                    self._held = False
                    self._al.alSourcePlay(self._source)
                elif self._state() == AL_PAUSED:
                    self._al.alSourcePlay(self._source)
                self._cond.notify_all()
        except Exception as error:
            self._owner._log_once("speech_pause", f"Unspoken: pausing speech failed: {error}")

    def set_position(self, position: tuple[float, float, float]) -> None:
        try:
            with self._cond:
                if self._closed:
                    return
                self._al.alSource3f(
                    self._source,
                    AL_POSITION,
                    ctypes.c_float(position[0]),
                    ctypes.c_float(position[1]),
                    ctypes.c_float(position[2]),
                )
        except Exception as error:
            self._owner._log_once("speech_position", f"Unspoken: placing speech failed: {error}")

    def close(self) -> None:
        """Stop, release anything blocked, and give the source back. Idempotent."""
        with self._cond:
            if self._closed:
                return
            self._closed = True
            self._generation += 1
            try:
                self._flush()
            except Exception as error:
                log.error(f"Unspoken: could not flush a speech stream: {error}")
            self._cond.notify_all()
        if threading.current_thread() is not self._thread:
            self._thread.join(timeout=5.0)
        try:
            self._al.alSourcei(self._source, AL_BUFFER, AL_NONE)
            source = ctypes.c_uint(self._source)
            self._al.alDeleteSources(1, ctypes.byref(source))
        except Exception as error:
            log.error(f"Unspoken: could not delete a speech source: {error}")

    # -- under the lock -----------------------------------------------------

    def _enqueue(self, data: bytes, on_done) -> list:
        """Queue one chunk; return the callbacks the recycle ahead of it made due."""
        frames = len(data) // 2
        if not frames:
            if on_done is not None:
                self._chunks.append(_Chunk(None, 0, on_done))
                self._cond.notify_all()
            return []
        al = self._al
        # Everything played goes first, so that if the source has run dry the
        # queue below holds only what has not been heard (see the class
        # docstring for why that has to be true before alSourcePlay).
        due = self._recycle()
        buffer_id = ctypes.c_uint(0)
        al.alGenBuffers(1, ctypes.byref(buffer_id))
        al.alBufferData(buffer_id.value, AL_FORMAT_MONO16, data, frames * 2, self._rate)
        al.alSourceQueueBuffers(self._source, 1, ctypes.byref(buffer_id))
        self._chunks.append(_Chunk(buffer_id.value, frames, on_done))
        self._queued_frames += frames
        if not self._held and self._state() not in (AL_PLAYING, AL_PAUSED):
            # Initial, or run dry: start now rather than at the next service
            # tick, because this is the onset of speech.
            if self._paused:
                self._held = True
            else:
                al.alSourcePlay(self._source)
        self._cond.notify_all()
        return due

    def _recycle(self) -> list:
        """Unqueue what has played and pop its chunks; return the callbacks due."""
        if self._held:
            return []  # stopped, but nothing on it has been heard yet
        processed = self._count(AL_BUFFERS_PROCESSED)
        self._unqueue(processed)
        due = []
        chunks = self._chunks
        while chunks:
            chunk = chunks[0]
            if chunk.buffer_id is not None:
                if not processed:
                    break
                processed -= 1
                self._queued_frames -= chunk.frames
            chunks.popleft()
            if chunk.on_done is not None:
                due.append(chunk.on_done)
        self._in_flight += len(due)
        if due:
            self._cond.notify_all()
        return due

    def _completed_now(self, on_done) -> list:
        """A callback for audio that will never play, due at once."""
        if on_done is None:
            return []
        self._in_flight += 1
        return [on_done]

    def _flush(self) -> None:
        """Stop the source and free every buffer; callbacks are dropped."""
        self._al.alSourceStop(self._source)
        # A stopped source has processed everything it held.
        self._unqueue(self._count(AL_BUFFERS_QUEUED))
        self._chunks.clear()
        self._queued_frames = 0
        self._held = False

    def _state(self) -> int:
        self._al.alGetSourcei(self._source, AL_SOURCE_STATE, ctypes.byref(self._scratch))
        return self._scratch.value

    def _count(self, token: int) -> int:
        self._al.alGetSourcei(self._source, token, ctypes.byref(self._scratch))
        return max(0, self._scratch.value)

    def _unqueue(self, count: int) -> None:
        if count <= 0:
            return
        ids = (ctypes.c_uint * count)()
        self._al.alSourceUnqueueBuffers(self._source, count, ids)
        self._al.alDeleteBuffers(count, ids)

    def _service(self) -> list:
        """One service tick: recycle, or complete everything if no one can hear it."""
        owner = self._owner
        if owner._disconnected or owner._closed:
            # Complete rather than strand the speech manager on indexes that
            # would never be reached.
            due = [chunk.on_done for chunk in self._chunks if chunk.on_done is not None]
            self._flush()
            self._in_flight += len(due)
            self._cond.notify_all()
            return due
        return self._recycle()

    def _deadline(self) -> float:
        return time.monotonic() + self._queued_frames / self._rate + 2.0

    # -- outside the lock ---------------------------------------------------

    def _complete(self, callbacks) -> None:
        """Run recycled callbacks, then let `sync` know they have run."""
        if not callbacks:
            return
        for callback in callbacks:
            try:
                callback()
            except Exception:
                log.error("Unspoken: a speech onDone callback raised", exc_info=True)
        with self._cond:
            self._in_flight -= len(callbacks)
            self._cond.notify_all()

    def _run(self) -> None:
        """The service thread: sleeps while nothing is queued, ticks while speech plays."""
        while True:
            due = []
            try:
                with self._cond:
                    while not self._chunks and not self._closed:
                        self._cond.wait()
                    if self._closed:
                        return
                    due = self._service()
            except Exception as error:  # a dead service thread strands the synth
                self._owner._log_once("speech_service", f"Unspoken: speech service error: {error}")
            self._complete(due)
            time.sleep(SPEECH_SERVICE_SECONDS)
