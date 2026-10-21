"""Spatial speech: the synth's audio, placed where the role sounds are (ADR 0008).

Every NVDA synth that plays in-process plays through `nvwave.WavePlayer`, and
the speech it hands over is a mono stream. This module patches five of that
class's methods -- `feed`, `stop`, `sync`, `idle`, `pause` -- and gives each
mono speech player a *lane*: the route its audio takes for the current
utterance. There are three routes, one per `spatialSpeech` setting:

- **none** -- the player's own methods, untouched. Speech is exactly what it
  would be without the addon.
- **panning** -- a companion stereo `WavePlayer` on the same device, fed the
  mono audio duplicated into both channels, with its left/right channel
  volumes set from `spatial.pan_gains`. Still nvwave: ducking, leading-silence
  trimming and device handling are nvwave's own.
- **hrtf** -- a `SpeechStream` from the Sound Player: the role sounds' device,
  HRTF and clock, played dry. Falls back to panning when there is no such
  stream (a degraded session) or the synth is not 16-bit.

Interface facts a caller must know:

- The route is chosen at an **utterance boundary** -- the first `feed` after
  the player was created, stopped or idled -- and kept until the next one. An
  utterance therefore always finishes where it started; a settings change, or
  the user moving on, applies from the next utterance.
- The position is read at the same boundary, from `position`. The wiring sets
  it on every arrival (`playback.is_arrival`), on NVDA's main thread; the
  synth's thread reads it. A tuple rebind is atomic, so there is no lock.
- Players that are not mono speech -- tones, wave files, the companions this
  module makes -- are never routed, and pay one attribute lookup per call.
- `install` patches, `uninstall` restores what is still ours (someone may have
  patched over us) and stops every routed lane, so no speech is left on a
  route that is going away.

No NVDA imports. The wave-player class, the setting, the companion factory and
the speech-stream factory are all handed in, so routing, boundaries and
fallbacks are testable off NVDA (`tests/test_speechrouter.py`).
"""

from __future__ import annotations

import ctypes
import threading
import weakref

try:  # pragma: no cover - package import inside NVDA
    from . import spatial
except ImportError:  # pragma: no cover - bare import under pytest
    import spatial

try:  # pragma: no cover - depends on whether NVDA is hosting us
    from logHandler import log
except ImportError:  # pragma: no cover - off-NVDA (tests, tooling)
    import logging

    log = logging.getLogger(__name__)


#: The methods patched on the wave-player class, and the only ones a synth
#: drives playback through. `close` is `stop` in nvwave, so it follows.
PATCHED_METHODS = ("feed", "stop", "sync", "idle", "pause")

#: nvwave's leading-silence threshold for 16-bit audio: full scale / 2**10
#: (`SilenceDetect::defaultThreshold` in nvdaHelper). The HRTF route trims to
#: it itself, because nvwave's trimming lives in the C++ player it bypasses.
SILENCE_THRESHOLD_16 = 32767 >> 10

#: Where speech comes from before anything has arrived: dead ahead.
CENTRE = (0.0, 0.0, -1.0)

#: A lane attribute's value for a route that failed to build, so a dead
#: device is not retried on every utterance.
_UNAVAILABLE = object()

_LANE_ATTRIBUTE = "_unspokenLane"


# --------------------------------------------------------------------------
# Pure audio helpers
# --------------------------------------------------------------------------


def as_bytes(data, size=None) -> bytes:
    """nvwave's `feed` arguments as bytes: bytes, a ctypes pointer, or None."""
    if data is None or size == 0:
        return b""
    if isinstance(data, bytes):
        return data
    if isinstance(data, (bytearray, memoryview)):
        return bytes(data)
    return ctypes.string_at(data, size)


def upmix_mono(data: bytes, width: int) -> bytes:
    """Duplicate mono frames into both channels of an interleaved stereo stream.

    Byte-sliced, so it costs a handful of C-level copies per chunk whatever
    the sample width, rather than a Python loop per sample.
    """
    frames = len(data) // width
    data = data[: frames * width]
    out = bytearray(frames * width * 2)
    step = width * 2
    for offset in range(width):
        column = data[offset::width]
        out[offset::step] = column
        out[width + offset::step] = column
    return bytes(out)


def leading_silence_bytes(data: bytes) -> int:
    """How many leading bytes of 16-bit mono PCM are silence, by nvwave's rule."""
    usable = len(data) - len(data) % 2
    samples = memoryview(data)[:usable].cast("h")
    threshold = SILENCE_THRESHOLD_16
    for index, sample in enumerate(samples):
        if sample > threshold or sample < -threshold:
            return index * 2
    return usable


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------


class _Passthrough:
    """The player's own methods: speech as nvwave would play it."""

    __slots__ = ("_originals",)

    def __init__(self, originals) -> None:
        self._originals = originals

    def begin(self, player, lane, position) -> None:
        pass

    def feed(self, player, lane, data, size, onDone) -> None:
        self._originals["feed"](player, data, size, onDone)

    def stop(self, player) -> None:
        self._originals["stop"](player)

    def sync(self, player) -> None:
        self._originals["sync"](player)

    def idle(self, player) -> None:
        self._originals["idle"](player)

    def pause(self, player, switch) -> None:
        self._originals["pause"](player, switch)

    def close(self) -> None:
        pass


class _PannedRoute:
    """A companion stereo wave player, panned by its channel volumes.

    Panning is a channel volume set once per utterance, not a gain applied to
    every sample, so the per-chunk cost is the upmix alone.
    """

    __slots__ = ("_companion", "_width")

    def __init__(self, companion, width: int) -> None:
        self._companion = companion
        self._width = width

    def begin(self, player, lane, position) -> None:
        left, right = spatial.pan_gains(position)
        self._companion.setVolume(left=left, right=right)

    def feed(self, player, lane, data, size, onDone) -> None:
        self._companion.feed(upmix_mono(as_bytes(data, size), self._width), onDone=onDone)

    def stop(self, player) -> None:
        self._companion.stop()

    def sync(self, player) -> None:
        self._companion.sync()

    def idle(self, player) -> None:
        self._companion.idle()

    def pause(self, player, switch) -> None:
        self._companion.pause(switch)

    def close(self) -> None:
        self._companion.stop()


class _SpatialRoute:
    """The Sound Player's speech stream: HRTF and clock shared with the earcons, no reverb.

    It bypasses nvwave's C++ player, so it does by hand the two things that
    player would have done for this utterance: assert the player's own audio
    ducker, and trim leading silence under the player's own trimming settings.
    """

    __slots__ = ("_stream",)

    def __init__(self, stream) -> None:
        self._stream = stream

    def begin(self, player, lane, position) -> None:
        self._stream.set_position(position)
        # nvwave trims when trimming is enabled for the player, unless this
        # utterance deliberately opens with a BreakCommand's silence -- which
        # the player's own pre-speech handler has already recorded.
        lane.trimming = bool(getattr(player, "_enableTrimmingLeadingSilence", False)) and not bool(
            getattr(player, "_isLeadingSilenceInserted", False)
        )

    def feed(self, player, lane, data, size, onDone) -> None:
        _duck(player, True)
        data = as_bytes(data, size)
        if lane.trimming and data:
            silent = leading_silence_bytes(data)
            if silent < len(data):
                lane.trimming = False
            data = data[silent:]
        self._stream.feed(data, onDone)

    def stop(self, player) -> None:
        self._stream.stop()
        _duck(player, False)

    def sync(self, player) -> None:
        self._stream.sync()

    def idle(self, player) -> None:
        self._stream.sync()
        _duck(player, False)

    def pause(self, player, switch) -> None:
        self._stream.pause(switch)
        _duck(player, not switch)

    def close(self) -> None:
        self._stream.close()


def _duck(player, enable: bool) -> None:
    ducker = getattr(player, "_audioDucker", None)
    if ducker is None:
        return
    try:
        if enable:
            ducker.enable()
        else:
            ducker.disable()
    except Exception:
        log.debug("Unspoken: could not change audio ducking", exc_info=True)


class _Lane:
    """One mono speech player's routing state."""

    __slots__ = ("route", "fresh", "trimming", "panned", "spatial", "__weakref__")

    def __init__(self, passthrough) -> None:
        self.route = passthrough
        #: True from creation and after every stop or idle: the next feed
        #: starts an utterance, which is when the route and position are set.
        self.fresh = True
        self.trimming = False
        #: Built on first use and kept for the player's life, so toggling the
        #: setting does not reopen a device per utterance.
        self.panned = None
        self.spatial = None


# --------------------------------------------------------------------------
# The router
# --------------------------------------------------------------------------


class SpeechRouter:
    """Patches a wave-player class so mono speech follows `spatialSpeech`."""

    def __init__(self, wave_player_class, *, mode, make_companion, open_speech_stream) -> None:
        """
        - `wave_player_class`: `nvwave.WavePlayer`, or a test double of it.
        - `mode()`: the current `spatialSpeech` value. Read once per
          utterance, on the synth's thread.
        - `make_companion(player)`: a stereo wave player matching `player`'s
          rate, width and device, or raise.
        - `open_speech_stream(samples_per_sec)`: a `player.SpeechStream`, or
          None when the Sound Player cannot carry speech.
        """
        self._class = wave_player_class
        self._mode = mode
        self._make_companion = make_companion
        self._open_speech_stream = open_speech_stream
        self._originals: dict = {}
        self._installed: dict = {}
        self._passthrough = None
        self._players: weakref.WeakSet = weakref.WeakSet()
        self._lock = threading.Lock()
        self._logged: set[str] = set()
        #: Where the next utterance comes from. Rebound, never mutated.
        self.position = CENTRE

    # -- lifetime -----------------------------------------------------------

    def install(self) -> None:
        """Patch the five methods. Resolve all of them before assigning any."""
        if self._installed:
            return
        originals = {name: getattr(self._class, name) for name in PATCHED_METHODS}
        self._passthrough = _Passthrough(originals)
        patches = self._build_patches(originals)
        self._originals = originals
        for name, patch in patches.items():
            setattr(self._class, name, patch)
        self._installed = patches

    def uninstall(self) -> None:
        """Restore what is still ours and stop every routed lane. Total."""
        for name, patch in self._installed.items():
            try:
                if self._class.__dict__.get(name) is patch:
                    setattr(self._class, name, self._originals[name])
            except Exception:
                log.debug(f"Unspoken: could not restore WavePlayer.{name}", exc_info=True)
        self._installed = {}
        with self._lock:
            players = list(self._players)
            self._players.clear()
        for player in players:
            lane = player.__dict__.pop(_LANE_ATTRIBUTE, None)
            if not isinstance(lane, _Lane):
                continue
            for route in (lane.panned, lane.spatial):
                if route is not None and route is not _UNAVAILABLE:
                    try:
                        route.close()
                    except Exception:
                        log.debug("Unspoken: could not close a speech route", exc_info=True)

    # -- the patched methods ------------------------------------------------

    def _build_patches(self, originals):
        router = self
        original_feed = originals["feed"]
        original_stop = originals["stop"]
        original_sync = originals["sync"]
        original_idle = originals["idle"]
        original_pause = originals["pause"]

        def feed(player, data, size=None, onDone=None):
            lane = router._lane(player)
            if lane is None:
                return original_feed(player, data, size, onDone)
            if lane.fresh:
                router._begin(player, lane)
            lane.route.feed(player, lane, data, size, onDone)

        def stop(player):
            lane = router._lane(player)
            if lane is None:
                return original_stop(player)
            lane.fresh = True
            lane.route.stop(player)

        def sync(player):
            lane = router._lane(player)
            if lane is None:
                return original_sync(player)
            lane.route.sync(player)

        def idle(player):
            lane = router._lane(player)
            if lane is None:
                return original_idle(player)
            lane.route.idle(player)
            lane.fresh = True

        def pause(player, switch):
            lane = router._lane(player)
            if lane is None:
                return original_pause(player, switch)
            lane.route.pause(player, switch)

        patches = {"feed": feed, "stop": stop, "sync": sync, "idle": idle, "pause": pause}
        for name, patch in patches.items():
            patch.__name__ = name
            patch.__qualname__ = f"{getattr(self._class, '__name__', 'WavePlayer')}.{name}"
            patch.__doc__ = getattr(originals[name], "__doc__", None)
        return patches

    # -- lanes and routes ---------------------------------------------------

    def _lane(self, player):
        """The player's lane, or None if it is not mono speech. One dict lookup when known."""
        lane = player.__dict__.get(_LANE_ATTRIBUTE)
        if lane is not None:
            return lane if lane is not _UNAVAILABLE else None
        with self._lock:
            lane = player.__dict__.get(_LANE_ATTRIBUTE)
            if lane is None:
                lane = _Lane(self._passthrough) if _is_mono_speech(player) else _UNAVAILABLE
                player.__dict__[_LANE_ATTRIBUTE] = lane
                if lane is not _UNAVAILABLE:
                    self._players.add(player)
        return lane if lane is not _UNAVAILABLE else None

    def _begin(self, player, lane) -> None:
        """An utterance starts: choose its route and place it. Never raises."""
        lane.fresh = False
        try:
            route = self._route_for(player, lane, self._mode())
            route.begin(player, lane, self.position)
        except Exception as error:
            self._log_once("begin", f"Unspoken: could not place speech; leaving it unplaced: {error}")
            route = self._passthrough
        lane.route = route

    def _route_for(self, player, lane, mode):
        if mode == "hrtf":
            route = self._spatial(player, lane)
            if route is not None:
                return route
            mode = "panning"
        if mode == "panning":
            route = self._panned(player, lane)
            if route is not None:
                return route
        return self._passthrough

    def _panned(self, player, lane):
        if lane.panned is None:
            lane.panned = _UNAVAILABLE
            width = int(getattr(player, "bitsPerSample", 0)) // 8
            if width not in (1, 2, 3, 4):
                self._log_once("panned_width", f"Unspoken: cannot pan {width * 8}-bit speech")
                return None
            try:
                lane.panned = _PannedRoute(self._make_companion(player), width)
            except Exception as error:
                self._log_once("companion", f"Unspoken: could not open a panned speech stream: {error}")
        return lane.panned if lane.panned is not _UNAVAILABLE else None

    def _spatial(self, player, lane):
        if lane.spatial is None:
            lane.spatial = _UNAVAILABLE
            if int(getattr(player, "bitsPerSample", 0)) != 16:
                self._log_once("spatial_width", "Unspoken: HRTF speech needs 16-bit audio; panning instead")
                return None
            stream = self._open_speech_stream(int(getattr(player, "samplesPerSec", 0)))
            if stream is not None:
                lane.spatial = _SpatialRoute(stream)
                weakref.finalize(player, stream.close)
        return lane.spatial if lane.spatial is not _UNAVAILABLE else None

    def _log_once(self, key: str, message: str) -> None:
        if key in self._logged:
            return
        self._logged.add(key)
        log.warning(message)


def _is_mono_speech(player) -> bool:
    """Mono audio a synth is speaking -- not a tone, a wave file, or a companion."""
    purpose = getattr(player, "_purpose", None)
    return getattr(purpose, "name", None) == "SPEECH" and getattr(player, "channels", None) == 1
