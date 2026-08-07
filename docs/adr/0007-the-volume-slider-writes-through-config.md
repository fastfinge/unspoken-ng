# The volume slider writes through config

[Issue #22](https://github.com/fastfinge/unspoken-ng/issues/22) asked for a way to change role-sound
volume. The settings provider already folds NVDA's Audio settings — *Volume of NVDA sounds* and
the follows-voice toggle — by reproducing `nvwave.WavePlayer._setVolumeFromConfig`, but there was
no addon-level control and nothing documented about it. A `volume` key from 0 to 100, default 100,
now multiplies onto that NVDA-effective gain, and the panel's slider writes the key through live
config on every change. That makes volume the third live-audible setting without widening the
`Preview` interface.

## Considered options

**Extend `Preview` with a `preview_volume` method.** Rejected because the preview interface exists
for pushed state, like a theme decode or reverb EFX writes, where only the plugin-side adapter
knows the cost and what is already applied. Volume is pulled state instead: the Sound Player reads
the settings provider on every play. An adapter hop would end exactly where the panel already
stands, writing one config key.

**Add a `set_volume` command to the Sound Player seam.** Rejected because this would be a second
channel for state the provider already owns. The seam stays four fire-and-forget commands per ADR
0003, and a pushed volume would race the pulled one.

**Apply on save only, as NVDA's own Audio panel does for *Volume of NVDA sounds*.** Rejected
because this panel's contract from ADR 0006 is that audible settings are heard while the panel is
open. A volume you cannot hear until Apply forces a set-Apply-listen loop on exactly the users the
addon serves.

**Follow speech volume by default instead of adding a control.** Rejected because NVDA already
owns that rule, *Volume of NVDA sounds follows voice volume*, and the provider follows it.
Re-deciding a global NVDA setting per-addon would surprise.

## Consequences

- Cancel-safety costs nothing new: `onDiscard` already restores every `unspoken` key from the
  panel's snapshot; the slider needs only the same late-event unbind as the combo boxes.
- The effective gain is a product: NVDA's sounds volume, or the voice volume when follows-voice is
  on, multiplied by the addon slider. At the default 100 the slider leaves NVDA's answer untouched.
- The slider fires no sound of its own; the new level is heard on the next role sound, typically
  the dialog's own focus sounds.
- Live preview now covers three audible settings, not two.
