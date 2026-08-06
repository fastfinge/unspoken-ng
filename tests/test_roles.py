import controlTypes

import roles


EXPECTED_ROLE_SLOTS = {
    controlTypes.Role.CHECKBOX: "checkbox",
    controlTypes.Role.RADIOBUTTON: "radiobutton",
    controlTypes.Role.STATICTEXT: "editabletext",
    controlTypes.Role.EDITABLETEXT: "editabletext",
    controlTypes.Role.BUTTON: "button",
    controlTypes.Role.MENUBAR: "menuitem",
    controlTypes.Role.MENUITEM: "menuitem",
    controlTypes.Role.MENU: "menuitem",
    controlTypes.Role.COMBOBOX: "combobox",
    controlTypes.Role.LISTITEM: "listitem",
    controlTypes.Role.GRAPHIC: "icon",
    controlTypes.Role.LINK: "link",
    controlTypes.Role.TREEVIEWITEM: "treeviewitem",
    controlTypes.Role.TAB: "tab",
    controlTypes.Role.TABCONTROL: "tab",
    controlTypes.Role.SLIDER: "slider",
    controlTypes.Role.DROPDOWNBUTTON: "combobox",
    controlTypes.Role.CLOCK: "clock",
    controlTypes.Role.ANIMATION: "icon",
    controlTypes.Role.ICON: "icon",
    controlTypes.Role.IMAGEMAP: "icon",
    controlTypes.Role.RADIOMENUITEM: "radiobutton",
    controlTypes.Role.RICHEDIT: "editabletext",
    controlTypes.Role.SHAPE: "icon",
    controlTypes.Role.TEAROFFMENU: "menuitem",
    controlTypes.Role.TOGGLEBUTTON: "checkbox",
    controlTypes.Role.CHART: "icon",
    controlTypes.Role.DIAGRAM: "icon",
    controlTypes.Role.DIAL: "slider",
    controlTypes.Role.DROPLIST: "combobox",
    controlTypes.Role.MENUBUTTON: "button",
    controlTypes.Role.DROPDOWNBUTTONGRID: "button",
    controlTypes.Role.HOTKEYFIELD: "editabletext",
    controlTypes.Role.INDICATOR: "icon",
    controlTypes.Role.SPINBUTTON: "slider",
    controlTypes.Role.TREEVIEWBUTTON: "button",
    controlTypes.Role.DESKTOPICON: "icon",
    controlTypes.Role.PASSWORDEDIT: "editabletext",
    controlTypes.Role.CHECKMENUITEM: "checkbox",
    controlTypes.Role.SPLITBUTTON: "splitbutton",
}

CANONICAL_SLOTS = {
    "button",
    "checkbox",
    "clock",
    "combobox",
    "editabletext",
    "icon",
    "link",
    "listitem",
    "menuitem",
    "radiobutton",
    "slider",
    "splitbutton",
    "tab",
    "treeviewitem",
}


def test_every_current_role_resolves_to_its_slot():
    for role, expected_slot in EXPECTED_ROLE_SLOTS.items():
        assert roles.slot_for(role) == expected_slot


def test_unmapped_role_returns_none():
    assert roles.slot_for(controlTypes.Role.UNKNOWN_TEST_ROLE) is None


def test_mapping_contains_exactly_the_canonical_slots():
    assert set(roles.ROLE_TO_SLOT.values()) == CANONICAL_SLOTS
