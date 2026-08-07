"""NVDA's sound-volume folding and the shared percentage-to-gain conversion.

Both rules compute a gain, not whether a sound happens, so they stay out of
the playback policy module. `GlobalPlugin`'s settings provider calls them on
the hot path inside `play`; the cost accounting lives on
`_NVDASettingsProvider`. Plain values in, a plain float out, so both rules are
table-tested off-NVDA (`tests/test_volume.py`).
"""

from __future__ import annotations


def gain_from_percent(value) -> float:
    """Convert a percentage to a gain in 0.0-1.0, tolerantly.

    A value NVDA (or the addon) would never store -- None, a string that is
    not a number, or NaN -- yields full gain rather than silence: the addon's
    failure mode is never "quietly plays nothing".
    """
    try:
        percent = float(value)
    except (TypeError, ValueError):
        return 1.0
    if percent != percent:  # NaN
        return 1.0
    return max(0.0, min(1.0, percent / 100.0))


def effective_volume(
    sound_volume: float | int | str | None,
    follows_voice: bool,
    synth_volume: float | int | str | None,
) -> float:
    """Fold NVDA's two sound-volume settings down to one gain in 0.0-1.0.

    This is NVDA's own rule, from `nvwave.WavePlayer._setVolumeFromConfig`:
    the configured sound volume, unless the user asked sound volume to follow
    the voice and there is a synth volume to follow. Both inputs are
    percentages.

    `synth_volume` is None when there is no synth or the synth does not support
    a volume setting -- exactly the case NVDA falls back to `soundVolume` for.

    Owning the output stream (ADR 0001) means nothing computes this for us, so
    the rule is reproduced here rather than inherited.
    """
    value = synth_volume if (follows_voice and synth_volume is not None) else sound_volume
    return gain_from_percent(value)
