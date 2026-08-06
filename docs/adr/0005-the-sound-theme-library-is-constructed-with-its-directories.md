# The sound theme library is constructed with its directories

The sound theme library is constructed with the bundled directory and the user's directory, or
none, and owns discovery, decoding and every "no usable theme" fallback behind `discover()` and
`load()`. This implements the 2026-08-01 architecture review's recommendation
([#66](https://github.com/akj/unspoken-ng/issues/66)): an instance that exists already knows where
to look, so callers cannot observe or violate a separate configuration step.

## Considered options

**Keep the module global plus the ordering comment.** Rejected because the settings panel already
calls past the wiring that sets it, and a comment cannot make the ordering checkable. Every caller
would still have to know that discovery and loading are unsafe until another caller has acted.

**A `configure()` that raises when called twice.** Rejected because it remains a two-step
interface. The module is still reachable before configuration, so the original invalid state and
its ordering rule survive even if a second configuration becomes loud.

**Classmethods over class-level state.** Rejected because class-level directories are the same
process-global state with different spelling. Registrations still interfere with one another, and
construction still proves nothing about readiness.

**Leave the empty-discovery fallback in the panel.** Rejected because it spreads sound-theme
policy across the GUI seam. The panel would have to invent an entry that loading independently
interprets, rather than taking one library answer as final.

**Make `load` never return `{}`.** Rejected because there is no truthful sample dictionary when
the bundled default itself is unusable. Inventing a non-empty answer would silently disable the
slot-count signal that puts the session in degraded mode and preserves spoken roles.

## Consequences

- **Discovery is never empty.** Even a missing or unreadable bundled default appears as a synthetic
  entry, while `{}` from `load` is now the only empty answer and the degraded-mode signal.
- **The reference level is a constructor argument.** The loudness rig can compare levels without
  mutating a module private or changing what another library instance loads.
- **The panel receives its library at registration.** Each registration creates a subclass carrying
  that one binding, so unregistering the class is complete teardown and reloads cannot rebind an
  old panel under NVDA's feet.
- **The broken-install `Default` label is untranslated.** It is a last-resort placeholder produced
  by pure stdlib code only when the bundled manifest cannot supply its real name.
- **This refines [ADR 0003](0003-sound-player-seam-and-module-layout.md).** The `themes` module
  remains above the Sound Player seam, but its directory knowledge and fallback policy now live in
  one constructed library rather than caller ordering and GUI recovery.
