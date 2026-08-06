"""Rewrite legacy Unspoken settings onto the surface declared in settings.py."""

_OLD_KEYS = (
    "sayAll",
    "speakRoles",
    "noSounds",
    "HRTF",
    "volumeAdjust",
    "Reverb",
    "RoomSize",
    "Damping",
    "WetLevel",
    "DryLevel",
    "Width",
)


def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "on"}
    return False


def migrate(section) -> None:
    """Migrate legacy settings by mutating ``section`` in place.

    ``section`` must be the raw ConfigObj profile section from
    ``config.conf.profiles[...]``. Its values are strings when read from disk,
    and it supports key deletion. Do not pass ``config.conf["unspoken"]``:
    NVDA's AggregatedSection does not support key deletion. A plain dict with
    the same behavior is also supported for testing.

    The function returns ``None``. If no legacy keys are present, the mapping
    is left completely unchanged.
    """
    if not any(key in section for key in _OLD_KEYS):
        return

    if "noSounds" in section or "speakRoles" in section:
        no_sounds = _as_bool(
            section["noSounds"] if "noSounds" in section else False
        )
        speak_roles = _as_bool(
            section["speakRoles"] if "speakRoles" in section else False
        )
        if no_sounds:
            section["roleAnnouncement"] = "speechOnly"
        elif speak_roles:
            section["roleAnnouncement"] = "soundsAndSpeech"
        else:
            section["roleAnnouncement"] = "sounds"

    if "sayAll" in section and _as_bool(section["sayAll"]):
        section["silenceDuringSayAll"] = True

    if "Reverb" in section:
        section["reverb"] = "smallRoom" if _as_bool(section["Reverb"]) else "none"

    for key in _OLD_KEYS:
        if key in section:
            del section[key]
