"""Spatial audio engine built on top of pyopenal.

This module provides a drop-in replacement for the old Steam Audio DLL
interface.  It exposes a small wrapper that manages an OpenAL device/context
and offers helpers for playing mono PCM samples positioned in 3D space.

The implementation intentionally keeps the public API compatible with the
previous wrapper when possible so the rest of the add-on can keep importing the
module using the same name.
"""

import math
import threading
import time
from array import array
from ctypes import byref, c_char, c_int, c_uint

try:
        from logHandler import log
except ImportError:  # pragma: no cover - NVDA-only dependency
        import logging as log

try:
        from openal.al import (
                AL_BUFFER,
                AL_FORMAT_MONO16,
                AL_GAIN,
                AL_PLAYING,
                AL_POSITION,
                AL_ROLLOFF_FACTOR,
                AL_SOURCE_STATE,
                alBufferData,
                alDeleteBuffers,
                alDeleteSources,
                alGenBuffers,
                alGenSources,
                alGetSourcei,
                alSource3f,
                alSourcePlay,
                alSourceStop,
                alSourcef,
                alSourcei,
        )
        from openal.alc import (
                alcCloseDevice,
                alcCreateContext,
                alcDestroyContext,
                alcMakeContextCurrent,
                alcOpenDevice,
        )
except ImportError as e:  # pragma: no cover - handled at runtime
        raise ImportError(
                "pyopenal is required to run the Unspoken add-on. Please install the"
                " 'openal' package."
        ) from e


_openal_mutex = threading.RLock()


def _clamp_sample(value):
        return max(min(int(value), 32767), -32768)


class SteamAudio:
        """Python implementation of the audio engine using pyopenal."""

        def __init__(self, output_device=None):
                self.device = None
                self.context = None
                self.initialized = False
                self.sample_rate = 44100
                self.output_device = output_device
                self._active_handles = []
                self._reverb_settings = {
                        "room_size": 0.1,
                        "damping": 0.5,
                        "wet_level": 0.1,
                        "dry_level": 1.0,
                        "width": 1.0,
                }

        # ------------------------------------------------------------------
        # Initialization / shutdown
        # ------------------------------------------------------------------
        def initialize(self, sample_rate=44100, frame_size=1024, output_device=None):
                """Initialise OpenAL using pyopenal."""
                if output_device is not None:
                        self.output_device = output_device
                with _openal_mutex:
                        if self.initialized:
                                return True
                        device_name = None
                        if self.output_device:
                                try:
                                        device_name = self.output_device.encode("utf-8")
                                except Exception:
                                        device_name = None
                        device = alcOpenDevice(device_name)
                        if not device and device_name:
                                log.warning(
                                        "Failed to open OpenAL device %r, falling back to default",
                                        self.output_device,
                                )
                                device = alcOpenDevice(None)
                        if not device:
                                log.error("Unable to open OpenAL device")
                                return False
                        context = alcCreateContext(device, None)
                        if not context:
                                log.error("Unable to create OpenAL context")
                                alcCloseDevice(device)
                                return False
                        if not alcMakeContextCurrent(context):
                                log.error("Unable to make OpenAL context current")
                                alcDestroyContext(context)
                                alcCloseDevice(device)
                                return False
                        self.device = device
                        self.context = context
                        self.initialized = True
                        self.sample_rate = sample_rate
                        self._active_handles = []
                        log.debug("OpenAL initialised successfully")
                        return True

        def reinitialize(self, output_device=None):
                with _openal_mutex:
                        self.cleanup()
                        return self.initialize(output_device=output_device)

        def cleanup(self):
                with _openal_mutex:
                        if not self.initialized:
                                return
                        for source_id, buffer_id in list(self._active_handles):
                                try:
                                        alSourceStop(source_id.value)
                                except Exception:
                                        pass
                                try:
                                        alDeleteSources(1, byref(source_id))
                                except Exception:
                                        pass
                                try:
                                        alDeleteBuffers(1, byref(buffer_id))
                                except Exception:
                                        pass
                        self._active_handles.clear()
                        alcMakeContextCurrent(None)
                        if self.context:
                                alcDestroyContext(self.context)
                                self.context = None
                        if self.device:
                                alcCloseDevice(self.device)
                                self.device = None
                        self.initialized = False
                        log.debug("OpenAL cleaned up")

        # ------------------------------------------------------------------
        # Reverb helpers
        # ------------------------------------------------------------------
        def set_reverb_settings(self, room_size, damping, wet_level, dry_level, width):
                """Store reverb configuration for later playback."""
                with _openal_mutex:
                        self._reverb_settings = {
                                "room_size": max(0.0, min(room_size, 1.0)),
                                "damping": max(0.0, min(damping, 1.0)),
                                "wet_level": max(0.0, min(wet_level, 1.0)),
                                "dry_level": max(0.0, min(dry_level, 1.0)),
                                "width": max(0.0, min(width, 1.0)),
                        }
                        log.debug("Updated reverb settings: %s", self._reverb_settings)
                return True

        def _apply_reverb(self, pcm_data, sample_rate):
                                if not pcm_data:
                                        return pcm_data
                                settings = self._reverb_settings
                                delay = max(
                                        1,
                                        int(
                                                sample_rate
                                                * (0.01 + settings["room_size"] * (0.05 + 0.02 * settings["width"]))
                                        ),
                                )
                                damping = 0.2 + 0.6 * settings["damping"]
                                wet_gain = settings["wet_level"]
                                dry_gain = settings["dry_level"] if settings["dry_level"] > 0 else 1.0
                                samples = array("h")
                                samples.frombytes(pcm_data)
                                output = array("h", samples)
                                for i in range(len(output)):
                                        dry_component = dry_gain * samples[i]
                                        wet_component = 0.0
                                        if i >= delay:
                                                wet_component = wet_gain * output[i - delay] * damping
                                        mixed = _clamp_sample(dry_component + wet_component)
                                        output[i] = mixed
                                return output.tobytes()

        # ------------------------------------------------------------------
        # Playback
        # ------------------------------------------------------------------
        def stop_all(self):
                with _openal_mutex:
                        if not self.initialized:
                                self._active_handles.clear()
                                return
                        for source_id, buffer_id in list(self._active_handles):
                                try:
                                        alSourceStop(source_id.value)
                                except Exception:
                                        pass
                                try:
                                        alDeleteSources(1, byref(source_id))
                                except Exception:
                                        pass
                                try:
                                        alDeleteBuffers(1, byref(buffer_id))
                                except Exception:
                                        pass
                        self._active_handles.clear()

        def _angles_to_position(self, angle_x, angle_y):
                azimuth = math.radians(angle_x)
                elevation = math.radians(angle_y)
                x = math.cos(elevation) * math.sin(azimuth)
                y = math.sin(elevation)
                z = math.cos(elevation) * math.cos(azimuth)
                return (x, y, z)

        def play_sound(
                self,
                _cache_key,
                pcm_data,
                sample_rate,
                angle_x,
                angle_y,
                volume=1.0,
                enable_reverb=True,
        ):
                if not self.initialized:
                        log.error("OpenAL not initialised")
                        return False
                if not pcm_data:
                        return False
                gain = max(0.0, min(volume, 2.0))
                if enable_reverb:
                        try:
                                pcm_data = self._apply_reverb(pcm_data, sample_rate)
                        except Exception as e:
                                log.error("Failed to apply reverb: %s", e)
                x, y, z = self._angles_to_position(angle_x, angle_y)
                buffer_id = c_uint(0)
                source_id = c_uint(0)
                audio_buffer = (c_char * len(pcm_data)).from_buffer_copy(pcm_data)
                with _openal_mutex:
                        alGenBuffers(1, byref(buffer_id))
                        alBufferData(buffer_id.value, AL_FORMAT_MONO16, audio_buffer, len(pcm_data), sample_rate)
                        alGenSources(1, byref(source_id))
                        alSourcei(source_id.value, AL_BUFFER, buffer_id.value)
                        alSourcef(source_id.value, AL_GAIN, gain)
                        alSource3f(source_id.value, AL_POSITION, x, y, z)
                        try:
                                alSourcef(source_id.value, AL_ROLLOFF_FACTOR, 1.0)
                        except Exception:
                                pass
                        alSourcePlay(source_id.value)
                        self._active_handles.append((source_id, buffer_id))
                threading.Thread(
                        target=self._monitor_source,
                        args=(source_id, buffer_id),
                        daemon=True,
                ).start()
                return True

        def _monitor_source(self, source_id, buffer_id):
                state = c_int(AL_PLAYING)
                try:
                        while True:
                                with _openal_mutex:
                                        alGetSourcei(source_id.value, AL_SOURCE_STATE, byref(state))
                                if state.value != AL_PLAYING:
                                        break
                                time.sleep(0.01)
                finally:
                        with _openal_mutex:
                                try:
                                        alSourceStop(source_id.value)
                                except Exception:
                                        pass
                                try:
                                        alDeleteSources(1, byref(source_id))
                                except Exception:
                                        pass
                                try:
                                        alDeleteBuffers(1, byref(buffer_id))
                                except Exception:
                                        pass
                                self._active_handles = [
                                        handle
                                        for handle in self._active_handles
                                        if handle[0].value != source_id.value
                                ]

        # Compatibility helpers ------------------------------------------------
        def process_sound(self, input_buffer, angle_x, angle_y):  # pragma: no cover - legacy API
                raise NotImplementedError("process_sound is no longer available; use play_sound().")

        def apply_reverb(self, input_buffer):  # pragma: no cover - legacy API
                raise NotImplementedError("apply_reverb is no longer available; use play_sound().")

        def __del__(self):  # pragma: no cover - cleanup helper
                try:
                        self.cleanup()
                except Exception:
                        pass


_steam_audio_instance = None


def get_steam_audio():
        global _steam_audio_instance
        if _steam_audio_instance is None:
                _steam_audio_instance = SteamAudio()
        return _steam_audio_instance


def initialize_steam_audio(sample_rate=44100, frame_size=1024, output_device=None):
        engine = get_steam_audio()
        return engine.initialize(sample_rate=sample_rate, frame_size=frame_size, output_device=output_device)


def cleanup_steam_audio():
        global _steam_audio_instance
        if _steam_audio_instance:
                _steam_audio_instance.cleanup()
                _steam_audio_instance = None
