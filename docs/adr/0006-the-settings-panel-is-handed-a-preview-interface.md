# The settings panel is handed a preview interface

The settings panel is handed one `Preview` when its per-registration class is created: it may
preview a sound theme, preview a reverb preset, or revert both to values it supplies. The running
addon implements that interface with `preview.LivePreview`, which alone knows what is already
playing, which work is expensive enough to debounce, and how to reach the Sound Player and sound
theme library. This implements the 2026-08-01 architecture review's recommendation
([#67](https://github.com/akj/unspoken-ng/issues/67)): the panel states the audible result it
wants without owning any of the mechanism that produces it.

## Considered options

**Keep the two module globals rebound at runtime.** Rejected because their binding is not testable
without mutating module state, and teardown has to remember identity-versus-equality etiquette
before restoring them. More importantly, one arrow keypress took eleven hops across four files:
the visible call was small only because the real interface remained scattered through wiring.

**Hand the panel the `GlobalPlugin` instance.** Rejected because the whole plugin would become the
contract. The panel could then reach event hooks, suppression state and lifetime machinery when it
needs exactly three operations, while a test double would have to imitate an object far wider than
the behavior under test.

**Set class attributes on the single `SettingsPanel` class at registration.** Rejected because
that is a global by another name. Two live plugins during a reload would stomp each other's
bindings, and a panel retained by the old settings dialog could begin calling the new plugin.
The per-registration subclass from ADR 0005 gives each lifetime its own bindings instead.

**Publish selection changes through an observer or event bus.** Rejected as machinery for two
events with one listener. There is no fan-out, subscription lifetime or event history to model;
one handed object is the whole relationship.

**Debounce in the panel.** Rejected because the panel would have to know that decoding a theme is
about 20 ms of pure Python on NVDA's main thread, while reverb is only a handful of EFX writes.
That audio cost is not GUI policy, and putting it there would also pull `wx.CallLater` into the GUI
module instead of handing the adapter a scheduler.

**Use an argument-less `revert()` with an adapter-held baseline.** Rejected because the adapter
would need `begin`, `commit` and `rollback` semantics to follow Apply as well as Cancel,
duplicating the panel's combo-index mapping and saved snapshots. The panel already knows exactly
which IDs it opened, or last saved, showing; passing them keeps both sides small.

## Consequences

- **`THEME_PREVIEW_DEBOUNCE_MS` lives in `preview.py`.** The adapter that knows why theme work is
  collapsed owns the policy, and `GlobalPlugin` only supplies `wx.CallLater`.
- **The interface is declared beside the plugin-side adapter, not beside its consumer.** This is
  the inverse of `player.SettingsProvider`, whose only implementation is NVDA-specific while the
  portable Sound Player is the consumer. Here the portable adapter and its structural contract
  must import bare under pytest, while the NVDA-only panel deliberately imports neither `themes`
  nor `preview`; registration is its composition boundary.
- **Revert is immediate and cancels a pending theme preview.** Cancel restores the requested state
  up to 300 ms sooner than the old debounced revert, deliberately: the user has finished choosing,
  so there is no burst left to collapse.
- **Teardown is `close()` plus `unregister`.** Closing drops pending work and makes an adapter held
  by a stale dialog inert; unregistering removes the per-registration panel class. There are no
  module globals to compare or restore.
- **The panel owns the baseline, while the adapter owns applied state.** Apply re-baselines the
  values Cancel supplies; the adapter skips any theme decode or reverb write already in effect.
