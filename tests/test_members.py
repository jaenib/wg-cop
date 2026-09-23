"""Pure-logic regressions for member helpers, balances and renames."""
from wgcop.members import (
    _get_member_name,
    _get_member_status,
    _match_member_name,
    _normalise_member_name,
    _rename_member_in_data,
    calculate_balances,
)


def test_normalise_accepts_strings_and_member_objects():
    assert _normalise_member_name("  Alice ") == "alice"
    assert _normalise_member_name({"name": "ALICE"}) == "alice"
    assert _normalise_member_name("") == ""
    assert _normalise_member_name({"name": ""}) == ""


def test_member_accessors_default_status():
    assert _get_member_name("Bob") == "Bob"
    assert _get_member_name({"name": "Bob"}) == "Bob"
    assert _get_member_status("Bob") == "active"
    assert _get_member_status({"name": "Bob", "status": "vacating"}) == "vacating"


def test_match_member_name_is_case_insensitive(sample_members):
    assert _match_member_name(sample_members, "alice") is sample_members[0]
    assert _match_member_name(sample_members, "CARLA") is sample_members[2]
    assert _match_member_name(sample_members, "Dave") is None


def test_calculate_balances_splits_evenly(sample_members):
    data = {
        "members": sample_members,
        "expenses": [
            {"payer": "Alice", "amount": 30.0, "split_with": ["Alice", "Bob", "Carla"]},
            {"payer": "bob", "amount": 10.0, "split_with": ["Alice", "Bob"]},
            {"payer": "Nobody", "amount": 99.0, "split_with": ["Alice"]},  # unknown payer
            {"payer": "Carla", "amount": 5.0, "split_with": []},  # no splitters -> ignored
        ],
    }
    balances = calculate_balances(data)
    # Alice: +30 -10 (first) -5 (second) -99 (unknown payer still charges her share)
    assert round(balances["Alice"], 2) == round(30 - 10 - 5 - 99, 2)
    assert round(balances["Bob"], 2) == round(-10 + 10 - 5, 2)
    assert round(balances["Carla"], 2) == -10.0


def test_rename_member_touches_every_structure():
    data = {
        "members": [{"name": "Alice", "status": "active"}],
        "chores": {"Alice": 7},
        "penalties": {"Alice": 2},
        "last_week_violators": {"alice": True},
        "chore_log": [{"member": "alice", "points": 1}],
        "expenses": [{"payer": "Alice", "split_with": ["Alice", "Bob"]}],
    }
    _rename_member_in_data(data, "Alice", "Alicia")
    assert data["members"][0]["name"] == "Alicia"
    assert data["chores"] == {"Alicia": 7}
    assert data["penalties"] == {"Alicia": 2}
    assert data["last_week_violators"] == {"alicia": True}
    assert data["chore_log"][0]["member"] == "Alicia"
    assert data["expenses"][0]["payer"] == "Alicia"
    assert data["expenses"][0]["split_with"] == ["Alicia", "Bob"]
