"""
OpenAL Soft ctypes loopback wrapper for HRTF spatialization and EFX reverb.

Uses ALC_SOFT_loopback: all rendering is synchronous inside
alcRenderSamplesSOFT, with no background mixing thread. This matches the
thread-per-sound architecture in __init__.py -- each ephemeral audio thread
acquires _openal_audio_mutex, renders, and feeds bytes to nvwave.WavePlayer.
nvwave.WavePlayer remains the sole audio output path, preserving NVDA ducking
and device routing.

Requires soft_oal.dll (OpenAL Soft official Windows x64 build) in the same
directory. DLL load failure raises OSError at import time.
"""

import ctypes
import math
import os
import threading

try:
    from logHandler import log
except ImportError:
    import logging as log

# OpenAL Soft constants verified against kcat/openal-soft efx.h
ALC_STEREO_SOFT = 0x1501
ALC_SHORT_SOFT = 0x1402
ALC_FORMAT_CHANNELS_SOFT = 0x1990
ALC_FORMAT_TYPE_SOFT = 0x1991
ALC_HRTF_SOFT = 0x1992
ALC_FREQUENCY = 0x1007

AL_FORMAT_MONO16 = 0x1101
AL_BUFFER = 0x1009
AL_POSITION = 0x1004
AL_GAIN = 0x100A
AL_NONE = 0

# EFX effect type constants
AL_EFFECT_TYPE = 0x8001
AL_EFFECT_REVERB = 0x0001

# EFX reverb parameter constants
AL_REVERB_DIFFUSION = 0x0002
AL_REVERB_GAIN = 0x0003
AL_REVERB_GAINHF = 0x0004
AL_REVERB_DECAY_TIME = 0x0005

# EFX slot and routing constants
AL_EFFECTSLOT_EFFECT = 0x0001
AL_AUXILIARY_SEND_FILTER = 0x20006
AL_FILTER_NULL = 0x0000

# AL error constants
AL_NO_ERROR = 0
ALC_NO_ERROR = 0

# Module-level mutex serializes all OpenAL calls across thread-per-sound threads.
# alcMakeContextCurrent is called once at initialize(); thereafter each thread
# acquires this lock only for the alcRenderSamplesSOFT render window.
_openal_audio_mutex = threading.Lock()


def _load_openal_dll(dll_path):
    """Load soft_oal.dll and configure ctypes argtypes/restype for all used symbols.

    Raises OSError if the DLL is not found or cannot be loaded.
    """
    dll = ctypes.CDLL(dll_path)

    # ALC device/context functions
    dll.alcCreateContext.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]
    dll.alcCreateContext.restype = ctypes.c_void_p
    dll.alcDestroyContext.argtypes = [ctypes.c_void_p]
    dll.alcDestroyContext.restype = None
    dll.alcMakeContextCurrent.argtypes = [ctypes.c_void_p]
    dll.alcMakeContextCurrent.restype = ctypes.c_int
    dll.alcGetIntegerv.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
    dll.alcGetIntegerv.restype = None
    dll.alcGetProcAddress.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    dll.alcGetProcAddress.restype = ctypes.c_void_p
    dll.alcCloseDevice.argtypes = [ctypes.c_void_p]
    dll.alcCloseDevice.restype = ctypes.c_int
    dll.alcGetError.argtypes = [ctypes.c_void_p]
    dll.alcGetError.restype = ctypes.c_int

    # AL source/buffer functions
    dll.alGenSources.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_uint)]
    dll.alGenSources.restype = None
    dll.alDeleteSources.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_uint)]
    dll.alDeleteSources.restype = None
    dll.alGenBuffers.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_uint)]
    dll.alGenBuffers.restype = None
    dll.alDeleteBuffers.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_uint)]
    dll.alDeleteBuffers.restype = None
    dll.alBufferData.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
    dll.alBufferData.restype = None
    dll.alSourcei.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_int]
    dll.alSourcei.restype = None
    dll.alSourcef.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_float]
    dll.alSourcef.restype = None
    dll.alSource3f.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_float, ctypes.c_float, ctypes.c_float]
    dll.alSource3f.restype = None
    dll.alSource3i.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
    dll.alSource3i.restype = None
    dll.alSourcePlay.argtypes = [ctypes.c_uint]
    dll.alSourcePlay.restype = None
    dll.alSourceStop.argtypes = [ctypes.c_uint]
    dll.alSourceStop.restype = None
    dll.alGetError.argtypes = []
    dll.alGetError.restype = ctypes.c_int

    # EFX extension functions
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


class OpenALLoopback:
    """ctypes wrapper around soft_oal.dll providing HRTF spatialization and EFX reverb
    via the ALC_SOFT_loopback extension.

    A single instance is shared across all threads through the module-level singleton
    get_openal_audio(). initialize() must be called before process_sound().
    DLL load failure sets self.dll = None; subsequent API calls return None/False.
    """

    def __init__(self, dll_path=None):
        self.dll = None
        self.initialized = False
        self._mutex = _openal_audio_mutex
        self._device = None
        self._context = None
        self._source = ctypes.c_uint(0)
        self._buffer = ctypes.c_uint(0)
        self._effect = ctypes.c_uint(0)
        self._effect_slot = ctypes.c_uint(0)
        self.sample_rate = 44100
        self.frame_size = 1024
        self._dry_level = 0.3
        self._reverb_enabled = False
        self._reverb_tail_frames = 0

        # Loopback extension functions loaded via alcGetProcAddress
        self._alcLoopbackOpenDeviceSOFT = None
        self._alcIsRenderFormatSupportedSOFT = None
        self._alcRenderSamplesSOFT = None

        if dll_path is None:
            addon_dir = os.path.dirname(__file__)
            dll_path = os.path.join(addon_dir, "soft_oal.dll")

        try:
            self.dll = _load_openal_dll(dll_path)
            self._load_loopback_extensions()
            log.debug(f"OpenAL Soft DLL loaded from: {dll_path}")
        except OSError as e:
            log.error(f"OpenAL Soft DLL not found or failed to load: {dll_path} -- {e}")
            self.dll = None

    def _load_loopback_extensions(self):
        """Load ALC_SOFT_loopback extension functions via alcGetProcAddress."""
        get_proc = self.dll.alcGetProcAddress
        addr = get_proc(None, b"alcLoopbackOpenDeviceSOFT")
        if not addr:
            raise OSError("alcLoopbackOpenDeviceSOFT not found; ALC_SOFT_loopback extension unavailable")
        self._alcLoopbackOpenDeviceSOFT = ctypes.cast(
            addr,
            ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_char_p)
        )
        addr = get_proc(None, b"alcIsRenderFormatSupportedSOFT")
        if not addr:
            raise OSError("alcIsRenderFormatSupportedSOFT not found; ALC_SOFT_loopback extension unavailable")
        self._alcIsRenderFormatSupportedSOFT = ctypes.cast(
            addr,
            ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int)
        )
        addr = get_proc(None, b"alcRenderSamplesSOFT")
        if not addr:
            raise OSError("alcRenderSamplesSOFT not found; ALC_SOFT_loopback extension unavailable")
        self._alcRenderSamplesSOFT = ctypes.cast(
            addr,
            ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int)
        )

    def _check_al_error(self, context_msg):
        """Log warning if OpenAL error is pending; does not raise."""
        err = self.dll.alGetError()
        if err != AL_NO_ERROR:
            log.warning(f"OpenAL error {err:#x} after {context_msg}")

    def _check_alc_error(self, device, context_msg):
        """Log warning if ALC device-level error is pending; does not raise."""
        err = self.dll.alcGetError(device)
        if err != ALC_NO_ERROR:
            log.warning(f"ALC error {err:#x} after {context_msg}")

    @staticmethod
    def _float_to_int16(float_samples):
        """Convert float32 samples in [-1,1] to int16 PCM array."""
        n = len(float_samples)
        arr = (ctypes.c_int16 * n)()
        for i, s in enumerate(float_samples):
            clamped = max(-1.0, min(1.0, s))
            arr[i] = int(clamped * 32767)
        return arr

    def initialize(self, sample_rate=44100, frame_size=1024):
        """Open loopback device, create HRTF context, and allocate persistent AL objects.

        Returns True on success, False on failure.
        """
        if self.dll is None:
            return False
        if self.initialized:
            log.debug("OpenAL already initialized")
            return True

        with self._mutex:
            device = None
            context = None
            try:
                # Open virtual loopback device -- no audio hardware involved
                device = self._alcLoopbackOpenDeviceSOFT(None)
                if not device:
                    log.error("alcLoopbackOpenDeviceSOFT returned NULL")
                    return False

                # Validate stereo short format before context creation
                if not self._alcIsRenderFormatSupportedSOFT(
                    device, sample_rate, ALC_STEREO_SOFT, ALC_SHORT_SOFT
                ):
                    log.error("Loopback render format not supported")
                    self.dll.alcCloseDevice(device)
                    return False

                # Context attributes: loopback format + HRTF enabled
                attrs = (ctypes.c_int * 9)(
                    ALC_FORMAT_CHANNELS_SOFT, ALC_STEREO_SOFT,
                    ALC_FORMAT_TYPE_SOFT, ALC_SHORT_SOFT,
                    ALC_FREQUENCY, sample_rate,
                    ALC_HRTF_SOFT, 1,
                    0,
                )
                context = self.dll.alcCreateContext(device, attrs)
                if not context:
                    log.error("alcCreateContext failed")
                    self.dll.alcCloseDevice(device)
                    return False
                self.dll.alcMakeContextCurrent(context)
                self._check_alc_error(device, "alcMakeContextCurrent")

                # Verify HRTF activated; log warning but continue if unavailable
                hrtf_status = ctypes.c_int(0)
                self.dll.alcGetIntegerv(device, ALC_HRTF_SOFT, 1, ctypes.byref(hrtf_status))
                if not hrtf_status.value:
                    log.warning("HRTF not available on loopback device; stereo panning will be used")

                # Persistent reusable source and buffer for all process_sound calls
                self.dll.alGenSources(1, ctypes.byref(self._source))
                self.dll.alGenBuffers(1, ctypes.byref(self._buffer))

                # EFX reverb effect and auxiliary slot setup
                self.dll.alGenEffects(1, ctypes.byref(self._effect))
                self._check_al_error("alGenEffects")
                self.dll.alEffecti(self._effect.value, AL_EFFECT_TYPE, AL_EFFECT_REVERB)
                self.dll.alGenAuxiliaryEffectSlots(1, ctypes.byref(self._effect_slot))
                self._check_al_error("alGenAuxiliaryEffectSlots")
                self.dll.alAuxiliaryEffectSloti(self._effect_slot.value, AL_EFFECTSLOT_EFFECT, self._effect.value)

                self._device = device
                self._context = context
                self.sample_rate = sample_rate
                self.frame_size = frame_size
                self.initialized = True
                log.debug(f"OpenAL Soft initialized: {sample_rate}Hz, HRTF={bool(hrtf_status.value)}")
                return True

            except Exception as e:
                log.error(f"OpenAL initialization failed: {e}")
                if context:
                    self.dll.alcMakeContextCurrent(None)
                    self.dll.alcDestroyContext(context)
                if device:
                    self.dll.alcCloseDevice(device)
                return False

    def cleanup(self):
        """Release all AL objects, destroy context, and close loopback device."""
        if not self.initialized:
            return
        with self._mutex:
            self.dll.alSourceStop(self._source.value)
            self.dll.alDeleteSources(1, ctypes.byref(self._source))
            self.dll.alDeleteBuffers(1, ctypes.byref(self._buffer))
            self.dll.alDeleteEffects(1, ctypes.byref(self._effect))
            self.dll.alDeleteAuxiliaryEffectSlots(1, ctypes.byref(self._effect_slot))
            self.dll.alcMakeContextCurrent(None)
            self.dll.alcDestroyContext(self._context)
            self.dll.alcCloseDevice(self._device)
            self._context = None
            self._device = None
            self.initialized = False
        log.debug("OpenAL Soft cleaned up")

    def __del__(self):
        if getattr(self, "initialized", False):
            self.cleanup()

    def set_reverb_settings(self, room_size, damping, wet_level, dry_level, width):
        """Map addon reverb parameters (0.0-1.0 normalized) to EFX reverb effect and source gain.

        dry_level is applied as AL_GAIN on the source at render time -- EFX separates
        dry/wet control at the source level, not the effect level.

        Returns True on success, False if not initialized.
        """
        if self.dll is None:
            return False
        if not self.initialized:
            log.error("OpenAL not initialized")
            return False

        with self._mutex:
            # Map addon parameters (0.0-1.0) to EFX reverb values.
            decay_time = 0.1 + room_size * 3.9
            gainhf = 1.0 - damping * 0.9
            gain = wet_level * 0.5
            diffusion = width

            self._dry_level = dry_level

            self.dll.alEffectf(self._effect.value, AL_REVERB_DECAY_TIME, ctypes.c_float(decay_time))
            self._check_al_error("alEffectf AL_REVERB_DECAY_TIME")
            self.dll.alEffectf(self._effect.value, AL_REVERB_GAINHF, ctypes.c_float(gainhf))
            self._check_al_error("alEffectf AL_REVERB_GAINHF")
            self.dll.alEffectf(self._effect.value, AL_REVERB_GAIN, ctypes.c_float(gain))
            self._check_al_error("alEffectf AL_REVERB_GAIN")
            self.dll.alEffectf(self._effect.value, AL_REVERB_DIFFUSION, ctypes.c_float(diffusion))
            self._check_al_error("alEffectf AL_REVERB_DIFFUSION")

            # Reattach effect to slot after parameter change
            self.dll.alAuxiliaryEffectSloti(self._effect_slot.value, AL_EFFECTSLOT_EFFECT, self._effect.value)

            # Reverb tail: int(decay_time * sample_rate * 2) frames.
            # The *2 multiplier provides headroom for the full decay envelope.
            self._reverb_tail_frames = int(decay_time * self.sample_rate * 2)

            log.debug(f"Reverb settings updated: decay={decay_time:.2f}s, gainhf={gainhf:.2f}, gain={gain:.2f}")
            return True

    def enable_reverb(self, enabled):
        """Toggle reverb processing; wired to config.conf[unspoken][Reverb] checkbox."""
        self._reverb_enabled = bool(enabled)
        if not enabled:
            self._reverb_tail_frames = 0

    def process_sound(self, input_samples, angle_x, angle_y):
        """Spatialize mono float32 samples and return stereo int16 PCM bytes.

        Uploads input_samples to an AL buffer, positions the source in 3D space using
        angle_x/angle_y, then calls alcRenderSamplesSOFT. The single render call applies
        EFX reverb followed by HRTF binaural processing -- no Python-level round-trip
        between reverb and HRTF stages.

        When reverb is enabled, render window is extended by _reverb_tail_frames to capture
        the full EFX decay after the source completes.

        Returns bytes suitable for nvwave.WavePlayer.feed() (stereo 16-bit PCM, interleaved).
        Returns None if not initialized or DLL failed to load.
        """
        if self.dll is None:
            return None
        if not self.initialized:
            log.error("OpenAL not initialized")
            return None

        with self._mutex:
            # Detach buffer from source before re-uploading data.
            # alBufferData fails on a buffer still attached to a source (even stopped).
            self.dll.alSourcei(self._source.value, AL_BUFFER, AL_NONE)

            # Convert float32 mono samples to int16 PCM for OpenAL buffer upload
            pcm_data = self._float_to_int16(input_samples)
            num_input_frames = len(input_samples)
            byte_size = num_input_frames * ctypes.sizeof(ctypes.c_int16)

            self.dll.alBufferData(
                self._buffer.value,
                AL_FORMAT_MONO16,
                pcm_data,
                byte_size,
                self.sample_rate,
            )
            self._check_al_error("alBufferData")

            # Attach buffer and position source as unit direction vector for HRTF.
            # Raw degree values would place the source far from the listener,
            # causing near-silence from OpenAL's distance attenuation model.
            self.dll.alSourcei(self._source.value, AL_BUFFER, self._buffer.value)
            rad_x = math.radians(angle_x)
            rad_y = math.radians(angle_y)
            pos_x = math.sin(rad_x) * math.cos(rad_y)
            pos_y = math.sin(rad_y)
            pos_z = -math.cos(rad_x) * math.cos(rad_y)
            self.dll.alSource3f(
                self._source.value, AL_POSITION,
                ctypes.c_float(pos_x), ctypes.c_float(pos_y), ctypes.c_float(pos_z)
            )
            # Dry level is the source gain; EFX separates dry/wet at source level
            self.dll.alSourcef(self._source.value, AL_GAIN, ctypes.c_float(self._dry_level))

            if self._reverb_enabled:
                self.dll.alSource3i(
                    self._source.value, AL_AUXILIARY_SEND_FILTER,
                    self._effect_slot.value, 0, AL_FILTER_NULL
                )
            else:
                # Disconnect from EFX slot when reverb disabled
                self.dll.alSource3i(self._source.value, AL_AUXILIARY_SEND_FILTER, 0, 0, AL_FILTER_NULL)

            self.dll.alSourcePlay(self._source.value)
            self._check_al_error("alSourcePlay")

            # Reverb tail extends render window to capture decay after source completes
            tail_frames = self._reverb_tail_frames if self._reverb_enabled else 0
            num_frames = num_input_frames + tail_frames

            # Stereo output: 2 samples per frame (HRTF binaural output)
            out_buf = (ctypes.c_int16 * (num_frames * 2))()
            self._alcRenderSamplesSOFT(self._device, out_buf, num_frames)
            self._check_alc_error(self._device, "alcRenderSamplesSOFT")

            self.dll.alSourceStop(self._source.value)
            return bytes(out_buf)

    def apply_reverb(self, input_buffer):
        """Return input_buffer unchanged.
        Reverb is applied inside process_sound via EFX effect slot.
        Callers that invoke apply_reverb separately receive unmodified audio."""
        return input_buffer


# Module-level singleton -- one OpenALLoopback instance shared across all threads
_openal_audio_instance = None


def get_openal_audio():
    """Return the global OpenALLoopback singleton, creating it on first call."""
    global _openal_audio_instance
    if _openal_audio_instance is None:
        _openal_audio_instance = OpenALLoopback()
    return _openal_audio_instance


def initialize_openal_audio(sample_rate=44100, frame_size=1024):
    """Initialize the global OpenALLoopback instance."""
    return get_openal_audio().initialize(sample_rate, frame_size)


def cleanup_openal_audio():
    """Cleanup and release the global OpenALLoopback instance."""
    global _openal_audio_instance
    if _openal_audio_instance is not None:
        _openal_audio_instance.cleanup()
        _openal_audio_instance = None
