"""Tests for the playback verdict, through its one interface.

`GlobalPlugin` itself is not unit-tested, deliberately and per spec section 10:
it imports NVDA on its first line and its job is property reads, patches and
lifetime, none of which mean anything outside a running screen reader. It is
covered by `docs/smoke-test.md`, run against a live NVDA.

What *is* testable is what it decides, which is why the whole verdict lives in
`playback.py` behind `decide` (#64, ADR 0004). The reading path is tested
against the #32 measurement dataset: 2,473 `getControlFieldSpeech` calls
captured from Chrome, Firefox and Word, collapsed to the 106 distinct
(reason, fieldType, role) triples they contain, in
`fixtures/control_field_calls.json`. The same dataset now also exercises the
gates that used to sit inline and untested in `GlobalPlugin`: the
role-announcement setting and the synth-index-capability override.
"""

import json
from pathlib import Path

import controlTypes
import pytest

import playback
import roles


FIXTURE = Path(__file__).parent / "fixtures" / "control_field_calls.json"


def _records():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _slot(role_name):
    return roles.slot_for(getattr(controlTypes.Role, role_name))


def _config(role_announcement="sounds", silence_during_say_all=False):
    return playback.Config(
        role_announcement=role_announcement,
        silence_during_say_all=silence_during_say_all,
    )


def _verdict(record, synth_reports_indexes=True, **config):
    return playback.decide(
        playback.ControlField(
            reason=record["reason"],
            field_type=record["fieldType"],
            slot=_slot(record["role"]),
            synth_reports_indexes=synth_reports_indexes,
        ),
        _config(**config),
    )


def _plays(record, **kwargs):
    return _verdict(record, **kwargs) is not playback.SILENT


# --- the reading-path play condition, against the #32 dataset --------------

#: Every triple in the dataset that plays, with the number of measured calls
#: it stands for. Written out rather than computed: a test that recomputes the
#: condition it is testing proves only that Python is deterministic.
#:
#: 113 plays out of 2,473 measured calls. The number is the point: the
#: obvious filters overshoot it badly, and by how much is measured in
#: `test_a_bare_start_filter_would_nearly_double_the_sounds` and
#: `test_in_stack_repeats_never_play`.
EXPECTED_PLAYS = {
    ("CARET", "start_addedToControlFieldStack", "BUTTON"): 7,
    ("CARET", "start_addedToControlFieldStack", "CHECKBOX"): 6,
    ("CARET", "start_addedToControlFieldStack", "EDITABLETEXT"): 6,
    ("CARET", "start_addedToControlFieldStack", "LINK"): 16,
    ("CARET", "start_addedToControlFieldStack", "LISTITEM"): 3,
    ("CARET", "start_addedToControlFieldStack", "STATICTEXT"): 6,
    ("CARET", "start_relative", "LINK"): 15,
    ("QUICKNAV", "start_addedToControlFieldStack", "LINK"): 40,
    ("SAYALL", "start_addedToControlFieldStack", "BUTTON"): 2,
    ("SAYALL", "start_addedToControlFieldStack", "CHECKBOX"): 2,
    ("SAYALL", "start_addedToControlFieldStack", "LINK"): 10,
}


def test_the_fixture_is_the_measured_dataset():
    records = _records()
    assert len(records) == 106
    assert sum(record["calls"] for record in records) == 2473


def test_exactly_the_expected_triples_play():
    played = {
        (record["reason"], record["fieldType"], record["role"]): record["calls"]
        for record in _records()
        if _plays(record)
    }
    assert played == EXPECTED_PLAYS


def test_the_focus_reason_never_plays():
    """`event_gainFocus` already played it. The dedup is this exclusion."""
    focus = [record for record in _records() if record["reason"] == "FOCUS"]
    assert sum(record["calls"] for record in focus) == 374
    assert not any(_plays(record) for record in focus)


def test_the_onlycache_reason_never_plays():
    only_cache = [record for record in _records() if record["reason"] == "ONLYCACHE"]
    assert sum(record["calls"] for record in only_cache) == 360
    assert not any(_plays(record) for record in only_cache)


def test_end_field_types_never_play():
    ends = [record for record in _records() if record["fieldType"].startswith("end")]
    assert sum(record["calls"] for record in ends) == 1293
    assert not any(_plays(record) for record in ends)


def test_in_stack_repeats_never_play():
    """The Word bug a bare `start*` filter would ship.

    `start_inControlFieldStack` fires for every field the caret is *already*
    inside. In the 40-keypress Word pass that is the enclosing EDITABLETEXT,
    76 times: roughly two editable-text sounds per line, forever.
    """
    in_stack = [
        record
        for record in _records()
        if record["fieldType"] == "start_inControlFieldStack"
    ]
    assert not any(_plays(record) for record in in_stack)

    word_repeats = [
        record
        for record in in_stack
        if record["reason"] == "CARET" and record["role"] == "EDITABLETEXT"
    ]
    assert sum(record["calls"] for record in word_repeats) == 76


def test_a_bare_start_filter_would_nearly_double_the_sounds():
    """What the fieldType clause is worth, in the dataset's own numbers."""
    naive = sum(
        record["calls"]
        for record in _records()
        if record["reason"] in playback.PLAY_REASONS
        and record["fieldType"].startswith("start")
        and _slot(record["role"]) is not None
    )
    assert naive == 203
    assert sum(EXPECTED_PLAYS.values()) == 113


def test_unmapped_roles_never_play():
    unmapped = [record for record in _records() if _slot(record["role"]) is None]
    assert {record["role"] for record in unmapped} == {
        "DOCUMENT",
        "GROUPING",
        "HEADING",
        "LABEL",
        "LANDMARK",
        "LIST",
        "PARAGRAPH",
        "SECTION",
    }
    assert not any(_plays(record) for record in unmapped)


def test_silence_during_say_all_removes_exactly_the_say_all_plays():
    silenced = {
        (record["reason"], record["fieldType"], record["role"])
        for record in _records()
        if _plays(record) and not _plays(record, silence_during_say_all=True)
    }
    assert silenced == {
        key for key in EXPECTED_PLAYS if key[0] == playback.SAY_ALL_REASON
    }
    assert all(
        _plays(record, silence_during_say_all=True)
        for record in _records()
        if _plays(record) and record["reason"] != playback.SAY_ALL_REASON
    )


@pytest.mark.parametrize("reason", [None, "", "QUERY", "CHANGE", "MOUSE", "sayall"])
def test_reasons_outside_the_set_never_play(reason):
    assert (
        playback.decide(
            playback.ControlField(
                reason=reason,
                field_type="start_addedToControlFieldStack",
                slot="link",
                synth_reports_indexes=True,
            ),
            _config(),
        )
        is playback.SILENT
    )


@pytest.mark.parametrize("field_type", [None, "", "start", "start_relative_extra"])
def test_field_types_outside_the_set_never_play(field_type):
    assert (
        playback.decide(
            playback.ControlField(
                reason="CARET",
                field_type=field_type,
                slot="link",
                synth_reports_indexes=True,
            ),
            _config(),
        )
        is playback.SILENT
    )


# --- lead or ride: when a reading-path sound fires (ADR 0002) --------------

#: Of the 113 plays, the ones that lead speech (play at decision time): fields
#: the navigation landed inside, under the two reasons whose utterance starts
#: immediately. Written out for the same reason `EXPECTED_PLAYS` is.
EXPECTED_LEADS = {
    ("CARET", "start_addedToControlFieldStack", "BUTTON"): 7,
    ("CARET", "start_addedToControlFieldStack", "CHECKBOX"): 6,
    ("CARET", "start_addedToControlFieldStack", "EDITABLETEXT"): 6,
    ("CARET", "start_addedToControlFieldStack", "LINK"): 16,
    ("CARET", "start_addedToControlFieldStack", "LISTITEM"): 3,
    ("CARET", "start_addedToControlFieldStack", "STATICTEXT"): 6,
    ("QUICKNAV", "start_addedToControlFieldStack", "LINK"): 40,
}


def test_exactly_the_entered_fields_lead():
    leads = {
        (record["reason"], record["fieldType"], record["role"]): record["calls"]
        for record in _records()
        if _verdict(record) is playback.LEAD
    }
    assert leads == EXPECTED_LEADS
    assert sum(EXPECTED_LEADS.values()) == 84


def test_the_rest_of_the_plays_ride():
    rides = {
        (record["reason"], record["fieldType"], record["role"]): record["calls"]
        for record in _records()
        if _verdict(record) is playback.RIDE
    }
    assert rides == {
        key: calls for key, calls in EXPECTED_PLAYS.items() if key not in EXPECTED_LEADS
    }
    assert sum(rides.values()) == 113 - 84


def test_say_all_rides_even_into_an_entered_field():
    """Read-ahead queues even the utterance start, so say-all never leads."""
    assert (
        _verdict({"reason": "SAYALL", "fieldType": playback.FIELD_ENTERED, "role": "LINK"})
        is playback.RIDE
    )


@pytest.mark.parametrize("reason", ["CARET", "QUICKNAV"])
def test_traversed_fields_ride(reason):
    """`start_relative` is announced mid-utterance -- the #52 burst case."""
    assert (
        _verdict({"reason": reason, "fieldType": "start_relative", "role": "LINK"})
        is playback.RIDE
    )


@pytest.mark.parametrize("reason", ["CARET", "QUICKNAV"])
def test_entered_fields_lead(reason):
    assert (
        _verdict({"reason": reason, "fieldType": playback.FIELD_ENTERED, "role": "LINK"})
        is playback.LEAD
    )


def test_a_non_playing_field_cannot_ride():
    """The old split's ordering precondition, dissolved.

    `should_ride_speech` was only meaningful after `should_play_control_field`
    said play -- a caller obligation. One verdict has no order to get wrong:
    a triple that does not play is SILENT, never RIDE, even where the old
    ride predicate alone would have said ride.
    """
    assert (
        _verdict({"reason": "FOCUS", "fieldType": "start_relative", "role": "LINK"})
        is playback.SILENT
    )


# --- the role-announcement gate, formerly inline in GlobalPlugin -----------


def test_speech_only_silences_every_measured_call():
    assert all(
        _verdict(record, role_announcement="speechOnly") is playback.SILENT
        for record in _records()
    )


def test_sounds_and_speech_plays_exactly_what_sounds_plays():
    """The setting splits speech, not sounds: both sound values are identical."""
    for record in _records():
        assert _verdict(record) is _verdict(record, role_announcement="soundsAndSpeech")


@pytest.mark.parametrize("role_announcement", ["sounds", "soundsAndSpeech"])
def test_an_object_event_with_a_mapped_role_leads(role_announcement):
    assert (
        playback.decide(
            playback.ObjectEvent(slot="button"), _config(role_announcement=role_announcement)
        )
        is playback.LEAD
    )


def test_speech_only_silences_object_events():
    assert (
        playback.decide(
            playback.ObjectEvent(slot="button"), _config(role_announcement="speechOnly")
        )
        is playback.SILENT
    )


def test_an_object_event_without_a_slot_is_silent():
    assert playback.decide(playback.ObjectEvent(slot=None), _config()) is playback.SILENT


# --- the synth-index override, formerly inline in GlobalPlugin -------------


def test_a_synth_without_indexes_downgrades_every_ride_to_a_lead():
    """No callback can fire on a synth that never reports reaching one.

    Across the whole dataset: what was silent stays silent, and every play --
    the 29 rides included -- leads instead. Bursts and all, rather than a
    sound that never fires.
    """
    for record in _records():
        with_indexes = _verdict(record)
        without_indexes = _verdict(record, synth_reports_indexes=False)
        if with_indexes is playback.SILENT:
            assert without_indexes is playback.SILENT
        else:
            assert without_indexes is playback.LEAD


# --- the suppression predicate, formerly inline in the speech hook ---------


@pytest.mark.parametrize(
    "role_announcement,expected",
    [
        ("sounds", True),
        ("soundsAndSpeech", False),
        ("speechOnly", False),
    ],
)
def test_only_sounds_suppresses_the_spoken_role(role_announcement, expected):
    assert (
        playback.should_suppress_spoken_role(
            "button", _config(role_announcement=role_announcement), degraded=False
        )
        is expected
    )


def test_a_degraded_session_suppresses_nothing():
    """Speech-only means NVDA speaks roles as if the addon were absent."""
    assert (
        playback.should_suppress_spoken_role("button", _config(), degraded=True) is False
    )


def test_an_unmapped_role_is_never_suppressed():
    """No sound stands in for it, so the spoken role must survive."""
    assert playback.should_suppress_spoken_role(None, _config(), degraded=False) is False


def test_suppression_tracks_exactly_the_mapped_roles_of_the_dataset():
    """Suppress iff a sound stands in: the slot mapping is the whole split."""
    for record in _records():
        assert playback.should_suppress_spoken_role(
            _slot(record["role"]), _config(), degraded=False
        ) is (_slot(record["role"]) is not None)


# --- "can I produce a role sound?" ----------------------------------------


def _outcome(**overrides):
    outcome = {"engine_ready": True, "device_open": True, "slots_loaded": 14}
    outcome.update(overrides)
    return outcome


def test_a_complete_outcome_can_produce_a_role_sound():
    assert playback.can_produce_role_sound(_outcome()) is True
    assert playback.degraded_cause(_outcome()) is None


@pytest.mark.parametrize(
    "overrides,cause",
    [
        ({"engine_ready": False, "device_open": False}, "audio engine"),
        ({"device_open": False}, "output device"),
        ({"slots_loaded": 0}, "sound theme samples"),
    ],
)
def test_a_missing_precondition_degrades_and_names_itself(overrides, cause):
    outcome = _outcome(**overrides)
    assert playback.can_produce_role_sound(outcome) is False
    assert cause in playback.degraded_cause(outcome)


def test_an_outcome_missing_a_key_degrades():
    """Anything the wiring forgot to fill counts as missing, not as fine."""
    assert playback.can_produce_role_sound({}) is False
    assert playback.can_produce_role_sound({"engine_ready": True}) is False


def test_a_device_opens_but_a_broken_theme_still_degrades():
    """Silence with suppression standing is the failure, not the fallback."""
    assert playback.can_produce_role_sound(_outcome(slots_loaded=0)) is False
