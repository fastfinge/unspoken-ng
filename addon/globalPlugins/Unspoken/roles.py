"""Role-to-slot mapping from spec section 4.1.

This is the only module that imports controlTypes. The canonical slots are
button, checkbox, clock, combobox, editabletext, icon, link, listitem,
menuitem, radiobutton, slider, splitbutton, tab, and treeviewitem.
"""

from __future__ import annotations

import controlTypes


ROLE_TO_SLOT = {
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


def slot_for(role: controlTypes.Role) -> str | None:
    """Return the canonical slot for a control role, if one is mapped."""
    return ROLE_TO_SLOT.get(role)
