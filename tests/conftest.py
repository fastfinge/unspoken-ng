import enum
import sys
from types import ModuleType


ROLE_NAMES = (
    "CHECKBOX",
    "RADIOBUTTON",
    "STATICTEXT",
    "EDITABLETEXT",
    "BUTTON",
    "MENUBAR",
    "MENUITEM",
    "MENU",
    "COMBOBOX",
    "LISTITEM",
    "GRAPHIC",
    "LINK",
    "TREEVIEWITEM",
    "TAB",
    "TABCONTROL",
    "SLIDER",
    "DROPDOWNBUTTON",
    "CLOCK",
    "ANIMATION",
    "ICON",
    "IMAGEMAP",
    "RADIOMENUITEM",
    "RICHEDIT",
    "SHAPE",
    "TEAROFFMENU",
    "TOGGLEBUTTON",
    "CHART",
    "DIAGRAM",
    "DIAL",
    "DROPLIST",
    "MENUBUTTON",
    "DROPDOWNBUTTONGRID",
    "HOTKEYFIELD",
    "INDICATOR",
    "SPINBUTTON",
    "TREEVIEWBUTTON",
    "DESKTOPICON",
    "PASSWORDEDIT",
    "CHECKMENUITEM",
    "SPLITBUTTON",
    "UNKNOWN_TEST_ROLE",
    # Roles that appear in the #32 reading-path fixture but are deliberately
    # absent from roles.ROLE_TO_SLOT. They are stubbed so the fixture test can
    # ask for them by name and get the real answer -- no slot, no sound --
    # rather than an AttributeError that would look like the same thing.
    "DOCUMENT",
    "GROUPING",
    "HEADING",
    "LABEL",
    "LANDMARK",
    "LIST",
    "PARAGRAPH",
    "SECTION",
)

# The review flagged that NVDA deprecated the module-level ROLE_* aliases
# in favor of controlTypes.Role, an IntEnum. Stubbing with plain ints could
# silently hide an alias collision, so this stub mirrors the real shape: a
# genuine IntEnum with one distinct member per role, matching production
# controlTypes.Role in kind (a real enum), not merely in appearance.
Role = enum.IntEnum("Role", ROLE_NAMES)

control_types_stub = ModuleType("controlTypes")
control_types_stub.Role = Role
sys.modules["controlTypes"] = control_types_stub
