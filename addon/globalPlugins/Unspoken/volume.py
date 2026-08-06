"""NVDA's sound-volume folding, reproduced for the settings provider.

One function, one rule -- NVDA's own -- and no playback policy: like the
debounce timer, #64 moved it out of the policy module because it computes a
gain, not whether a sound happens. `GlobalPlugin`'s settings provider calls
it on the hot path inside `play`; the cost accounting lives on
`_NVDASettingsProvider`. Plain values in, a plain float out, so the rule is
table-tested off-NVDA (`tests/test_volume.py`).
"""

from __future__ import annotations


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
    the rule is reproduced here rather than inherited. A value NVDA would never
    store -- None, a string that is not a number -- yields full gain rather
    than silence: the addon's failure mode is never "quietly plays nothing".
    """
    value = synth_volume if (follows_voice and synth_volume is not None) else sound_volume
    try:
        percent = float(value)
    except (TypeError, ValueError):
        return 1.0
    if percent != percent:  # NaN
        return 1.0
    return max(0.0, min(1.0, percent / 100.0))
