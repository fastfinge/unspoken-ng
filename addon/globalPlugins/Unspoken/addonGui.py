"""The Unspoken-ng settings panel: five controls, sound theme first (spec §8)."""

import addonHandler
import config
import gui
import wx
from gui import nvdaControls

from . import settings


addonHandler.initTranslation()


def _labelled(values, labels):
	"""Pair each declared value with its label; a mismatch is drift, refused loudly."""
	if set(labels) != set(values):
		raise ValueError(f"labels {sorted(labels)} do not cover values {sorted(values)}")
	return tuple((value, labels[value]) for value in values)


ROLE_ANNOUNCEMENT_CHOICES = _labelled(
	settings.ROLE_ANNOUNCEMENT_VALUES,
	{
		# Translators: A role announcement mode: roles are announced by sounds instead of speech.
		"sounds": _("Sounds"),
		# Translators: A role announcement mode: roles are announced by sounds and spoken as well.
		"soundsAndSpeech": _("Sounds and speech"),
		# Translators: A role announcement mode: roles are spoken and no sounds play.
		"speechOnly": _("Speech only"),
	},
)

REVERB_CHOICES = _labelled(
	settings.REVERB_PRESETS,
	{
		# Translators: A reverb preset: role sounds play with no reverb.
		"none": _("None"),
		# Translators: A reverb preset: role sounds play as if in a small room.
		"smallRoom": _("Small room"),
		# Translators: A reverb preset: role sounds play as if in a medium-sized room.
		"mediumRoom": _("Medium room"),
		# Translators: A reverb preset: role sounds play as if in a hall.
		"hall": _("Hall"),
	},
)

_DEFAULT_THEME_ID = settings.DEFAULTS["theme"]


def build_theme_choices(discovered_themes):
	"""Return the (labels, IDs) pair for the sound theme combo box.

	Discovery is never empty: the sound theme library always offers the bundled
	default, even when the bundled default itself is unreadable, so there is
	nothing for the panel to synthesise (#66).
	"""

	return (
		[theme.name for theme in discovered_themes],
		[theme.id for theme in discovered_themes],
	)


def theme_index_for(theme_ids, selected_id):
	try:
		return theme_ids.index(selected_id)
	except ValueError:
		return 0


def theme_id_for_index(theme_ids, index):
	"""Return the sound theme ID shown at ``index``, or the bundled default.

	wx.Choice reports wx.NOT_FOUND (-1) when nothing is selected, which would
	otherwise select the last theme in the list by negative indexing.
	"""

	if 0 <= index < len(theme_ids):
		return theme_ids[index]
	return _DEFAULT_THEME_ID


def role_announcement_index_for(selected_value):
	values = settings.ROLE_ANNOUNCEMENT_VALUES
	try:
		return values.index(selected_value)
	except ValueError:
		return values.index(settings.DEFAULTS["roleAnnouncement"])


def role_announcement_value_for_index(index):
	values = settings.ROLE_ANNOUNCEMENT_VALUES
	if 0 <= index < len(values):
		return values[index]
	return settings.DEFAULTS["roleAnnouncement"]


def reverb_index_for(selected_value):
	values = settings.REVERB_PRESETS
	try:
		return values.index(selected_value)
	except ValueError:
		return values.index(settings.DEFAULTS["reverb"])


def reverb_value_for_index(index):
	values = settings.REVERB_PRESETS
	if 0 <= index < len(values):
		return values[index]
	return settings.DEFAULTS["reverb"]


def volume_slider_position(value):
	"""Clamp a stored volume percentage to the slider's 0-100 range.

	Garbage input means full volume, matching the playback conversion's
	failure philosophy.
	"""
	try:
		percent = int(value)
	except (TypeError, ValueError):
		return settings.DEFAULTS["volume"]
	return max(0, min(100, percent))


class SettingsPanel(gui.settingsDialogs.SettingsPanel):
	#: Bound by `register` onto the class NVDA constructs; see its docstring.
	_themes = None
	#: Bound by `register` alongside `_themes`; the panel's one voice to the
	#: running addon while it is open.
	_preview = None

	# Translators: The title of this add-on's category in NVDA's settings dialog.
	title = _("Unspoken-ng")

	def makeSettings(self, settingsSizer):
		self._priorSettings = self._readSettings()

		theme_labels, self._themeIds = build_theme_choices(self._themes.discover())
		role_labels = [label for value, label in ROLE_ANNOUNCEMENT_CHOICES]
		reverb_labels = [label for value, label in REVERB_CHOICES]

		sHelper = gui.guiHelper.BoxSizerHelper(self, sizer=settingsSizer)
		# Translators: The label of a combo box to choose the active sound theme.
		self.themeChoice = sHelper.addLabeledControl(
			_("Sound &theme:"),
			wx.Choice,
			choices=theme_labels,
		)
		self.themeChoice.SetSelection(
			theme_index_for(self._themeIds, self._priorSettings.get("theme"))
		)
		self.themeChoice.Bind(wx.EVT_CHOICE, self.onThemeChanged)

		# Translators: The label of a combo box to choose how control roles are announced.
		self.roleAnnouncementChoice = sHelper.addLabeledControl(
			_("&Role announcement:"),
			wx.Choice,
			choices=role_labels,
		)
		self.roleAnnouncementChoice.SetSelection(
			role_announcement_index_for(self._priorSettings.get("roleAnnouncement"))
		)

		# Translators: The label of a combo box to choose the reverb preset for role sounds.
		self.reverbChoice = sHelper.addLabeledControl(
			_("Re&verb:"),
			wx.Choice,
			choices=reverb_labels,
		)
		self.reverbChoice.SetSelection(
			reverb_index_for(self._priorSettings.get("reverb"))
		)
		self.reverbChoice.Bind(wx.EVT_CHOICE, self.onReverbChanged)

		# Translators: The label of a slider that sets the volume of role sounds.
		self.volumeSlider = sHelper.addLabeledControl(
			_("Sound vol&ume:"),
			nvdaControls.EnhancedInputSlider,
			minValue=0,
			maxValue=100,
		)
		self.volumeSlider.SetValue(
			volume_slider_position(self._priorSettings.get("volume"))
		)
		self.volumeSlider.Bind(wx.EVT_SLIDER, self.onVolumeChanged)

		# This silences role sounds only; whether roles are spoken during say all
		# stays governed by Role announcement (spec §8, deliberate per #10/#15).
		self.silenceDuringSayAllCheckBox = sHelper.addItem(
			wx.CheckBox(
				self,
				# Translators: The label of a checkbox to stop role sounds playing during say all.
				label=_("&Silence role sounds during say all"),
			)
		)
		self.silenceDuringSayAllCheckBox.SetValue(
			bool(self._priorSettings.get("silenceDuringSayAll"))
		)

		# What Cancel reverts to: the theme and reverb preset the panel opened
		# showing, which is what the user is hearing before touching anything.
		self._priorThemeId = self._selectedThemeId()
		self._priorReverbPreset = self._selectedReverbPreset()

	def onThemeChanged(self, event):
		self._preview.preview_theme(self._selectedThemeId())

	def onReverbChanged(self, event):
		self._preview.preview_reverb(self._selectedReverbPreset())

	# Volume is pulled from config on every play, so writing it through here is
	# the whole live preview: the next role sound, including those this dialog's
	# own focus changes fire, plays at the new level. `onDiscard` puts the saved
	# value back exactly as it does for every other key.
	def onVolumeChanged(self, event):
		try:
			config.conf["unspoken"]["volume"] = self.volumeSlider.GetValue()
		except KeyError:
			pass

	def onSave(self):
		section = config.conf["unspoken"]
		section["theme"] = self._selectedThemeId()
		section["roleAnnouncement"] = role_announcement_value_for_index(
			self.roleAnnouncementChoice.GetSelection()
		)
		section["reverb"] = self._selectedReverbPreset()
		section["volume"] = self.volumeSlider.GetValue()
		section["silenceDuringSayAll"] = self.silenceDuringSayAllCheckBox.IsChecked()
		# The theme and reverb preset are already live; saving must not re-apply
		# them. Saving is also what Apply does, and the dialog stays open
		# afterwards, so a later Cancel reverts to what was applied rather than
		# to what the panel opened with — as NVDA's own magnifier panel does.
		self._priorSettings = self._readSettings()
		self._priorThemeId = self._selectedThemeId()
		self._priorReverbPreset = self._selectedReverbPreset()

	def onDiscard(self):
		# wx can close an open combo box while the dialog is cancelling, firing a
		# late EVT_CHOICE that would re-apply the very selection being reverted.
		# NVDA's own driver settings panel unbinds for this reason.
		self.themeChoice.Unbind(wx.EVT_CHOICE)
		self.reverbChoice.Unbind(wx.EVT_CHOICE)
		self.volumeSlider.Unbind(wx.EVT_SLIDER)

		section = config.conf["unspoken"]
		for key, value in self._priorSettings.items():
			section[key] = value

		# What the panel opened, or last saved, showing — which is what the user
		# was hearing. Unconditional: whether putting it back costs anything is
		# the preview adapter's knowledge, not ours.
		self._preview.revert(self._priorThemeId, self._priorReverbPreset)

	def _readSettings(self):
		"""Snapshot the five settings, tolerating a config spec not yet registered.

		Keys that are missing are simply absent from the snapshot; the choice
		helpers then fall back to the spec §8 defaults, so the panel still
		builds if `GlobalPlugin` never got as far as registering the spec.
		"""

		snapshot = {}
		try:
			section = config.conf["unspoken"]
		except KeyError:
			return snapshot
		for key in settings.CONFIG_KEYS:
			try:
				snapshot[key] = section[key]
			except KeyError:
				pass
		return snapshot

	def _selectedThemeId(self):
		return theme_id_for_index(self._themeIds, self.themeChoice.GetSelection())

	def _selectedReverbPreset(self):
		return reverb_value_for_index(self.reverbChoice.GetSelection())


def register(*, themes, preview):
	"""Give NVDA a settings panel bound to the collaborators it needs.

	NVDA constructs the panel itself, from a class it holds in
	`categoryClasses`, so the panel's collaborators cannot arrive through
	`__init__`. They arrive as attributes of a class made for this registration
	-- which is also what makes unregistering sufficient teardown: the bindings
	live and die with the class object, there is nothing to restore by identity,
	and a plugin reload registers a new class rather than rebinding state under
	the old one's feet.

	`themes` is a `themes.SoundThemeLibrary`; the panel asks it for `discover()`
	and nothing else, and takes its answer as final.

	`preview` satisfies `preview.Preview` — preview a sound theme, preview a
	reverb preset, revert — and everything about debouncing or the cost of
	reapplying lives behind it, not in the panel.

	Returns the class to hand back to `unregister`.
	"""

	class UnspokenSettingsPanel(SettingsPanel):
		pass

	UnspokenSettingsPanel._themes = themes
	UnspokenSettingsPanel._preview = preview
	gui.settingsDialogs.NVDASettingsDialog.categoryClasses.append(UnspokenSettingsPanel)
	return UnspokenSettingsPanel


def unregister(panel_class):
	"""Take the panel back out of NVDA's settings dialog. Total."""
	try:
		gui.settingsDialogs.NVDASettingsDialog.categoryClasses.remove(panel_class)
	except Exception:
		pass
