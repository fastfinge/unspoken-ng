"""Spec section 8's configuration surface, declared once.

These are the five keys of ``config.conf["unspoken"]``. The allowed values,
the defaults, and the ConfigObj spec NVDA validates against all derive from
the declarations here, so adding a key or a value is a change to this file
alone.

The panel's translated labels stay in ``addonGui`` because they need NVDA's
translation machinery. The Sound Player's EFX parameters stay in ``player``,
which deliberately imports nothing from this package. Both are verified
against these declarations: the panel at import time, and the player by
``tests/test_settings.py``.
"""

#: How control roles are announced, in the order the panel offers them.
ROLE_ANNOUNCEMENT_VALUES = ("sounds", "soundsAndSpeech", "speechOnly")

#: The reverb preset names the Sound Player accepts, in the order the panel offers them.
REVERB_PRESETS = ("none", "smallRoom", "mediumRoom", "hall")

#: Spec section 8's defaults, the one place they are written down.
DEFAULTS = {
    "theme": "default",
    "roleAnnouncement": "sounds",
    "reverb": "smallRoom",
    "volume": 100,
    "silenceDuringSayAll": False,
}

#: The five config keys, in the panel's order.
CONFIG_KEYS = tuple(DEFAULTS)


def _option(values, default):
    if default not in values:
        raise ValueError(f"default {default!r} is not one of {values!r}")
    choices = ", ".join(f'"{value}"' for value in values)
    return f'option({choices}, default="{default}")'


CONF_SPEC = {
    "theme": f'string(default="{DEFAULTS["theme"]}")',
    "roleAnnouncement": _option(
        ROLE_ANNOUNCEMENT_VALUES, DEFAULTS["roleAnnouncement"]
    ),
    "reverb": _option(REVERB_PRESETS, DEFAULTS["reverb"]),
    "volume": "integer(default=100, min=0, max=100)",
    "silenceDuringSayAll": f'boolean(default={DEFAULTS["silenceDuringSayAll"]})',
}
