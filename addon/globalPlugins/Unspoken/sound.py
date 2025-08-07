import time
import math
import random

_synthizer_initialized = False
_synthizer_module = None
context = None
reverb = None


# Sound buffer class, for handling synthizer sound buffers.
class sound_buffer(object):
	def __init__(self, filename, buffer):
		self.filename = filename
		self.buffer = buffer

	def destroy(self):
		self.buffer.destroy()


# Sound buffer manager class, for passing already loaded sound buffers if they exist, else creating new ones.
class sound_buffer_manager(object):
	def __init__(self):
		self.buffers = []

	def buffer(self, filename):
		for i in self.buffers:
			if i.filename == filename:
				# Our sound is already loaded into a buffer, so return it.
				return i.buffer
		# Our sound is not loaded, so load it, add the buffer to the buffers list and return it.
		synthizer = get_synthizer()
		tmp = synthizer.Buffer.from_file(filename)
		self.buffers.append(sound_buffer(filename, tmp))
		return tmp

	def destroy(self, buffer):
		# Not used anywhere yet, todo.
		buffer.destroy()
		self.buffers.remove(self)


gsbm = sound_buffer_manager()


# The actual sound3D class.
class sound3d(object):
	def __init__(self, type, context):
		ensure_synthizer_initialized()
		self.context = context
		self.type = type
		self.vol = 0
		self.handle = 0
		self.paused = False
		self.filename = ""
		self.buffer = None
		self.source = None
		self.generator = None
		self.length = None

	def load(self, filename):
		if self.handle != None:
			self.close()
		if isinstance(filename, str):  # Asume path on disk.
			synthizer = get_synthizer()
			self.generator = synthizer.BufferGenerator(self.context)
			self.buffer = gsbm.buffer(filename)
			self.length = self.buffer.get_length_in_seconds()
			self.generator.buffer.value = self.buffer
			if self.type == "3d":
				self.source = synthizer.Source3D(self.context)
			elif self.type == "direct":
				self.source = synthizer.DirectSource(self.context)
			elif self.type == "panned":
				self.source = synthizer.PannedSource(self.context)
		if self.is_active:
			self.filename = filename
			return True
		return False

	def close(self):
		if not self.is_active():
			return False
		self.source.remove_generator(self.generator)
		self.source.destroy()
		self.generator.destroy()
		self.source = None
		self.buffer = None
		self.generator = None
		self.filename = ""

	def play(self):
		if not self.is_active():
			return False
		self.generator.looping.value = False
		self.source.add_generator(self.generator)
		self.paused = False
		self.looping = False
		return True

	def play_looped(self):
		if not self.is_active():
			return False
		self.generator.looping.value = True
		self.source.add_generator(self.generator)
		self.paused = False
		self.looping = True
		return True

	def play_wait(self):
		if not self.is_active():
			return False
		self.generator.looping = False
		self.play()
		while self.is_playing():
			time.sleep(0.005)
		return True

	def is_playing(self):
		return self.position <= self.length - 0.005

	def pause(self):
		if not self.is_active():
			return False
		self.source.remove_generator(self.generator)
		self.paused = True

	def stop(self):
		if not self.is_active():
			return False
		self.source.remove_generator(self.generator)
		self.generator.playback_position.value = 0
		self.paused = False

	def get_position(self):
		if not self.is_active():
			return -1
		return self.generator.playback_position

	def set_position(self, position):
		if not self.is_active():
			return False
		self.generator.playback_position = position
		return True

	def get_volume(self):
		if not self.is_active():
			return 0
		return self.vol

	def set_volume(self, volume):
		if not self.is_active():
			return False
		if volume > 0:
			volume = 0
		self.vol = volume
		# using formula from the example code to convert to DB
		self.source.gain = 10 ** (volume / 20)

	def get_pitch(self):
		if not self.is_active():
			return 100
		return self.generator.pitch_bend * 100

	def set_pitch(self, pitch):
		if not self.is_active():
			return False
		freq = float(pitch) / 100
		if freq > 10:
			freq = 10
		if freq < 0.1:
			freq = 0.1
		self.generator.pitch_bend = freq

	def get_pan(self):
		if not self.is_active():
			return 0
		if self.type == "panned":
			return int(self.source.panning_scaler * 100)
		else:
			return 0

	def set_pan(self, pan):
		if not self.is_active():
			return False
		if self.type != "panned":
			return False
		self.source.panning_scalar = pan / 100

	def is_active(self):
		if self.source != None:
			try:
				pb = self.generator.playback_position.value
			except:
				return False
			return True
		return False

	pan = property(get_pan, set_pan)
	pitch = property(get_pitch, set_pitch)
	volume = property(get_volume, set_volume)
	position = property(get_position, set_position)
	active = property(is_active)

	# Fade a sound.
	def fade(self, dest_volume, time_per_fade):
		while self.volume != dest_volume:
			if self.volume < dest_volume:
				self.volume = self.volume + 1
			if self.volume > dest_volume:
				self.volume = self.volume - 1
			time.sleep(time_per_fade / 1000)
		self.stop()
		return True


def initialize_synthizer():
	global _synthizer_initialized, _synthizer_module, context, reverb
	if not _synthizer_initialized:
		from . import synthizer as _synthizer_module

		_synthizer_module.initialize()
		context = _synthizer_module.Context()
		reverb = _synthizer_module.GlobalFdnReverb(context)
		_synthizer_initialized = True


def ensure_synthizer_initialized():
	if not _synthizer_initialized:
		initialize_synthizer()


def get_synthizer():
	"""Ottieni il modulo synthizer, inizializzandolo se necessario"""
	ensure_synthizer_initialized()
	return _synthizer_module
