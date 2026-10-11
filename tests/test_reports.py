"""Weekly report: penalty state machine and due-time logic."""
from datetime import datetime

import pytz

from wgcop.reports import (
    _build_weekly_report,
    _parse_report_time,
    _weekly_report_is_due,
    _weekly_report_target_for_week,
)

BERLIN = pytz.timezone("Europe/Berlin")


def _data(chores, violators=None, penalties=None):
    return {
        "members": [{"name": n, "status": "active"} for n in chores],
        "chores": dict(chores),
        "last_week_violators": dict(violators or {}),
        "penalties": dict(penalties or {}),
        "chore_log": [],
        "expenses": [],
    }


def test_first_week_lagging_is_a_warning_not_a_beer():
    data = _data({"Alice": 10, "Bob": 2})
    text = _build_weekly_report(data)
    assert "Bob is 8 pts behind Alice" in text
    assert data["last_week_violators"] == {"bob": True}
    assert data["penalties"] == {}


def test_second_week_lagging_adds_one_beer():
    data = _data({"Alice": 10, "Bob": 2}, violators={"bob": True})
    text = _build_weekly_report(data)
    assert "Bob owes 1 beer(s)!" in text
    assert data["penalties"] == {"Bob": 1}
    # still flagged for next week
    assert data["last_week_violators"] == {"bob": True}


def test_beers_accumulate_each_lagging_week():
    data = _data({"Alice": 10, "Bob": 2}, violators={"bob": True}, penalties={"Bob": 3})
    _build_weekly_report(data)
    assert data["penalties"] == {"Bob": 4}


def test_catching_up_clears_flag_but_keeps_debt():
    data = _data({"Alice": 10, "Bob": 8}, violators={"bob": True}, penalties={"Bob": 2})
    text = _build_weekly_report(data)
    assert "Comeback of the week: Bob" in text
    assert "Bob still owes 2 beer(s) (caught up, debt remains)" in text
    assert data["last_week_violators"] == {}
    assert data["penalties"] == {"Bob": 2}


def test_gap_of_exactly_four_is_not_a_violation():
    data = _data({"Alice": 10, "Bob": 6})
    text = _build_weekly_report(data)
    assert "no penalties this week" in text
    assert data["last_week_violators"] == {}


def test_no_members_gives_placeholder():
    assert _build_weekly_report({"members": [], "chores": {}}) == "Mario's Monday Mauling: No data yet."


def test_parse_report_time_defaults():
    assert _parse_report_time("18:05") == (18, 5)
    assert _parse_report_time("garbage") == (9, 30)
    assert _parse_report_time("") == (9, 30)


def test_target_is_monday_of_reference_week():
    wednesday = BERLIN.localize(datetime(2026, 9, 23, 15, 0))
    target = _weekly_report_target_for_week(wednesday, hour=9, minute=30)
    assert target.weekday() == 0
    assert (target.year, target.month, target.day, target.hour, target.minute) == (2026, 9, 21, 9, 30)


def test_due_logic_around_monday():
    monday_early = BERLIN.localize(datetime(2026, 9, 21, 9, 0))
    tuesday = BERLIN.localize(datetime(2026, 9, 22, 10, 0))
    data = {"weekly_report_time": "09:30", "weekly_report_meta": {"last_sent": None}}
    assert not _weekly_report_is_due(data, reference=monday_early)
    assert _weekly_report_is_due(data, reference=tuesday)
    data["weekly_report_meta"]["last_sent"] = BERLIN.localize(datetime(2026, 9, 21, 9, 31)).isoformat()
    assert not _weekly_report_is_due(data, reference=tuesday)
    data["weekly_report_meta"]["last_sent"] = BERLIN.localize(datetime(2026, 9, 14, 9, 31)).isoformat()
    assert _weekly_report_is_due(data, reference=tuesday)


def test_retired_members_are_left_out_of_standings_and_penalties():
    data = _data({"Alice": 10, "Bob": 2, "Zed": 30}, penalties={"Zed": 3})
    data["members"][2]["status"] = "retired"
    text = _build_weekly_report(data)
    assert "Leader: Alice with 10 points" in text
    assert "Zed" not in text
    assert data["last_week_violators"] == {"bob": True}
    assert data["penalties"] == {"Zed": 3}   # frozen, untouched
