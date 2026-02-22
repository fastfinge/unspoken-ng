# Unspoken user interface feedback for NVDA
# By Bryan Smart (bryansmart@bryansmart.com) and Austin Hicks (camlorn38@gmail.com)
# Updated to use Synthizer by Mason Armstrong (mason@masonasons.me)

import atexit
import os
import os.path
import sys
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
import nvwave
from synthDriverHandler import synthChanged

# openal_audio wraps soft_oal.dll via ctypes; import failure means DLL is missing.
# The HRTF config checkbox adjusts source gain by +0.25; it does not disable HRTF rendering.
try:
    from . import openal_audio
except ImportError as e:
    log.error(f"Failed to load OpenAL audio engine: {e}")
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
        self.audio_engine = openal_audio.get_openal_audio()
        if not self.audio_engine.initialize():
            log.error("Failed to initialize OpenAL audio engine")
            raise RuntimeError("OpenAL audio engine initialization failed")

        # Configure reverb settings
        self.audio_engine.set_reverb_settings(
            room_size=config.conf["unspoken"]["RoomSize"] / 100.0,
            damping=config.conf["unspoken"]["Damping"] / 100.0,
            wet_level=config.conf["unspoken"]["WetLevel"] / 100.0,
            dry_level=config.conf["unspoken"]["DryLevel"] / 100.0,
            width=config.conf["unspoken"]["Width"] / 100.0,
        )
        self.audio_engine.enable_reverb(config.conf["unspoken"]["Reverb"])

        self.make_sound_objects()

        # Initialize WavePlayer for audio output (stereo, 44100Hz, 16-bit)
        self.create_wave_player()
        # Hook to keep NVDA from announcing roles.
        self._NVDA_getSpeechTextForProperties = speech.speech.getPropertiesSpeech
        speech.speech.getPropertiesSpeech = self._hook_getSpeechTextForProperties

        self._previous_mouse_object = None
        self._last_played_object = None
        self._last_played_time = 0
        self._last_navigator_object = None
        self._wave_player_lock = threading.Lock()
        self._sound_generation = 0

        # Cached values to reduce main-thread blocking during sound playback.
        # Desktop dimensions change rarely (monitor changes); refresh every 5 seconds.
        # Volume changes only when synth changes; refresh in on_synthChanged.
        self._cached_desktop_size = None  # (max_x, max_y)
        self._desktop_cache_time = 0
        self._cached_volume = 1.0
        self._update_desktop_cache()
        self._update_volume_cache()

        # Lightweight timer to check arrow key navigation
        self._navigation_timer = wx.Timer()
        self._navigation_timer.Bind(wx.EVT_TIMER, self._onNavigationTimer)
        self._navigation_timer.Start(100)  # Check every 100ms

        # these are in degrees.
        self._display_width = 180.0
        self._display_height_min = -40.0
        self._display_height_magnitude = 50.0
        synthChanged.register(self.on_synthChanged)

    def create_wave_player(self):
        self.wave_player = nvwave.WavePlayer(
            channels=2,
            samplesPerSec=44100,
            bitsPerSample=16,
            outputDevice=config.conf["audio"]["outputDevice"],
        )

    def make_sound_objects(self):
        """Load sound files for OpenAL audio processing."""
        log.debug("Loading sound files for OpenAL audio engine", exc_info=True)
        for key, value in sound_files.items():
            path = os.path.join(UNSPOKEN_SOUNDS_PATH, value)
            log.debug("Loading " + path, exc_info=True)
            try:
                # Load WAV file and convert to float32 mono
                with wave.open(path, "rb") as wav_file:
                    frames = wav_file.readframes(wav_file.getnframes())
                    sample_width = wav_file.getsampwidth()
                    channels = wav_file.getnchannels()
                    sample_rate = wav_file.getframerate()

                    # Convert to float32 samples
                    if sample_width == 2:  # 16-bit
                        samples = struct.unpack(f"<{len(frames) // 2}h", frames)
                        float_samples = [s / 32768.0 for s in samples]
                    else:
                        log.error(f"Unsupported sample width: {sample_width}")
                        continue

                    # Convert to mono if stereo
                    if channels == 2:
                        # Source WAV files are mono or have identical left/right channels;
                        # if stereo, we take left channel only as it's sufficient
                        float_samples = [
                            float_samples[i] for i in range(0, len(float_samples), 2)
                        ]

                    sounds[key] = {"data": float_samples, "sample_rate": sample_rate}

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
                self._play_object_async(current_nav)
        except Exception:
            # Ignore any errors to avoid interrupting the timer
            pass

    def _compute_volume(self):
        if not config.conf["unspoken"]["volumeAdjust"]:
            return 1.0
        driver = speech.speech.getSynth()
        volume = getattr(driver, "volume", 100) / 100.0  # nvda reports as percent.
        volume = clamp(volume, 0.0, 1.0)
        return volume if not config.conf["unspoken"]["HRTF"] else volume + 0.25

    def _update_volume_cache(self):
        """Update cached volume value. Called at init and when synth changes."""
        self._cached_volume = self._compute_volume()

    def _update_desktop_cache(self):
        """Update cached desktop dimensions. Called at init and lazily refreshed."""
        desktop = NVDAObjects.api.getDesktopObject()
        self._cached_desktop_size = (desktop.location[2], desktop.location[3])
        self._desktop_cache_time = time.time()

    def _get_desktop_size(self):
        """Get desktop dimensions, refreshing cache if stale (>5 seconds)."""
        if time.time() - self._desktop_cache_time > 5.0:
            self._update_desktop_cache()
        return self._cached_desktop_size

    # CRITICAL: NVDA objects use COM single-threaded apartment model. All property
    # access (role, location, treeInterceptor.currentNVDAObject) MUST occur on the
    # main thread before spawning background threads. Moving these accesses to
    # background threads will cause COM threading violations and crash NVDA.
    # This is why we use two-phase architecture: extract params on main thread
    # (_extract_sound_params), then process audio on background thread (_play_sound_async).
    def _extract_sound_params(self, obj):
        """Extract NVDA object properties on main thread for sound playback.

        Returns tuple (role, angle_x, angle_y, volume) or None if sound should not play.
        Must be called from main thread before spawning background threads.
        """
        if config.conf["unspoken"]["noSounds"]:
            return None
        if config.conf["unspoken"]["sayAll"] and SayAllHandler.isRunning():
            return None

        curtime = time.time()
        if curtime - self._last_played_time < 0.1 and obj == self._last_played_object:
            return None

        self._last_played_object = obj
        self._last_played_time = curtime

        role = obj.role
        if role not in sounds:
            return None

        # Get coordinate bounds of desktop (cached, refreshed every 5 seconds).
        desktop_max_x, desktop_max_y = self._get_desktop_size()

        # Get location of the object.
        if obj.location != None and obj.treeInterceptor == None:
            obj_x = obj.location[0] + (obj.location[2] / 2.0)
            obj_y = obj.location[1] + (obj.location[3] / 2.0)
        elif (
            obj.treeInterceptor != None
            and obj.treeInterceptor.currentNVDAObject != None
            and obj.treeInterceptor.currentNVDAObject.location != None
        ):
            obj_x = obj.treeInterceptor.currentNVDAObject.location[0] + (
                obj.treeInterceptor.currentNVDAObject.location[2] / 2.0
            )
            obj_y = obj.treeInterceptor.currentNVDAObject.location[1] + (
                obj.treeInterceptor.currentNVDAObject.location[3] / 2.0
            )
        else:
            obj_x = desktop_max_x / 2.0
            obj_y = desktop_max_y / 2.0

        # Scale object position to audio display.
        angle_x = ((obj_x - desktop_max_x / 2.0) / desktop_max_x) * self._display_width

        percent = (desktop_max_y - obj_y) / desktop_max_y
        angle_y = self._display_height_magnitude * percent + self._display_height_min

        # Clamp angles to valid ranges.
        angle_x = clamp(angle_x, -90.0, 90.0)
        angle_y = clamp(angle_y, -90.0, 90.0)

        # Use cached volume (updated at init and when synth changes)
        return (role, angle_x, angle_y, self._cached_volume)

    def _play_object_async(self, obj):
        """Extract params and play sound in background thread."""
        params = self._extract_sound_params(obj)
        if params is not None:
            role, angle_x, angle_y, volume = params
            self._sound_generation += 1
            my_generation = self._sound_generation

            def play_async():
                try:
                    self._play_sound_async(role, angle_x, angle_y, volume, my_generation)
                except Exception:
                    pass

            threading.Thread(target=play_async, daemon=True).start()

    def _play_sound_async(self, role, angle_x, angle_y, volume, generation):
        """Process and play sound on background thread using pre-extracted parameters.

        Args:
                role: Control type role constant
                angle_x: Horizontal angle in degrees (-90 to 90)
                angle_y: Vertical angle in degrees (-90 to 90)
                volume: Pre-computed volume multiplier
                generation: Sound generation number for interrupt detection
        """
        if role not in sounds:
            return

        sound_data = sounds[role]
        audio_data = sound_data["data"]

        # Adjust volume (pre-computed on main thread)
        adjusted_audio = [sample * volume for sample in audio_data]

        # Process with OpenAL for HRTF spatialization and reverb
        final_audio = self.audio_engine.process_sound(
            adjusted_audio, angle_x, angle_y
        )
        if not final_audio:
            log.warn("Failed processing %r", role)
            return

        # Exit early if this sound has been superseded by a newer request
        if generation != self._sound_generation:
            return

        # Immediate interrupt - stop() is called WITHOUT lock to enable instant
        # interruption per NVDA WavePlayer design. Any thread can interrupt at
        # any time; the generation check above ensures only current sound stops.
        self.wave_player.stop()

        # Lock protects feed() from concurrent calls (WavePlayer requirement).
        # Second generation check catches threads that passed the pre-stop check
        # but queued at the lock while a newer sound was requested.
        with self._wave_player_lock:
            if generation != self._sound_generation:
                return
            self.wave_player.feed(final_audio)

    def event_gainFocus(self, obj, nextHandler):
        # Always call nextHandler first to avoid blocking navigation
        nextHandler()
        self._play_object_async(obj)

    def event_mouseMove(self, obj, nextHandler, x, y):
        # Always call nextHandler first
        nextHandler()

        if obj != self._previous_mouse_object:
            self._previous_mouse_object = obj
            self._play_object_async(obj)

    def terminate(self):
        # Stop the timer
        if hasattr(self, "_navigation_timer"):
            self._navigation_timer.Stop()

        # Restore original hooks
        speech.speech.getPropertiesSpeech = self._NVDA_getSpeechTextForProperties

        # Close WavePlayer
        if hasattr(self, "wave_player"):
            try:
                with self._wave_player_lock:
                    self.wave_player.close()
            except Exception:
                pass

        # Cleanup OpenAL audio engine
        if hasattr(self, "audio_engine"):
            self.audio_engine.cleanup()
        synthChanged.unregister(self.on_synthChanged)

    def on_synthChanged(self):
        self._update_volume_cache()
        with self._wave_player_lock:
            self.wave_player.close()
            self.create_wave_player()
