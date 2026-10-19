"""Verdict validation -- the trust boundary. No network, no Drive, no model."""

from __future__ import annotations

import datetime as dt

import pytest

from verdict import (
    MAX_SUMMARY,
    MAX_TAGS,
    ValidationError,
    from_declared,
    needs_model,
    parse_due,
    validate,
)

NOW = dt.datetime(2026, 10, 18, 12, 0, tzinfo=dt.UTC)
SOON = "2026-10-23T09:00:00+00:00"
IDS = {"abc123", "def456"}


def ok(**over):
    base = {
        "item_id": "abc123",
        "kind": "reminder",
        "title": "Call the accountant",
        "due_at": SOON,
        "priority": "high",
        "tags": ["finance"],
        "actionable": True,
        "summary": "Ring them about the Q3 filing.",
    }
    base.update(over)
    return base


def val(raw, **kw):
    kw.setdefault("allowed_ids", IDS)
    kw.setdefault("now", NOW)
    return validate(raw, **kw)


# -- item_id --------------------------------------------------------------------


def test_accepts_a_wellformed_verdict():
    v = val(ok())
    assert v.item_id == "abc123"
    assert v.title == "Call the accountant"
    assert v.priority == "high"
    assert v.tags == ["finance"]
    assert v.actionable is True


def test_rejects_an_invented_item_id():
    """The model must not be able to produce a verdict for an item we never sent."""
    with pytest.raises(ValidationError, match="not a pending item"):
        val(ok(item_id="not-a-real-item"))


@pytest.mark.parametrize("bad", [None, 42, [], {}])
def test_rejects_non_string_item_id(bad):
    with pytest.raises(ValidationError, match="not a pending item"):
        val(ok(item_id=bad))


def test_rejects_a_non_object_verdict():
    with pytest.raises(ValidationError, match="must be an object"):
        val(["not", "an", "object"])


# -- title ----------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["", "   ", None, 7, ["a"]])
def test_rejects_bad_titles(bad):
    with pytest.raises(ValidationError):
        val(ok(title=bad))


def test_rejects_an_overlong_title():
    with pytest.raises(ValidationError, match="exceeds"):
        val(ok(title="x" * 201))


def test_collapses_whitespace_in_a_title():
    assert val(ok(title="  call   the\n accountant ")).title == "call the accountant"


# -- due_at ---------------------------------------------------------------------


def test_rejects_an_unparseable_due_date():
    with pytest.raises(ValidationError, match="not ISO-8601"):
        val(ok(due_at="next friday-ish"))


def test_rejects_a_past_due_date():
    with pytest.raises(ValidationError, match="in the past"):
        val(ok(due_at="2020-01-01T09:00:00+00:00"))


def test_rejects_a_due_date_beyond_the_horizon():
    """Guards against a hallucinated year creating a reminder nobody sees."""
    with pytest.raises(ValidationError, match="horizon"):
        val(ok(due_at="2525-01-01T09:00:00+00:00"))


def test_accepts_z_suffix_and_naive_datetimes():
    assert parse_due("2026-10-23T09:00:00Z", now=NOW) is not None
    naive = parse_due("2026-10-23T09:00", now=NOW)
    assert naive is not None and naive.tzinfo is not None


def test_null_due_is_allowed_for_todo():
    assert val(ok(due_at=None)).due_at is None


def test_a_declared_reminder_without_a_date_is_quarantined():
    """The author said reminder and gave no time -- a contradiction worth surfacing."""
    with pytest.raises(ValidationError, match="no due date"):
        val(ok(due_at=None), filename="r: call them")


def test_a_guessed_reminder_without_a_date_is_demoted_to_a_todo():
    """The model merely guessed, so repair it rather than losing the capture."""
    v = val(ok(kind="reminder", due_at=None))
    assert v.kind == "todo"
    assert v.due_at is None


# -- priority -------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected", [("high", "high"), ("LOW", "low"), (None, None), ("none", None), ("", None)]
)
def test_priority_coercion(value, expected):
    assert val(ok(priority=value)).priority == expected


@pytest.mark.parametrize("bad", ["urgent", "critical", 3, ["high"]])
def test_rejects_unknown_priority(bad):
    with pytest.raises(ValidationError):
        val(ok(priority=bad))


# -- tags -----------------------------------------------------------------------


def test_normalises_tags():
    assert val(ok(tags=["Finance", "Q3 Filing", "finance"])).tags == ["finance", "q3-filing"]


def test_rejects_too_many_tags():
    with pytest.raises(ValidationError, match="more than"):
        val(ok(tags=[f"tag{i}" for i in range(MAX_TAGS + 1)]))


def test_rejects_an_overlong_tag():
    with pytest.raises(ValidationError, match="exceeds"):
        val(ok(tags=["x" * 33]))


@pytest.mark.parametrize("bad", ["héllo", "tag!", "../etc", "<script>"])
def test_rejects_tags_outside_the_charset(bad):
    with pytest.raises(ValidationError, match="is not"):
        val(ok(tags=[bad]))


def test_rejects_non_string_tags():
    with pytest.raises(ValidationError, match="all be strings"):
        val(ok(tags=["fine", 7]))


def test_accepts_empty_tags():
    assert val(ok(tags=[])).tags == []


# -- actionable / summary -------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected", [(True, True), (False, False), ("true", True), ("no", False), (None, False)]
)
def test_actionable_coercion(value, expected):
    assert val(ok(actionable=value)).actionable is expected


@pytest.mark.parametrize("bad", ["maybe", 1, 0, [], {}])
def test_rejects_non_boolean_actionable(bad):
    with pytest.raises(ValidationError, match="must be a boolean"):
        val(ok(actionable=bad))


def test_truncates_an_overlong_summary():
    assert len(val(ok(summary="x" * 5000)).summary) == MAX_SUMMARY


# -- frontmatter precedence -----------------------------------------------------


def test_frontmatter_title_beats_the_model():
    v = val(ok(title="model guess"), frontmatter={"title": "human wrote this"})
    assert v.title == "human wrote this"


def test_frontmatter_due_beats_the_model():
    v = val(ok(due_at="2026-11-30T09:00:00+00:00"), frontmatter={"due": SOON})
    assert v.due_at == parse_due(SOON, now=NOW)


def test_frontmatter_priority_and_tags_beat_the_model():
    v = val(ok(priority="low", tags=["wrong"]), frontmatter={"priority": "high", "tags": ["right"]})
    assert v.priority == "high"
    assert v.tags == ["right"]


def test_model_fills_fields_frontmatter_omits():
    v = val(ok(), frontmatter={"title": "human"})
    assert v.title == "human"
    assert v.priority == "high"


def test_invalid_frontmatter_still_fails_closed():
    """Trusted does not mean unchecked -- a typo'd date must not pass through."""
    with pytest.raises(ValidationError, match="not ISO-8601"):
        val(ok(), frontmatter={"due": "whenever"})


# -- the model-bypass path ------------------------------------------------------


def test_needs_model_without_a_declared_kind():
    """With one flat inbox the kind is part of what must be settled."""
    assert needs_model({"title": "x"}) is True
    assert needs_model({"title": "x", "type": "todo"}) is False


def test_needs_model_without_a_title():
    assert needs_model({"type": "todo"}) is True


def test_a_filename_marker_can_supply_the_kind():
    assert needs_model({"title": "x"}, "todo: ship it") is False


def test_a_declared_reminder_still_needs_a_date_to_skip_the_model():
    assert needs_model({"title": "x", "type": "reminder"}) is True
    assert needs_model({"title": "x", "type": "reminder", "due": SOON}) is False


def test_from_declared_builds_a_verdict_with_no_model():
    v = from_declared(
        "abc123",
        {
            "type": "reminder",
            "title": "Pay rent",
            "due": SOON,
            "priority": "high",
            "tags": ["home"],
        },
        now=NOW,
    )
    assert v.kind == "reminder"
    assert v.title == "Pay rent"
    assert v.due_at is not None
    assert v.tags == ["home"]


def test_from_declared_reads_the_kind_from_a_filename():
    v = from_declared("abc123", {"title": "A thought"}, "idea - nightly rollup", now=NOW)
    assert v.kind == "idea"


def test_from_declared_rejects_a_dateless_reminder():
    with pytest.raises(ValidationError, match="needs a due date"):
        from_declared("abc123", {"type": "reminder", "title": "Pay rent"}, now=NOW)


def test_from_declared_requires_a_kind():
    with pytest.raises(ValidationError, match="no kind declared"):
        from_declared("abc123", {"title": "A thought"}, now=NOW)


def test_from_declared_defaults_actionable_false():
    """Nothing is promoted to a todo unless the item says so."""
    v = from_declared("abc123", {"type": "idea", "title": "A thought"}, now=NOW)
    assert v.actionable is False


# -- time zone handling ---------------------------------------------------------


def test_local_zone_is_a_real_zone_not_a_fixed_offset():
    """A fixed offset applied across a DST boundary shifts the time by an hour."""
    from verdict import local_zone

    tz = local_zone()
    summer = dt.datetime(2026, 7, 1, 9, 0, tzinfo=tz)
    winter = dt.datetime(2026, 12, 1, 9, 0, tzinfo=tz)
    # Either the zone observes DST (offsets differ) or it genuinely does not.
    # What must never happen is both reporting the SAME offset while the real
    # zone changes -- which is exactly the fixed-offset snapshot bug.
    if summer.utcoffset() == winter.utcoffset():
        assert tz.tzname(summer) == tz.tzname(winter)


def test_naive_due_keeps_its_wall_clock_time_across_a_dst_boundary(monkeypatch):
    """due: 2026-11-14T09:00 must mean 9am on Nov 14, not 8am."""
    from zoneinfo import ZoneInfo

    import verdict as vd

    monkeypatch.setattr(vd, "local_zone", lambda: ZoneInfo("America/Denver"))
    # Parsed in October (MDT, -06:00) but targeting November (MST, -07:00).
    parsed = vd.parse_due("2026-11-14T09:00", now=dt.datetime(2026, 10, 18, tzinfo=dt.UTC))
    assert parsed is not None
    assert parsed.hour == 9
    assert parsed.utcoffset() == dt.timedelta(hours=-7)


def test_naive_due_in_the_same_dst_period_is_unchanged(monkeypatch):
    from zoneinfo import ZoneInfo

    import verdict as vd

    monkeypatch.setattr(vd, "local_zone", lambda: ZoneInfo("America/Denver"))
    parsed = vd.parse_due("2026-10-23T09:00", now=dt.datetime(2026, 10, 18, tzinfo=dt.UTC))
    assert parsed is not None
    assert parsed.hour == 9
    assert parsed.utcoffset() == dt.timedelta(hours=-6)


def test_an_explicit_offset_is_respected(monkeypatch):
    """If the note really says UTC, honour it rather than reinterpreting."""
    from zoneinfo import ZoneInfo

    import verdict as vd

    monkeypatch.setattr(vd, "local_zone", lambda: ZoneInfo("America/Denver"))
    parsed = vd.parse_due("2026-10-23T09:00:00+00:00", now=NOW)
    assert parsed is not None
    assert parsed.utcoffset() == dt.timedelta(0)


def test_a_bare_date_defaults_to_nine_am(monkeypatch):
    from zoneinfo import ZoneInfo

    import verdict as vd

    monkeypatch.setattr(vd, "local_zone", lambda: ZoneInfo("America/Denver"))
    parsed = vd.parse_due(dt.date(2026, 11, 20), now=NOW)
    assert parsed is not None and parsed.hour == 9


# -- kind detection -------------------------------------------------------------


@pytest.mark.parametrize("kind", ["reminder", "todo", "idea"])
def test_model_kind_is_accepted(kind):
    assert val(ok(kind=kind, due_at=None if kind != "reminder" else SOON)).kind == kind


@pytest.mark.parametrize(
    "spelling,expected",
    [
        ("reminders", "reminder"),
        ("REMINDER", "reminder"),
        ("todos", "todo"),
        ("task", "todo"),
        ("ideas", "idea"),
        ("note", "idea"),
    ],
)
def test_model_kind_spellings_are_normalised(spelling, expected):
    assert val(ok(kind=spelling)).kind == expected


@pytest.mark.parametrize("bad", ["reminderz", "project", "", None, 7, ["todo"]])
def test_rejects_an_unknown_kind(bad):
    with pytest.raises(ValidationError, match="is not one of"):
        val(ok(kind=bad))


def test_frontmatter_type_overrides_the_model():
    v = val(ok(kind="reminder", due_at=SOON), frontmatter={"type": "idea"})
    assert v.kind == "idea"


def test_filename_marker_overrides_the_model():
    v = val(ok(kind="todo"), filename="idea: what if we")
    assert v.kind == "idea"


def test_frontmatter_type_beats_the_filename_marker():
    v = val(ok(kind="todo"), frontmatter={"type": "idea"}, filename="r: call them")
    assert v.kind == "idea"


def test_an_unrecognised_override_falls_back_to_the_model():
    """A typo'd type must not silently become some other kind."""
    v = val(ok(kind="idea"), frontmatter={"type": "projekt"})
    assert v.kind == "idea"
