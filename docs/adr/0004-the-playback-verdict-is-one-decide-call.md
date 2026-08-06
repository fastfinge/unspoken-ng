# The playback verdict is one decide call

Whether an NVDA event produces a role sound, and whether that sound leads speech or rides it, is
decided in one module behind one call: `playback.decide(event, config)` takes an event's facts
(`ObjectEvent` or `ControlField`, plain values the caller extracted) and a `Config` snapshot, and
returns **lead**, **ride** or **silent**. Beside it sits the one other half of the same verdict,
`should_suppress_spoken_role`, which the speech hook asks before swallowing NVDA's spoken role.
`GlobalPlugin`'s three call sites — the object events, the reading-path hook, the suppression
hook — gather inputs and obey; nothing there compares a setting or re-implements a clause.

This was the top recommendation of the 2026-08-01 architecture review
([#64](https://github.com/akj/unspoken-ng/issues/64)). Before it, the verdict had five conditions
in three homes: `wiring.py` held `should_play_control_field` and `should_ride_speech` — with the
undeclared caller obligation that the second is only meaningful after the first said play — while
the role-announcement setting was compared three different ways inline in `GlobalPlugin` (object
path, reading path, suppression hook), and the synth-index-capability override was OR-ed into the
ride verdict at the call site. The inline gates were exactly the ones no table test covered.

## Considered options

**Leave the split (status quo).** Rejected on the review's own finding: the three inline gates
were untested, the three role-announcement comparisons could drift apart silently — the
"suppressed but not sounded" bug is one wrong comparison away — and the ordering precondition
between the two wiring predicates lived only in a docstring.

**Extract the inline gates as three more predicates in the policy module.** The smallest diff,
and rejected for making the real problem worse: callers would compose five booleans instead of
three, every call site must know which predicates apply to its path and in what order, and the
ordering obligation survives. Many shallow functions over one decision is the shape the review
set out to eliminate.

**A boolean trio (`should_play` / `should_ride` / `should_suppress`).** Rejected because
play-and-when is not two independent booleans: `ride` without `play` is a representable but
meaningless state, and keeping it meaningless is precisely the ordering rule callers kept having
to be told. A three-valued verdict makes the invalid state unrepresentable and the precondition
dissolves into an implementation detail.

**The policy module reads config itself.** Rejected without much argument: the policy module's
value is that it imports no NVDA and runs under pytest, which is what lets the whole verdict be
table-tested against the #32 dataset. The caller snapshots the two settings into a plain
`Config`; four dict lookups per decision, on par with what the call sites already paid.

## Consequences

- **All five conditions are table-tested through one interface** (`tests/test_playback.py`),
  including the formerly inline gates: the role-announcement setting across both paths, and the
  synth-index override — measured over the whole #32 dataset, not just the happy triples.
- **ADR 0002's truth table is unchanged.** The lead/ride split relocated verbatim into
  `decide`; the dataset still falls 84 leads / 29 rides across 113 plays, and the pre-existing
  table tests pin the same triples.
- **The synth-index capability is gathered up front** as one of the verdict's inputs, on every
  reading-path call rather than lazily behind the old ride predicate: two attribute reads and a
  frozenset membership against a value the 32-bit bridge caches, traded for a call site with no
  logic in it.
- **Degraded mode is an input to suppression, not to `decide`.** A play in a degraded session
  lands on the silent adapter and no-ops below the seam; only suppression is dangerous there, so
  only `should_suppress_spoken_role` takes `degraded`.
- **The policy module holds only playback decisions.** The debounce timer and the NVDA
  volume-folding rule — generic utilities the old `wiring.py` had accreted — moved to
  `debounce.py` and `volume.py`. `wiring.py` is retired, and the packaging check forbids it
  from ever shipping again.
