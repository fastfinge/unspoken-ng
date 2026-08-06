"""Tests for the volume the Sound Player sees (spec section 4.4)."""

import pytest

import volume


@pytest.mark.parametrize(
    "sound_volume,follows_voice,synth_volume,expected",
    [
        # Not following the voice: the sound volume, whatever the synth is at.
        (100, False, 20, 1.0),
        (50, False, 100, 0.5),
        (0, False, 100, 0.0),
        # Following it: the synth's volume replaces the sound volume.
        (100, True, 20, 0.2),
        (25, True, 80, 0.8),
        # Following it with nothing to follow -- no synth, or a synth with no
        # volume setting -- falls back to the sound volume, as NVDA does.
        (75, True, None, 0.75),
        # ConfigObj hands back strings when a key predates its spec entry.
        ("60", False, None, 0.6),
        (60, True, "30", 0.3),
        # Nothing usable is full gain, never silence.
        (None, False, None, 1.0),
        ("loud", False, None, 1.0),
        # Out of range is clamped rather than trusted.
        (140, False, None, 1.0),
        (-10, False, None, 0.0),
    ],
)
def test_effective_volume(sound_volume, follows_voice, synth_volume, expected):
    assert volume.effective_volume(sound_volume, follows_voice, synth_volume) == expected


def test_effective_volume_survives_a_nan():
    assert volume.effective_volume(float("nan"), False, None) == 1.0
