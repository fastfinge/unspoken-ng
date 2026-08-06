"""The config surface has one declaration.

These tests pin what derives from it and cross-check the one consumer that
cannot import it: the Sound Player.
"""

import player
import settings
import themes


def test_conf_spec_matches_the_configuration_schema():
    assert settings.CONF_SPEC == {
        "theme": 'string(default="default")',
        "roleAnnouncement": (
            'option("sounds", "soundsAndSpeech", "speechOnly", default="sounds")'
        ),
        "reverb": (
            'option("none", "smallRoom", "mediumRoom", "hall", default="smallRoom")'
        ),
        "silenceDuringSayAll": "boolean(default=False)",
    }


def test_defaults_cover_exactly_the_config_keys():
    assert settings.CONFIG_KEYS == (
        "theme",
        "roleAnnouncement",
        "reverb",
        "silenceDuringSayAll",
    )
    assert set(settings.DEFAULTS) == set(settings.CONFIG_KEYS)


def test_the_theme_library_falls_back_to_the_declared_default_theme():
    assert themes.DEFAULT_THEME_ID == settings.DEFAULTS["theme"]


def test_the_sound_player_accepts_exactly_the_declared_reverb_presets():
    assert set(player.REVERB_PRESETS) == set(settings.REVERB_PRESETS)
