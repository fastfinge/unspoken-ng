# Unspoken user interface feedback for NVDA
# By Bryan Smart (bryansmart@bryansmart.com) and Austin Hicks (camlorn38@gmail.com)
# Updated to use Synthizer by Mason Armstrong (mason@masonasons.me)

import os
import os.path
import time
import threading
import wave
import struct
import globalPluginHandler
import NVDAObjects
import config
import speech
import controlTypes
from speech.sayAll import SayAllHandler
from logHandler import log
import gui
import api
import textInfos
import wx
from synthDriverHandler import synthChanged

# Import spatial audio engine
try:
	from . import steam_audio
except ImportError as e:
	log.error(f"Failed to load spatial audio engine: {e}")
	raise

UNSPOKEN_ROOT_PATH = os.path.abspath(os.path.dirname(__file__))


# Sounds

UNSPOKEN_SOUNDS_PATH = os.path.join(UNSPOKEN_ROOT_PATH, "sounds")

# Associate object roles to sounds.
sound_files = {
	controlTypes.ROLE_CHECKBOX: "checkbox.wav",
	controlTypes.ROLE_RADIOBUTTON: "radiobutton.wav",
	controlTypes.ROLE_STATICTEXT: "editabletext.wav",
	controlTypes.ROLE_EDITABLETEXT: "editabletext.wav",
	controlTypes.ROLE_BUTTON: "button.wav",
	controlTypes.ROLE_MENUBAR: "menuitem.wav",
	controlTypes.ROLE_MENUITEM: "menuitem.wav",
	controlTypes.ROLE_MENU: "menuitem.wav",
	controlTypes.ROLE_COMBOBOX: "combobox.wav",
	controlTypes.ROLE_LISTITEM: "listitem.wav",
	controlTypes.ROLE_GRAPHIC: "icon.wav",
	controlTypes.ROLE_LINK: "link.wav",
	controlTypes.ROLE_TREEVIEWITEM: "treeviewitem.wav",
	controlTypes.ROLE_TAB: "tab.wav",
	controlTypes.ROLE_TABCONTROL: "tab.wav",
	controlTypes.ROLE_SLIDER: "slider.wav",
	controlTypes.ROLE_DROPDOWNBUTTON: "combobox.wav",
	controlTypes.ROLE_CLOCK: "clock.wav",
	controlTypes.ROLE_ANIMATION: "icon.wav",
	controlTypes.ROLE_ICON: "icon.wav",
	controlTypes.ROLE_IMAGEMAP: "icon.wav",
	controlTypes.ROLE_RADIOMENUITEM: "radiobutton.wav",
	controlTypes.ROLE_RICHEDIT: "editabletext.wav",
	controlTypes.ROLE_SHAPE: "icon.wav",
	controlTypes.ROLE_TEAROFFMENU: "menuitem.wav",
	controlTypes.ROLE_TOGGLEBUTTON: "checkbox.wav",
	controlTypes.ROLE_CHART: "icon.wav",
	controlTypes.ROLE_DIAGRAM: "icon.wav",
	controlTypes.ROLE_DIAL: "slider.wav",
	controlTypes.ROLE_DROPLIST: "combobox.wav",
	controlTypes.ROLE_MENUBUTTON: "button.wav",
	controlTypes.ROLE_DROPDOWNBUTTONGRID: "button.wav",
	controlTypes.ROLE_HOTKEYFIELD: "editabletext.wav",
	controlTypes.ROLE_INDICATOR: "icon.wav",
	controlTypes.ROLE_SPINBUTTON: "slider.wav",
	controlTypes.ROLE_TREEVIEWBUTTON: "button.wav",
	controlTypes.ROLE_DESKTOPICON: "icon.wav",
	controlTypes.ROLE_PASSWORDEDIT: "editabletext.wav",
	controlTypes.ROLE_CHECKMENUITEM: "checkbox.wav",
	controlTypes.ROLE_SPLITBUTTON: "splitbutton.wav",
}

sounds = dict()  # For holding instances in RAM.


# taken from Stackoverflow. Don't ask.
def clamp(my_value, min_value, max_value):
	return max(min(my_value, max_value), min_value)


class GlobalPlugin(globalPluginHandler.GlobalPlugin):
	def __init__(self, *args, **kwargs):
		super(GlobalPlugin, self).__init__(*args, **kwargs)
		from . import addonGui

		gui.settingsDialogs.NVDASettingsDialog.categoryClasses.append(
			addonGui.SettingsPanel
		)
		config.conf.spec["unspoken"] = {
			"sayAll": "boolean(default=False)",
			"speakRoles": "boolean(default=False)",
			"noSounds": "boolean(default=False)",
			"HRTF": "boolean(default=True)",
			"volumeAdjust": "boolean(default=True)",
			"Reverb": "boolean(default=True)",
			"RoomSize": "integer(default=10, min=0, max=100)",
			"Damping": "integer(default=100, min=0, max=100)",
			"WetLevel": "integer(default=9, min=0, max=100)",
			"DryLevel": "integer(default=30, min=0, max=100)",
			"Width": "integer(default=100, min=0, max=100)",
		}
		log.debug("Initializing OpenAL audio engine", exc_info=True)
		self.steam_audio = steam_audio.get_steam_audio()
		if not self.steam_audio.initialize(
			output_device=config.conf["audio"].get("outputDevice")
		):
			log.error("Failed to initialize OpenAL audio engine")
			raise RuntimeError("OpenAL initialization failed")

		# Configure reverb settings
		self.steam_audio.set_reverb_settings(
			room_size=config.conf["unspoken"]["RoomSize"] / 100.0,
			damping=config.conf["unspoken"]["Damping"] / 100.0,
			wet_level=config.conf["unspoken"]["WetLevel"] / 100.0,
			dry_level=config.conf["unspoken"]["DryLevel"] / 100.0,
			width=config.conf["unspoken"]["Width"] / 100.0,
		)

		self.make_sound_objects()
		# Hook to keep NVDA from announcing roles.
		self._NVDA_getSpeechTextForProperties = speech.speech.getPropertiesSpeech
		speech.speech.getPropertiesSpeech = self._hook_getSpeechTextForProperties

		self._previous_mouse_object = None
		self._last_played_object = None
		self._last_played_time = 0
		self._last_navigator_object = None

		# Lightweight timer to check arrow key navigation
		self._navigation_timer = wx.Timer()
		self._navigation_timer.Bind(wx.EVT_TIMER, self._onNavigationTimer)
		self._navigation_timer.Start(100)  # Check every 100ms

		# these are in degrees.
		self._display_width = 180.0
		self._display_height_min = -40.0
		self._display_height_magnitude = 50.0
		synthChanged.register(self.on_synthChanged)

	def make_sound_objects(self):
		"""Load sound files for the spatial audio engine."""
		log.debug("Loading sound files for spatial audio", exc_info=True)
		for key, value in sound_files.items():
			path = os.path.join(UNSPOKEN_SOUNDS_PATH, value)
			log.debug("Loading " + path, exc_info=True)
			try:
				# Load WAV file and convert to mono PCM bytes
				with wave.open(path, "rb") as wav_file:
					frames = wav_file.readframes(wav_file.getnframes())
					sample_width = wav_file.getsampwidth()
					channels = wav_file.getnchannels()
					sample_rate = wav_file.getframerate()

					if sample_width != 2:
						log.error(f"Unsupported sample width: {sample_width}")
						continue

					if channels == 2:
						samples = struct.unpack(f"<{len(frames) // 2}h", frames)
						mono_samples = []
						for i in range(0, len(samples), 2):
							try:
								left = samples[i]
								right = samples[i + 1]
							except IndexError:
								break
							mono_samples.append(int((left + right) / 2))
						frames = struct.pack(f"<{len(mono_samples)}h", *mono_samples)
					elif channels != 1:
						log.error(f"Unsupported channel count: {channels}")
						continue

					sounds[key] = {
						"path": path,
						"pcm_data": frames,
						"sample_rate": sample_rate,
					}

			except Exception as e:
				log.error(f"Failed to load {path}: {e}")

	def shouldNukeRoleSpeech(self):
		if config.conf["unspoken"]["sayAll"] and SayAllHandler.isRunning():
			return False
		if config.conf["unspoken"]["speakRoles"]:
			return False
		return True

	def _hook_getSpeechTextForProperties(
		self, reason=NVDAObjects.controlTypes.OutputReason.QUERY, *args, **kwargs
	):
		role = kwargs.get("role", None)
		if role:
			if role in sounds and self.shouldNukeRoleSpeech():
				# NVDA will not announce roles if we put it in as _role.
				kwargs["_role"] = kwargs["role"]
				del kwargs["role"]
		return self._NVDA_getSpeechTextForProperties(reason, *args, **kwargs)

	def _onNavigationTimer(self, event):
		"""Timer to check navigator object changes without blocking"""
		try:
			current_nav = api.getNavigatorObject()
			if current_nav and current_nav != self._last_navigator_object:
				self._last_navigator_object = current_nav
				# Play sound in separate thread to avoid blocking
				import threading

				def play_async():
					try:
						self.play_object(current_nav)
					except:
						pass

				threading.Thread(target=play_async, daemon=True).start()
		except:
			# Ignore any errors to avoid interrupting the timer
			pass

	def _compute_volume(self):
		if not config.conf["unspoken"]["volumeAdjust"]:
			return 1.0
		driver = speech.speech.getSynth()
		volume = getattr(driver, "volume", 100) / 100.0  # nvda reports as percent.
		volume = clamp(volume, 0.0, 1.0)
		return volume if not config.conf["unspoken"]["HRTF"] else volume + 0.25

	def play_object(self, obj):
		if config.conf["unspoken"]["noSounds"]:
			return
		if config.conf["unspoken"]["sayAll"] and SayAllHandler.isRunning():
			return
		curtime = time.time()
		if curtime - self._last_played_time < 0.1 and obj is self._last_played_object:
			return
		self._last_played_object = obj
		self._last_played_time = curtime
		role = obj.role
		if role in sounds:
			# Get coordinate bounds of desktop.
			desktop = NVDAObjects.api.getDesktopObject()
			desktop_max_x = desktop.location[2]
			desktop_max_y = desktop.location[3]
			# Get location of the object.
			if obj.location != None and obj.treeInterceptor == None:
				# Object has a location. Get its center.
				obj_x = obj.location[0] + (obj.location[2] / 2.0)
				obj_y = obj.location[1] + (obj.location[3] / 2.0)
			elif obj.treeInterceptor != None and obj.treeInterceptor.currentNVDAObject.location != None:
				# Object is a browser object. Get its center.
				obj_x = obj.treeInterceptor.currentNVDAObject.location[0] + (obj.treeInterceptor.currentNVDAObject.location[2] / 2.0)
				obj_y = obj.treeInterceptor.currentNVDAObject.location[1] + (obj.treeInterceptor.currentNVDAObject.location[3] / 2.0)

			else:
				# Objects without location are assumed in the center of the screen.
				obj_x = desktop_max_x / 2.0
				obj_y = desktop_max_y / 2.0
			# Scale object position to audio display.
			angle_x = (
				(obj_x - desktop_max_x / 2.0) / desktop_max_x
			) * self._display_width
			# angle_y is a bit more involved.
			percent = (desktop_max_y - obj_y) / desktop_max_y
			angle_y = (
				self._display_height_magnitude * percent + self._display_height_min
			)
			# clamp these to Libaudioverse's internal ranges.
			angle_x = clamp(angle_x, -90.0, 90.0)
			angle_y = clamp(angle_y, -90.0, 90.0)
			# Process audio with spatial engine
			if role in sounds:
				sound_data = sounds[role]
				volume = self._compute_volume()
				self.steam_audio.stop_all()
				success = self.steam_audio.play_sound(
					role,
					sound_data["pcm_data"],
					sound_data["sample_rate"],
					angle_x,
					angle_y,
					volume=volume,
					enable_reverb=config.conf["unspoken"]["Reverb"],
				)
				if not success:
					log.warn("Failed playing sound for %r", role)

	def event_gainFocus(self, obj, nextHandler):
		# Always call nextHandler first to avoid blocking navigation
		nextHandler()
		# Play sound asynchronously to avoid blocking
		import threading

		def play_async():
			try:
				self.play_object(obj)
			except:
				pass

		threading.Thread(target=play_async, daemon=True).start()

	def event_mouseMove(self, obj, nextHandler, x, y):
		# Always call nextHandler first
		nextHandler()
		# Handle mouse move in separate thread
		if obj != self._previous_mouse_object:
			self._previous_mouse_object = obj
			import threading

			def play_async():
				try:
					self.play_object(obj)
				except:
					pass

			threading.Thread(target=play_async, daemon=True).start()

	def terminate(self):
		# Stop the timer
		if hasattr(self, "_navigation_timer"):
			self._navigation_timer.Stop()

		# Restore original hooks
		speech.speech.getPropertiesSpeech = self._NVDA_getSpeechTextForProperties

		# Cleanup spatial audio engine
		if hasattr(self, "steam_audio"):
			try:
				self.steam_audio.stop_all()
			except Exception:
				pass
			self.steam_audio.cleanup()
		synthChanged.unregister(self.on_synthChanged)

	def on_synthChanged(self):
		try:
			self.steam_audio.reinitialize(
				output_device=config.conf["audio"].get("outputDevice")
			)
		except Exception as e:
			log.error(f"Failed to reinitialize OpenAL: {e}")
