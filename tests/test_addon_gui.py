"""Tests for the settings panel.

The panel is exercised for real: wx and NVDA's gui helpers are replaced with
fakes that keep the parts NVDA's contract depends on — label association
through ``BoxSizerHelper``, ``EVT_CHOICE`` bindings, and combo selections — so
``makeSettings``, ``onSave`` and ``onDiscard`` run as written rather than being
skipped as "untestable".
"""

import importlib
import inspect
import itertools
import sys
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

import preview
import settings as addon_settings


UNSPOKEN_DIR = (
	Path(__file__).parents[1]
	/ "addon"
	/ "globalPlugins"
	/ "Unspoken"
)

_package_counter = itertools.count()


def _default_conf():
	return dict(addon_settings.DEFAULTS)


class _Conf(dict):
	def __init__(self, unspoken=None, spec=None):
		super().__init__()
		if unspoken is not None:
			self["unspoken"] = unspoken
		self.spec = {} if spec is None else spec


class _FakeLibrary:
	"""The panel's whole view of the sound theme library: one call."""

	def __init__(self, themes):
		self.themes = list(themes)
		self.discoveries = 0

	def discover(self):
		self.discoveries += 1
		return list(self.themes)


class _RecordingPreview:
	"""The second adapter: what the panel asked for, in order."""

	def __init__(self):
		self.calls = []

	def preview_theme(self, theme_id):
		self.calls.append(("theme", theme_id))

	def preview_reverb(self, preset):
		self.calls.append(("reverb", preset))

	def revert(self, theme_id, preset):
		self.calls.append(("revert", theme_id, preset))


class _FakeChoice:
	"""Stands in for wx.Choice: a selection, and event handlers by event type."""

	def __init__(self, parent, choices=None):
		self.parent = parent
		self.choices = list(choices or [])
		self.selection = -1
		self.handlers = {}

	def SetSelection(self, index):
		self.selection = index

	def GetSelection(self):
		return self.selection

	def Bind(self, event, handler):
		self.handlers[event] = handler

	def Unbind(self, event):
		self.handlers.pop(event, None)

	def choose(self, index, event):
		"""Select ``index`` the way a keyboard user does, then fire the event."""
		self.selection = index
		handler = self.handlers.get(event)
		assert handler is not None, "control has no handler bound for this event"
		handler(None)


class _FakeCheckBox:
	def __init__(self, parent, label=""):
		self.parent = parent
		self.label = label
		self.value = False

	def SetValue(self, value):
		self.value = value

	def IsChecked(self):
		return self.value


class _FakeBoxSizerHelper:
	"""Records what NVDA's guiHelper would build, keeping label association."""

	def __init__(self, parent, sizer=None):
		self.parent = parent
		self.sizer = sizer
		self.labeledControls = []
		self.items = []
		# So a test can inspect what the panel built.
		parent._testHelper = self

	def addLabeledControl(self, labelText, wxCtrlClass, **kwargs):
		control = wxCtrlClass(self.parent, **kwargs)
		self.labeledControls.append((labelText, control))
		self.items.append(control)
		return control

	def addItem(self, item, **kwargs):
		self.items.append(item)
		return item


@contextmanager
def _addon_gui(conf=None):
	"""Import a fresh copy of addonGui against stubbed NVDA modules."""

	conf = _Conf(_default_conf()) if conf is None else conf
	package_name = f"_test_unspoken_package_{next(_package_counter)}"
	module_names = ("wx", "gui", "config", "addonHandler")
	original_modules = {name: sys.modules.get(name) for name in module_names}

	wx_stub = ModuleType("wx")
	wx_stub.Choice = _FakeChoice
	wx_stub.CheckBox = _FakeCheckBox
	wx_stub.EVT_CHOICE = object()

	gui_stub = ModuleType("gui")
	gui_stub.settingsDialogs = SimpleNamespace(
		SettingsPanel=object,
		NVDASettingsDialog=SimpleNamespace(categoryClasses=[]),
	)
	gui_stub.guiHelper = SimpleNamespace(BoxSizerHelper=_FakeBoxSizerHelper)

	config_stub = ModuleType("config")
	config_stub.conf = conf

	addon_handler_stub = ModuleType("addonHandler")

	def initTranslation():
		# NVDA installs the add-on's gettext function into the caller's globals.
		caller = inspect.currentframe().f_back
		try:
			caller.f_globals["_"] = lambda text: text
		finally:
			del caller

	addon_handler_stub.initTranslation = initTranslation

	package = ModuleType(package_name)
	package.__path__ = [str(UNSPOKEN_DIR)]

	sys.modules["wx"] = wx_stub
	sys.modules["gui"] = gui_stub
	sys.modules["config"] = config_stub
	sys.modules["addonHandler"] = addon_handler_stub
	sys.modules[package_name] = package

	try:
		module = importlib.import_module(f"{package_name}.addonGui")
		module._test_wx = wx_stub
		module._test_conf = conf
		yield module
	finally:
		for name in tuple(sys.modules):
			if name == package_name or name.startswith(f"{package_name}."):
				sys.modules.pop(name, None)
		for name, original in original_modules.items():
			if original is None:
				sys.modules.pop(name, None)
			else:
				sys.modules[name] = original


@pytest.fixture
def addon_gui():
	with _addon_gui() as module:
		yield module


def _theme(theme_id, name):
	return SimpleNamespace(id=theme_id, name=name)


_ONE_BUNDLED_THEME = (_theme("default", "Default"),)


def _make_panel(module, themes=_ONE_BUNDLED_THEME, preview=None):
	"""Build the panel against a fixed set of discovered themes."""

	preview = preview or _RecordingPreview()
	cls = module.register(themes=_FakeLibrary(themes), preview=preview)
	panel = cls.__new__(cls)
	panel.makeSettings(object())
	return panel


# --- pure helpers ---------------------------------------------------------


def test_build_theme_choices_uses_names_and_ids(addon_gui):
	labels, ids = addon_gui.build_theme_choices(
		[_theme("default", "Default theme"), _theme("retro", "Retro")]
	)

	assert labels == ["Default theme", "Retro"]
	assert ids == ["default", "retro"]


@pytest.mark.parametrize(
	("selected_id", "expected"),
	[
		("default", 0),
		("retro", 1),
		("missing", 0),
		(None, 0),
	],
)
def test_theme_index_for(addon_gui, selected_id, expected):
	assert addon_gui.theme_index_for(["default", "retro"], selected_id) == expected


@pytest.mark.parametrize(
	("index", "expected_id"),
	[
		(0, "default"),
		(1, "retro"),
		# wx.Choice reports -1 when nothing is selected; negative indexing would
		# silently pick the last theme.
		(-1, "default"),
		(2, "default"),
	],
)
def test_theme_id_for_index(addon_gui, index, expected_id):
	assert addon_gui.theme_id_for_index(["default", "retro"], index) == expected_id


@pytest.mark.parametrize(
	("value", "expected_index"),
	[
		("sounds", 0),
		("soundsAndSpeech", 1),
		("speechOnly", 2),
		("unknown", 0),
		(None, 0),
	],
)
def test_role_announcement_index_for(addon_gui, value, expected_index):
	assert addon_gui.role_announcement_index_for(value) == expected_index


@pytest.mark.parametrize(
	("index", "expected_value"),
	[
		(0, "sounds"),
		(1, "soundsAndSpeech"),
		(2, "speechOnly"),
		(-1, "sounds"),
		(99, "sounds"),
	],
)
def test_role_announcement_value_for_index(addon_gui, index, expected_value):
	assert addon_gui.role_announcement_value_for_index(index) == expected_value


@pytest.mark.parametrize(
	("value", "expected_index"),
	[
		("none", 0),
		("smallRoom", 1),
		("mediumRoom", 2),
		("hall", 3),
		("unknown", 1),
		(None, 1),
	],
)
def test_reverb_index_for(addon_gui, value, expected_index):
	assert addon_gui.reverb_index_for(value) == expected_index


@pytest.mark.parametrize(
	("index", "expected_value"),
	[
		(0, "none"),
		(1, "smallRoom"),
		(2, "mediumRoom"),
		(3, "hall"),
		(-1, "smallRoom"),
		(99, "smallRoom"),
	],
)
def test_reverb_value_for_index(addon_gui, index, expected_value):
	assert addon_gui.reverb_value_for_index(index) == expected_value


def test_panel_choices_offer_exactly_the_declared_values(addon_gui):
	assert tuple(v for v, _ in addon_gui.ROLE_ANNOUNCEMENT_CHOICES) == addon_settings.ROLE_ANNOUNCEMENT_VALUES
	assert tuple(v for v, _ in addon_gui.REVERB_CHOICES) == addon_settings.REVERB_PRESETS


# --- the panel ------------------------------------------------------------


def test_panel_builds_the_four_spec_controls_in_order(addon_gui):
	panel = _make_panel(addon_gui)
	helper = panel._testHelper

	# Every combo box goes through addLabeledControl, which is what associates a
	# label with the control so NVDA announces it.
	assert helper.labeledControls == [
		("Sound &theme:", panel.themeChoice),
		("&Role announcement:", panel.roleAnnouncementChoice),
		("Re&verb:", panel.reverbChoice),
	]
	# Tab order is sizer order, and the spec puts the sound theme first.
	assert helper.items == [
		panel.themeChoice,
		panel.roleAnnouncementChoice,
		panel.reverbChoice,
		panel.silenceDuringSayAllCheckBox,
	]
	# The check box carries its own label.
	assert (
		panel.silenceDuringSayAllCheckBox.label
		== "&Silence role sounds during say all"
	)
	assert all(control.parent is panel for control in helper.items)


def test_panel_shows_the_saved_settings(addon_gui):
	addon_gui._test_conf["unspoken"] = {
		"theme": "retro",
		"roleAnnouncement": "speechOnly",
		"reverb": "hall",
		"silenceDuringSayAll": True,
	}

	panel = _make_panel(
		addon_gui, themes=[_theme("default", "Default"), _theme("retro", "Retro")]
	)

	assert panel.themeChoice.selection == 1
	assert panel.roleAnnouncementChoice.selection == 2
	assert panel.reverbChoice.selection == 3
	assert panel.silenceDuringSayAllCheckBox.IsChecked() is True


def test_panel_falls_back_to_spec_defaults_without_a_registered_config(addon_gui):
	# Before #38 registers CONF_SPEC the section can be missing entirely.
	addon_gui._test_conf.pop("unspoken", None)

	panel = _make_panel(addon_gui)

	assert panel.themeChoice.selection == 0
	assert panel.roleAnnouncementChoice.selection == 0
	assert panel.reverbChoice.selection == 1
	assert panel.silenceDuringSayAllCheckBox.IsChecked() is False


def test_the_panel_offers_exactly_what_the_library_discovered(addon_gui):
	panel = _make_panel(addon_gui, themes=(_theme("default", "Bundled Default"),))

	assert panel.themeChoice.choices == ["Bundled Default"]
	assert panel._themeIds == ["default"]


def test_each_registration_gets_its_own_library(addon_gui):
	first_library = _FakeLibrary((_theme("default", "First"),))
	second_library = _FakeLibrary(
		(_theme("default", "Second"), _theme("retro", "Retro"))
	)
	first_class = addon_gui.register(
		themes=first_library, preview=_RecordingPreview()
	)
	second_class = addon_gui.register(
		themes=second_library, preview=_RecordingPreview()
	)

	first_panel = first_class.__new__(first_class)
	first_panel.makeSettings(object())
	second_panel = second_class.__new__(second_class)
	second_panel.makeSettings(object())

	assert first_panel.themeChoice.choices == ["First"]
	assert second_panel.themeChoice.choices == ["Second", "Retro"]
	assert first_library.discoveries == 1
	assert second_library.discoveries == 1


def test_register_and_unregister_leave_the_category_list_as_they_found_it(addon_gui):
	categories = addon_gui.gui.settingsDialogs.NVDASettingsDialog.categoryClasses
	sentinel = object()
	categories.append(sentinel)
	original = list(categories)

	panel_class = addon_gui.register(
		themes=_FakeLibrary(_ONE_BUNDLED_THEME), preview=_RecordingPreview()
	)
	assert categories == original + [panel_class]

	addon_gui.unregister(panel_class)
	assert categories == original
	addon_gui.unregister(panel_class)
	assert categories == original


def test_choosing_a_theme_applies_it_live(addon_gui):
	preview_recorder = _RecordingPreview()
	panel = _make_panel(
		addon_gui,
		themes=[_theme("default", "Default"), _theme("retro", "Retro")],
		preview=preview_recorder,
	)

	panel.themeChoice.choose(1, addon_gui._test_wx.EVT_CHOICE)

	assert preview_recorder.calls == [("theme", "retro")]


def test_choosing_a_reverb_preset_applies_it_live(addon_gui):
	preview_recorder = _RecordingPreview()
	panel = _make_panel(addon_gui, preview=preview_recorder)

	panel.reverbChoice.choose(3, addon_gui._test_wx.EVT_CHOICE)

	assert preview_recorder.calls == [("reverb", "hall")]


def test_role_announcement_is_not_applied_live(addon_gui):
	panel = _make_panel(addon_gui)

	assert addon_gui._test_wx.EVT_CHOICE not in panel.roleAnnouncementChoice.handlers


def test_save_persists_all_four_settings_without_reapplying(addon_gui):
	preview_recorder = _RecordingPreview()
	panel = _make_panel(
		addon_gui,
		themes=[_theme("default", "Default"), _theme("retro", "Retro")],
		preview=preview_recorder,
	)

	panel.themeChoice.choose(1, addon_gui._test_wx.EVT_CHOICE)
	panel.roleAnnouncementChoice.SetSelection(1)
	panel.reverbChoice.choose(2, addon_gui._test_wx.EVT_CHOICE)
	panel.silenceDuringSayAllCheckBox.SetValue(True)
	calls_before_save = list(preview_recorder.calls)
	panel.onSave()

	assert addon_gui._test_conf["unspoken"] == {
		"theme": "retro",
		"roleAnnouncement": "soundsAndSpeech",
		"reverb": "mediumRoom",
		"silenceDuringSayAll": True,
	}
	assert preview_recorder.calls == calls_before_save == [
		("theme", "retro"),
		("reverb", "mediumRoom"),
	]


def test_cancel_reverts_to_the_state_the_panel_opened_with(addon_gui):
	preview_recorder = _RecordingPreview()
	addon_gui._test_conf["unspoken"] = {
		"theme": "default",
		"roleAnnouncement": "sounds",
		"reverb": "smallRoom",
		"silenceDuringSayAll": False,
	}
	panel = _make_panel(
		addon_gui,
		themes=[
			_theme("default", "Default"),
			_theme("retro", "Retro"),
			_theme("marimba", "Marimba"),
		],
		preview=preview_recorder,
	)

	panel.themeChoice.choose(1, addon_gui._test_wx.EVT_CHOICE)
	panel.themeChoice.choose(2, addon_gui._test_wx.EVT_CHOICE)
	panel.reverbChoice.choose(0, addon_gui._test_wx.EVT_CHOICE)
	panel.reverbChoice.choose(3, addon_gui._test_wx.EVT_CHOICE)
	panel.onDiscard()

	# Not the previous selection: the one the panel opened with.
	assert preview_recorder.calls[-1] == (
		"revert",
		"default",
		"smallRoom",
	)
	assert addon_gui._test_conf["unspoken"] == {
		"theme": "default",
		"roleAnnouncement": "sounds",
		"reverb": "smallRoom",
		"silenceDuringSayAll": False,
	}


def test_cancel_after_apply_reverts_to_the_applied_state(addon_gui):
	preview_recorder = _RecordingPreview()
	panel = _make_panel(
		addon_gui,
		themes=[
			_theme("default", "Default"),
			_theme("retro", "Retro"),
			_theme("marimba", "Marimba"),
		],
		preview=preview_recorder,
	)

	panel.themeChoice.choose(1, addon_gui._test_wx.EVT_CHOICE)
	panel.reverbChoice.choose(3, addon_gui._test_wx.EVT_CHOICE)
	panel.onSave()  # what the Apply button does; the dialog stays open
	panel.themeChoice.choose(2, addon_gui._test_wx.EVT_CHOICE)
	panel.reverbChoice.choose(0, addon_gui._test_wx.EVT_CHOICE)
	panel.onDiscard()

	assert preview_recorder.calls[-1] == ("revert", "retro", "hall")
	assert addon_gui._test_conf["unspoken"]["theme"] == "retro"
	assert addon_gui._test_conf["unspoken"]["reverb"] == "hall"


def test_cancel_without_changes_asks_for_the_state_it_opened_with(addon_gui):
	preview_recorder = _RecordingPreview()
	panel = _make_panel(addon_gui, preview=preview_recorder)

	panel.onDiscard()

	assert preview_recorder.calls == [("revert", "default", "smallRoom")]


def test_cancel_stops_a_late_selection_event_from_reapplying(addon_gui):
	panel = _make_panel(addon_gui)

	panel.onDiscard()

	evt_choice = addon_gui._test_wx.EVT_CHOICE
	assert evt_choice not in panel.themeChoice.handlers
	assert evt_choice not in panel.reverbChoice.handlers


def test_the_recording_adapter_is_the_declared_interface():
	assert isinstance(_RecordingPreview(), preview.Preview)
