import wx
import config
import gui
from gui import settingsDialogs, guiHelper, NVDASettingsDialog


class SettingsPanel(gui.settingsDialogs.SettingsPanel):
	title = "Unspoken"

	def makeSettings(self, settingsSizer):
		settingsSizer = guiHelper.BoxSizerHelper(self, sizer=settingsSizer)
		self.sayAllCheckBox = settingsSizer.addItem(
			wx.CheckBox(self, label="&Play sounds during say all")
		)
		self.sayAllCheckBox.SetValue(
			(True if config.conf["unspoken"]["sayAll"] == False else False)
		)
		self.speakRolesCheckBox = settingsSizer.addItem(
			wx.CheckBox(self, label="&Speak object roles")
		)
		self.speakRolesCheckBox.SetValue(config.conf["unspoken"]["speakRoles"])
		self.HRTFCheckBox = settingsSizer.addItem(
			wx.CheckBox(self, label="Use &HRTF (3D Sound)")
		)
		self.HRTFCheckBox.SetValue(config.conf["unspoken"]["HRTF"])
		self.ReverbCheckBox = settingsSizer.addItem(
			wx.CheckBox(self, label="Use &Reverb")
		)
		self.ReverbCheckBox.SetValue(config.conf["unspoken"]["Reverb"])
		self.ReverbCheckBox.Bind(wx.EVT_CHECKBOX, self.onReverbSettingChanged)

		# EFX reverb settings
		self.RoomSizeSliderLabel = settingsSizer.addItem(
			wx.StaticText(self, label="Room Size (0-100)")
		)
		self.RoomSizeSlider = settingsSizer.addItem(
			wx.Slider(
				self,
				value=config.conf["unspoken"]["RoomSize"],
				minValue=0,
				maxValue=100,
			)
		)
		self.RoomSizeSlider.Bind(wx.EVT_SLIDER, self.onReverbSettingChanged)

		self.DampingSliderLabel = settingsSizer.addItem(
			wx.StaticText(self, label="Damping (0-100)")
		)
		self.DampingSlider = settingsSizer.addItem(
			wx.Slider(
				self, value=config.conf["unspoken"]["Damping"], minValue=0, maxValue=100
			)
		)
		self.DampingSlider.Bind(wx.EVT_SLIDER, self.onReverbSettingChanged)

		self.WetLevelSliderLabel = settingsSizer.addItem(
			wx.StaticText(self, label="Wet Level (0-100)")
		)
		self.WetLevelSlider = settingsSizer.addItem(
			wx.Slider(
				self,
				value=config.conf["unspoken"]["WetLevel"],
				minValue=0,
				maxValue=100,
			)
		)
		self.WetLevelSlider.Bind(wx.EVT_SLIDER, self.onReverbSettingChanged)

		self.DryLevelSliderLabel = settingsSizer.addItem(
			wx.StaticText(self, label="Dry Level (0-100)")
		)
		self.DryLevelSlider = settingsSizer.addItem(
			wx.Slider(
				self,
				value=config.conf["unspoken"]["DryLevel"],
				minValue=0,
				maxValue=100,
			)
		)
		self.DryLevelSlider.Bind(wx.EVT_SLIDER, self.onReverbSettingChanged)

		self.WidthSliderLabel = settingsSizer.addItem(
			wx.StaticText(self, label="Width (0-100)")
		)
		self.WidthSlider = settingsSizer.addItem(
			wx.Slider(
				self, value=config.conf["unspoken"]["Width"], minValue=0, maxValue=100
			)
		)
		self.WidthSlider.Bind(wx.EVT_SLIDER, self.onReverbSettingChanged)

		self.noSoundsCheckBox = settingsSizer.addItem(
			wx.CheckBox(self, label="&play sounds for roles (Enable Add-On)")
		)
		self.noSoundsCheckBox.SetValue(
			(True if config.conf["unspoken"]["noSounds"] == False else False)
		)
		self.volumeCheckBox = settingsSizer.addItem(
			wx.CheckBox(self, label="Automatically adjust sounds with speech &volume")
		)
		self.volumeCheckBox.SetValue(config.conf["unspoken"]["volumeAdjust"])
		self.unspoken_copy = config.conf["unspoken"].copy()

	def onReverbSettingChanged(self, event):
		"""Push slider values to the live OpenALLoopback instance.
		enable_reverb() is called before set_reverb_settings() so the EFX tail
		frame count is only computed when reverb is active."""
		try:
			# Import here to avoid circular imports
			from . import openal_audio

			openal_audio_instance = openal_audio.get_openal_audio()
			if openal_audio_instance and openal_audio_instance.initialized:
				config.conf["unspoken"]["Reverb"] = self.ReverbCheckBox.IsChecked()
				openal_audio_instance.enable_reverb(self.ReverbCheckBox.IsChecked())
				openal_audio_instance.set_reverb_settings(
					room_size=self.RoomSizeSlider.GetValue() / 100.0,
					damping=self.DampingSlider.GetValue() / 100.0,
					wet_level=self.WetLevelSlider.GetValue() / 100.0,
					dry_level=self.DryLevelSlider.GetValue() / 100.0,
					width=self.WidthSlider.GetValue() / 100.0,
				)
		except ImportError:
			pass

	def postInit(self):
		self.sayAllCheckBox.SetFocus()

	def onSave(self):
		if (
			not self.noSoundsCheckBox.IsChecked()
			and not self.speakRolesCheckBox.IsChecked()
		):
			gui.messageBox(
				"Disabling both sounds and  speaking is not allowed. NVDA will not say roles like button and checkbox, and sounds won't play either. Please change one of these settings",
				"Error",
			)
			return
		config.conf["unspoken"]["sayAll"] = not self.sayAllCheckBox.IsChecked()
		config.conf["unspoken"]["speakRoles"] = self.speakRolesCheckBox.IsChecked()

		config.conf["unspoken"]["HRTF"] = self.HRTFCheckBox.IsChecked()
		config.conf["unspoken"]["Reverb"] = self.ReverbCheckBox.IsChecked()

		# Save EFX reverb settings
		config.conf["unspoken"]["RoomSize"] = self.RoomSizeSlider.GetValue()
		config.conf["unspoken"]["Damping"] = self.DampingSlider.GetValue()
		config.conf["unspoken"]["WetLevel"] = self.WetLevelSlider.GetValue()
		config.conf["unspoken"]["DryLevel"] = self.DryLevelSlider.GetValue()
		config.conf["unspoken"]["Width"] = self.WidthSlider.GetValue()
		config.conf["unspoken"]["noSounds"] = not self.noSoundsCheckBox.IsChecked()
		config.conf["unspoken"]["volumeAdjust"] = self.volumeCheckBox.IsChecked()

	def update_reverb_from_config(self):
		# Update OpenAL EFX reverb settings
		try:
			from . import openal_audio

			openal_audio_instance = openal_audio.get_openal_audio()
			if openal_audio_instance and openal_audio_instance.initialized:
				openal_audio_instance.enable_reverb(config.conf["unspoken"]["Reverb"])
				openal_audio_instance.set_reverb_settings(
					room_size=config.conf["unspoken"]["RoomSize"] / 100.0,
					damping=config.conf["unspoken"]["Damping"] / 100.0,
					wet_level=config.conf["unspoken"]["WetLevel"] / 100.0,
					dry_level=config.conf["unspoken"]["DryLevel"] / 100.0,
					width=config.conf["unspoken"]["Width"] / 100.0,
				)
		except ImportError:
			pass

	def onDiscard(self):
		for k, v in self.unspoken_copy.items():
			config.conf["unspoken"][k] = v
		self.update_reverb_from_config()
