"""The playback decision: does this NVDA event produce a role sound, and when?

Every role sound the addon plays -- and every spoken role it suppresses --
follows from one verdict, and this module is where the whole verdict lives
(#64). Before it, the decision had no home: two of its five conditions sat in
the old `wiring.py` as separate predicates with an undeclared ordering between
them ("ask `should_ride_speech` only after `should_play_control_field` said
play"), and the other three sat inline and untested in `GlobalPlugin` -- the
role-announcement setting, compared three different ways across the object
path, the reading path and the suppression hook, plus the
synth-index-capability override OR-ed into the ride verdict.

The interface is one decide call with one predicate beside it:

- `decide(event, config)` takes an event's facts (`ObjectEvent` or
  `ControlField`) and a `Config` snapshot, and returns `LEAD`, `RIDE` or
  `SILENT`.
- `should_suppress_spoken_role(slot, config, degraded=...)` answers the
  speech hook's one question: does the addon swallow NVDA's spoken role
  because a sound stands in for it?

`GlobalPlugin`'s call sites gather inputs and obey; nothing there compares a
setting or re-implements a clause, and there is no order for a caller to get
wrong. Everything here takes plain values and returns plain values: no NVDA,
no OpenAL, no I/O, no globals -- which is what lets the whole verdict be
table-tested off NVDA against the #32 dataset (`tests/test_playback.py`).

The session-level half of the same question -- can this session produce a
role sound at all -- is `can_produce_role_sound`, decided once at the end of
construction; its answer reaches the suppression predicate as `degraded`.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass


# --------------------------------------------------------------------------
# The reading-path vocabulary (spec section 5)
# --------------------------------------------------------------------------

#: The say-all reason, named because two clauses of the verdict turn on it.
SAY_ALL_REASON = "SAYALL"

#: `OutputReason` names the reading path plays for.
#:
#: `FOCUS` is excluded deliberately rather than accidentally: `event_gainFocus`
#: has already played that object, so excluding it is the dedup (zero
#: double-fires across 1,789 measured records in #32). `ONLYCACHE` never
#: reaches a hook that is about to speak. Everything else -- `QUERY`, `CHANGE`,
#: `MESSAGE`, `MOUSE`, `FOCUSENTERED` -- is not the reading path.
PLAY_REASONS = frozenset({"CARET", "QUICKNAV", SAY_ALL_REASON})

#: The fieldType of a field the reading position just landed *inside*, named
#: because the lead/ride split turns on it.
FIELD_ENTERED = "start_addedToControlFieldStack"

#: The two `fieldType` values NVDA announces a control on.
#:
#: The tempting filter is `fieldType.startswith("start")`, and it is wrong:
#: `start_inControlFieldStack` fires for every field the caret is *already*
#: inside, which in Word is the enclosing EDITABLETEXT on every line --- 76
#: repeats in 40 keypresses (#32). That filter would play the editable-text
#: sound roughly twice per line, forever.
#:
#: **Known accepted cost.** Excluding `start_inControlFieldStack` is not quite
#: exact, so "suppress if and only if we play" is *nearly* true rather than
#: true. `speech.py` does emit a role for that fieldType in one case:
#: `speakWithinForLine`, which applies only to `PRESCAT_SINGLELINE` fields. A
#: single-line field whose role maps to a slot is therefore suppressed with no
#: sound replacing it. It does not occur anywhere in #32's 2,473 measured calls
#: -- Word's repeated EDITABLETEXT is multiline and LISTITEM is
#: `PRESCAT_MARKER` -- and widening the filter to catch it would reinstate
#: those 76 repeats per 40 keypresses, which is far worse. Recorded here and in
#: PR #51 so it lands as a stated cost; folding it into spec section 13 is
#: Andrew's call, not this module's.
PLAY_FIELD_TYPES = frozenset({FIELD_ENTERED, "start_relative"})


# --------------------------------------------------------------------------
# The verdict and its inputs
# --------------------------------------------------------------------------


class Verdict(enum.Enum):
    """The three-way answer: play now, play when speech arrives, or not at all.

    `LEAD` plays at decision time, ahead of the speech announcing the control
    -- the object events' behaviour, and the reading path's for the field the
    navigation landed inside (ADR 0002). `RIDE` puts the sound into the
    field's speech sequence to fire when the synth reaches it. `SILENT` plays
    nothing.
    """

    LEAD = "lead"
    RIDE = "ride"
    SILENT = "silent"


#: Module-level aliases, so call sites and tests read `playback.LEAD`.
LEAD = Verdict.LEAD
RIDE = Verdict.RIDE
SILENT = Verdict.SILENT


@dataclass(frozen=True, slots=True)
class Config:
    """The two settings the verdict reads, snapshotted by the caller.

    - `role_announcement`: spec section 8's three-way setting, exactly as
      stored -- "sounds", "soundsAndSpeech" or "speechOnly".
    - `silence_during_say_all`: the spec section 8 say-all gate. It silences
      say-all plays and nothing else -- suppression of *spoken* roles stays
      governed by role announcement, which is a separate setting and a
      separate question (`should_suppress_spoken_role`).
    """

    role_announcement: str
    silence_during_say_all: bool


@dataclass(frozen=True, slots=True)
class ObjectEvent:
    """An object event's facts: focus, navigator or mouse handed us an object.

    One fact suffices: `slot` is what `roles.slot_for()` returned for the
    object's role, so None means "this role has no sound". An object event's
    sound never rides -- the event cancels current speech and the fresh
    utterance starts immediately, so decision time *is* utterance time
    (ADR 0002).
    """

    slot: str | None


@dataclass(frozen=True, slots=True)
class ControlField:
    """A reading-path control field's facts, gathered at the speech hook.

    Every field is a plain value the caller has already extracted:

    - `reason`: the `OutputReason` member's *name*, or None.
    - `field_type`: NVDA's `fieldType` string.
    - `slot`: what `roles.slot_for()` returned for the field's role, so None
      means "this role has no sound".
    - `synth_reports_indexes`: whether the current synth notifies
      `synthIndexReached`. A riding sound is fired by the speech manager when
      the synth reports reaching its index; a synth that never reports would
      leave it waiting forever, so on such a synth a would-be ride downgrades
      to a lead -- bursts and all -- rather than never firing (ADR 0002).
    """

    reason: str | None
    field_type: str | None
    slot: str | None
    synth_reports_indexes: bool


def decide(event: ObjectEvent | ControlField, config: Config) -> Verdict:
    """The whole verdict for one event: `LEAD`, `RIDE` or `SILENT`.

    Position is not an input, and that is the point: when this says play, the
    sound plays, and the position tiers degrade underneath it. A sound whose
    presence depends on a metadata lookup teaches users to hear our lookup
    failures (#31).

    Degraded mode is not an input either: a play lands on whichever Sound
    Player adapter sits under the seam, and in a degraded session that is the
    silent one. Only suppression is dangerous there, and
    `should_suppress_spoken_role` asks about it.

    The reading-path clauses keep ADR 0002's truth table verbatim. The play
    condition is the #32-measured filter: a fieldType NVDA announces a
    control on, a reason that is the reading path, a role with a sound, and
    not say-all when the user silenced say-all. The lead/ride split needs no
    utterance tracking, because `fieldType` already encodes it:
    `FIELD_ENTERED` is a field the reading position just landed *inside* --
    its announcement heads the utterance, and under `CARET` and `QUICKNAV`
    that utterance starts now, both cancel current speech, so decision time
    *is* utterance time and the sound may lead. `start_relative` is a field
    speech will traverse mid-utterance, which is the burst-at-line-start case
    (#52). Under `SAYALL` even the utterance start sits behind the read-ahead
    queue, so there everything rides. In the #32 dataset the split falls 84
    leads / 29 rides across the 113 plays (`tests/test_playback.py` pins the
    exact triples).
    """
    if event.slot is None:
        return SILENT
    if config.role_announcement == "speechOnly":
        return SILENT
    if isinstance(event, ObjectEvent):
        return LEAD
    if event.field_type not in PLAY_FIELD_TYPES:
        return SILENT
    if event.reason not in PLAY_REASONS:
        return SILENT
    if event.reason == SAY_ALL_REASON and config.silence_during_say_all:
        return SILENT
    rides = event.reason == SAY_ALL_REASON or event.field_type != FIELD_ENTERED
    if rides and event.synth_reports_indexes:
        return RIDE
    return LEAD


def should_suppress_spoken_role(
    slot: str | None, config: Config, *, degraded: bool
) -> bool:
    """Does the addon swallow NVDA's spoken role announcement?

    True exactly when a sound stands in for the role: the session can produce
    role sounds at all (`degraded` is False), the role maps to a slot, and
    role announcement is "sounds". "soundsAndSpeech" plays *and* speaks, so
    it never suppresses; "speechOnly" plays nothing and suppresses nothing;
    and a degraded session suppresses nothing, so NVDA speaks control roles
    as it would without the addon (spec section 9.2).

    "Suppress if and only if we play" is nearly true rather than true, in one
    recorded direction: the single-line-field cost documented beside
    `PLAY_FIELD_TYPES`.
    """
    return not degraded and slot is not None and config.role_announcement == "sounds"


# --------------------------------------------------------------------------
# "Can I produce a role sound?" (spec section 9.2)
# --------------------------------------------------------------------------

#: The keys `can_produce_role_sound` reads. `GlobalPlugin` fills all three
#: during construction; anything it forgets counts as missing, which degrades.
OUTCOME_KEYS = ("engine_ready", "device_open", "slots_loaded")

_CAUSES = (
    ("engine_ready", "the audio engine could not be started"),
    ("device_open", "no output device would open"),
    ("slots_loaded", "no sound theme samples could be loaded"),
)


def can_produce_role_sound(outcome) -> bool:
    """Spec section 9.2's one question, asked once, at the end of construction.

    It is an *outcome* predicate, not a health check: it asks whether this
    session can put a role sound in the air, over the three things that have to
    be true for that -- the engine started, a device opened, and the theme
    decoded to at least one slot. Any fourth cause is covered by construction:
    whatever it was, it raised, and `engine_ready`/`device_open` stay False.

    `outcome` is a plain mapping so the wiring can fill it from three different
    exception branches and hand the same shape to the log, the flag and the
    tests.

    False means the session runs speech-only: silent adapter below the seam, no
    suppression above it, and the saved role-announcement setting untouched, so
    a repaired install returns to sounds with nothing for the user to re-set.
    """
    return all(bool(outcome.get(key)) for key in OUTCOME_KEYS)


def degraded_cause(outcome) -> str | None:
    """The first missing precondition, phrased for the log; None if there is none.

    Spec section 9.4 gives the user one sentence and the log the cause. This is
    the cause.
    """
    for key, phrase in _CAUSES:
        if not outcome.get(key):
            return phrase
    return None
