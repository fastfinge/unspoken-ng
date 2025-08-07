"""
Python ctypes binding for Steam Audio DLL
Provides 3D audio positioning, reverb, and 16-bit sample output
"""

import ctypes
import os
import struct
import threading
from ctypes import c_bool, c_int, c_float, POINTER, byref

try:
	from logHandler import log
except ImportError:
	import logging as log

# Global mutex for thread-safe DLL access
_steam_audio_mutex = threading.Lock()


# Define ctypes for the DLL functions
class SteamAudio:
	def __init__(self, dll_path=None):
		"""Initialize Steam Audio wrapper

		Args:
		    dll_path: Path to steam_audio.dll. If None, looks in addon directory.
		"""
		self.dll = None
		self.initialized = False

		if dll_path is None:
			# Look for DLL in the same directory as this module
			addon_dir = os.path.dirname(__file__)
			dll_path = os.path.join(addon_dir, "steam_audio.dll")

		if not os.path.exists(dll_path):
			raise FileNotFoundError(f"Steam Audio DLL not found at: {dll_path}")

		try:
			self.dll = ctypes.CDLL(dll_path)
			self._setup_function_signatures()
			log.debug(f"Steam Audio DLL loaded from: {dll_path}")
		except Exception as e:
			log.error(f"Failed to load Steam Audio DLL: {e}")
			raise

	def _setup_function_signatures(self):
		"""Setup ctypes function signatures for all DLL functions"""

		# bool initialize_steam_audio(int samplingrate, int framesize)
		self.dll.initialize_steam_audio.argtypes = [c_int, c_int]
		self.dll.initialize_steam_audio.restype = c_bool

		# void cleanup_steam_audio()
		self.dll.cleanup_steam_audio.argtypes = []
		self.dll.cleanup_steam_audio.restype = None

		# bool set_reverb_settings(float room_size, float damping, float wet_level, float dry_level, float width)
		self.dll.set_reverb_settings.argtypes = [
			c_float,
			c_float,
			c_float,
			c_float,
			c_float,
		]
		self.dll.set_reverb_settings.restype = c_bool

		# bool process_sound(const float* input_buffer, int input_length, float angle_x, float angle_y, int16_t** output_buffer, int* output_length)
		self.dll.process_sound.argtypes = [
			POINTER(c_float),  # input_buffer
			c_int,  # input_length
			c_float,  # angle_x
			c_float,  # angle_y
			POINTER(POINTER(ctypes.c_int16)),  # output_buffer
			POINTER(c_int),  # output_length
		]
		self.dll.process_sound.restype = c_bool

		# bool apply_reverb(const int16_t* input_buffer, int input_length, int16_t** output_buffer, int* output_length)
		self.dll.apply_reverb.argtypes = [
			POINTER(ctypes.c_int16),  # input_buffer
			c_int,  # input_length
			POINTER(POINTER(ctypes.c_int16)),  # output_buffer
			POINTER(c_int),  # output_length
		]
		self.dll.apply_reverb.restype = c_bool

		# void free_output_sound(int16_t* buffer)
		self.dll.free_output_sound.argtypes = [POINTER(ctypes.c_int16)]
		self.dll.free_output_sound.restype = None

	def initialize(self, sample_rate=44100, frame_size=1024):
		"""Initialize Steam Audio with given parameters

		Args:
		    sample_rate: Audio sample rate in Hz (default: 44100)
		    frame_size: Audio frame size in samples (default: 1024)

		Returns:
		    bool: True if initialization successful, False otherwise
		"""
		if self.initialized:
			log.debug("Steam Audio already initialized")
			return True

		with _steam_audio_mutex:
			success = self.dll.initialize_steam_audio(sample_rate, frame_size)
			if success:
				self.initialized = True
				self.sample_rate = sample_rate
				self.frame_size = frame_size
				log.debug(
					f"Steam Audio initialized: {sample_rate}Hz, {frame_size} samples"
				)
			else:
				log.error("Failed to initialize Steam Audio")

		return success

	def cleanup(self):
		"""Cleanup Steam Audio resources"""
		if self.initialized:
			with _steam_audio_mutex:
				self.dll.cleanup_steam_audio()
				self.initialized = False
			log.debug("Steam Audio cleaned up")

	def set_reverb_settings(self, room_size, damping, wet_level, dry_level, width):
		"""Configure verblib reverb settings

		Args:
		    room_size: Room size (0.0 to 1.0)
		    damping: Damping amount (0.0 to 1.0,)
		    wet_level: Wet signal level (0.0 to 1.0, default)
		    dry_level: Dry signal level (0.0 to 1.0)
		    width: Stereo width (0.0 to 1.0)

		Returns:
		    bool: True if successful
		"""
		if not self.initialized:
			log.error("Steam Audio not initialized")
			return False

		with _steam_audio_mutex:
			success = self.dll.set_reverb_settings(
				room_size, damping, wet_level, dry_level, width
			)
			if success:
				log.debug(
					f"Reverb settings updated: room_size={room_size}, damping={damping}, wet_level={wet_level}, dry_level={dry_level}, width={width}"
				)
			else:
				log.error("Failed to set reverb settings")

		return success

	def process_sound(self, input_buffer, angle_x, angle_y):
		"""Process audio with 3D positioning (without reverb)

		Args:
		    input_buffer: list of float32 mono audio samples
		    angle_x: Horizontal angle in degrees (-90 to 90)
		    angle_y: Vertical angle in degrees (-90 to 90)

		Returns:
		    bytes: Stereo 16-bit audio samples as bytes, or None if failed
		"""
		if not self.initialized:
			log.error("Steam Audio not initialized")
			return None

		# Convert list to ctypes array
		input_array = (c_float * len(input_buffer))(*input_buffer)
		input_ptr = ctypes.cast(input_array, POINTER(c_float))
		input_length = len(input_buffer)

		# Prepare output parameters
		output_buffer_ptr = POINTER(ctypes.c_int16)()
		output_length = c_int()

		# Call the DLL function
		with _steam_audio_mutex:
			success = self.dll.process_sound(
				input_ptr,
				input_length,
				c_float(angle_x),
				c_float(angle_y),
				byref(output_buffer_ptr),
				byref(output_length),
			)

		if not success or not output_buffer_ptr:
			log.error("Failed to process sound")
			return None

		try:
			# Convert output to bytes
			output_samples = output_length.value
			if output_samples > 0:
				# Pack int16 samples into bytes
				result = struct.pack(
					f"<{output_samples}h",
					*[output_buffer_ptr[i] for i in range(output_samples)],
				)

				# Free the output buffer
				with _steam_audio_mutex:
					self.dll.free_output_sound(output_buffer_ptr)

				return result
			else:
				return b""

		except Exception as e:
			log.error(f"Error processing output buffer: {e}")
			# Make sure to free the buffer even if there's an error
			if output_buffer_ptr:
				with _steam_audio_mutex:
					self.dll.free_output_sound(output_buffer_ptr)
			return None

	def apply_reverb(self, input_buffer):
		"""Apply reverb to stereo 16-bit audio

		Args:
		    input_buffer: bytes of stereo 16-bit audio samples

		Returns:
		    bytes: Stereo 16-bit audio samples with reverb, or None if failed
		"""
		if not self.initialized:
			log.error("Steam Audio not initialized")
			return None

		# Convert bytes to int16 array
		import struct

		samples = struct.unpack(f"<{len(input_buffer) // 2}h", input_buffer)
		input_array = (ctypes.c_int16 * len(samples))(*samples)
		input_ptr = ctypes.cast(input_array, POINTER(ctypes.c_int16))
		input_length = len(samples)

		# Prepare output parameters
		output_buffer_ptr = POINTER(ctypes.c_int16)()
		output_length = c_int()

		# Call the DLL function
		with _steam_audio_mutex:
			success = self.dll.apply_reverb(
				input_ptr, input_length, byref(output_buffer_ptr), byref(output_length)
			)

		if not success or not output_buffer_ptr:
			log.error("Failed to apply reverb")
			return None

		try:
			# Convert output to bytes
			output_samples = output_length.value
			if output_samples > 0:
				# Pack int16 samples into bytes
				result = struct.pack(
					f"<{output_samples}h",
					*[output_buffer_ptr[i] for i in range(output_samples)],
				)

				# Free the output buffer
				with _steam_audio_mutex:
					self.dll.free_output_sound(output_buffer_ptr)

				return result
			else:
				return b""

		except Exception as e:
			log.error(f"Error processing reverb output buffer: {e}")
			# Make sure to free the buffer even if there's an error
			if output_buffer_ptr:
				with _steam_audio_mutex:
					self.dll.free_output_sound(output_buffer_ptr)
			return None

	def __del__(self):
		"""Cleanup when object is destroyed"""
		if hasattr(self, "initialized") and self.initialized:
			self.cleanup()


# Global instance for easy access
_steam_audio_instance = None


def get_steam_audio():
	"""Get the global Steam Audio instance"""
	global _steam_audio_instance
	if _steam_audio_instance is None:
		_steam_audio_instance = SteamAudio()
	return _steam_audio_instance


def initialize_steam_audio(sample_rate=44100, frame_size=1024):
	"""Initialize the global Steam Audio instance"""
	steam_audio = get_steam_audio()
	return steam_audio.initialize(sample_rate, frame_size)


def cleanup_steam_audio():
	"""Cleanup the global Steam Audio instance"""
	global _steam_audio_instance
	if _steam_audio_instance:
		_steam_audio_instance.cleanup()
		_steam_audio_instance = None
