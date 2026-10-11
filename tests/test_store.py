"""Multi-home store: envelope validation, legacy migration, revisions, selections."""
import json

import pytest

from tests.legacy_fixture import LEGACY_GROUP_ID, legacy_data, legacy_seed
from wgcop import store as store_mod
from wgcop.members import calculate_balances
from wgcop.store import (
    HomeNotFound,
    StaleHomeError,
    Store,
    StoreError,
    has_access,
    is_admin,
    member_by_mid,
    member_for_uid,
    migrate_legacy,
    new_home,
    validate_envelope,
)


@pytest.fixture
def path(tmp_path):
    return tmp_path / "wg_data_alpha.json"


@pytest.fixture
def legacy_store(path):
    path.write_text(json.dumps(legacy_data()), encoding="utf-8")
    s = Store(path)
    assert s.initialize(legacy_seed()) == "migrated"
    return s


# ---------------------------------------------------------------- initialisation
def test_fresh_store_is_created_empty(path):
    s = Store(path)
    assert s.initialize() == "created"
    env = json.loads(path.read_text())
    assert env == {"schema_version": 2, "homes": {}, "selections": {}}
    assert s.initialize() == "ok"


def test_corrupt_json_is_refused_and_left_untouched(path):
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(StoreError):
        Store(path).initialize(legacy_seed())
    assert path.read_text() == "{not json"
    assert not path.with_name(path.name + ".pre-multihome.bak").exists()


def test_unknown_schema_version_is_refused(path):
    path.write_text(json.dumps({"schema_version": 3, "homes": {}, "selections": {}}))
    with pytest.raises(StoreError):
        Store(path).initialize()


def test_unrecognised_legacy_shape_is_refused(path):
    path.write_text(json.dumps({"foo": 1}))
    with pytest.raises(StoreError, match="migration refused"):
        Store(path).initialize(legacy_seed())
    assert json.loads(path.read_text()) == {"foo": 1}


def test_legacy_without_group_or_admin_is_refused(path):
    old = legacy_data()
    del old["group_chat_id"]
    with pytest.raises(StoreError, match="group chat id"):
        migrate_legacy(old, {"admin_uid": 1001})
    with pytest.raises(StoreError, match="BOT_HANDLER_ID"):
        migrate_legacy(legacy_data(), {"admin_uid": None})


# ------------------------------------------------------------------- migration
def test_migration_keeps_every_record_and_balances(legacy_store, path):
    old = legacy_data()
    home = legacy_store.get_home(LEGACY_GROUP_ID)
    assert home["id"] == str(LEGACY_GROUP_ID)
    assert home["group_chat_id"] == LEGACY_GROUP_ID
    assert home["expenses"] == old["expenses"]
    assert home["chores"] == old["chores"]
    assert home["penalties"] == old["penalties"]
    assert home["last_week_violators"] == old["last_week_violators"]
    assert home["chore_log"] == old["chore_log"]
    assert home["weekly_report_meta"] == {"last_sent": "2026-09-21T13:12:05+02:00", "pending": None}
    assert home["chronicler_backup"]["greeting_sent"] is True
    assert home["settings"]["weekly_report_time"] == "13:12"
    assert home["settings"]["wg_hoeck_date"] == "2026-06-25"
    assert home["settings"]["currency"] == "CHF"
    assert calculate_balances(home) == calculate_balances(old)
    # archive holds the untouched original
    archive = path.with_name(path.name + ".pre-multihome.bak")
    assert json.loads(archive.read_text()) == old
    assert json.loads(path.read_text())["migrated_from_legacy"]["expenses"] == 4


def test_second_migration_keeps_every_pre_migration_copy(path):
    path.write_text(json.dumps(legacy_data()), encoding="utf-8")
    assert Store(path).initialize(legacy_seed()) == "migrated"
    archive = path.with_name(path.name + ".pre-multihome.bak")
    first = archive.read_bytes()

    # Rollback: the archive is restored, old code keeps writing, then new code starts again.
    newer = legacy_data()
    newer["expenses"][0]["description"] = "entered after the rollback"
    path.write_text(json.dumps(newer), encoding="utf-8")
    assert Store(path).initialize(legacy_seed()) == "migrated"

    assert archive.read_bytes() == first                      # the first archive is untouched
    extra = list(path.parent.glob(path.name + ".pre-multihome.bak.*"))
    assert len(extra) == 1 and json.loads(extra[0].read_text()) == newer
    assert extra[0].stat().st_mode & 0o077 == 0
    migrated = Store(path).get_home(LEGACY_GROUP_ID)
    assert migrated["expenses"][0]["description"] == "entered after the rollback"


def test_migration_seeds_roles_and_links_members(legacy_store):
    home = legacy_store.get_home(LEGACY_GROUP_ID)
    assert home["roles"] == {"admins": [1001], "chronicler": None, "handler": 1001}
    by_name = {m["name"]: m for m in home["members"]}
    assert by_name["Janidputzä"]["uid"] == 1006          # kept
    assert by_name["Nicci Lopez"]["uid"] == 1002         # kept, status kept
    assert by_name["Nicci Lopez"]["status"] == "vacating"
    assert by_name["General Guysan"]["uid"] == 1004      # exact seed match
    assert by_name["Gjango Gmüeshole"]["uid"] == 1003    # typo-tolerant seed match
    mids = [m["mid"] for m in home["members"]]
    assert len(set(mids)) == 5 and all(m.startswith("m") for m in mids)
    assert member_for_uid(home, 1003)["name"] == "Gjango Gmüeshole"
    assert member_by_mid(home, mids[0]) is home["members"][0]


def test_migration_is_idempotent(legacy_store, path):
    before = path.read_text()
    assert legacy_store.initialize(legacy_seed()) == "ok"
    assert path.read_text() == before


def test_migration_accepts_string_members_and_defaults(path):
    old = legacy_data()
    old["members"] = ["Alice", {"name": "Bob"}]
    old["weekly_report_time"] = "25:99"
    env = migrate_legacy(old, legacy_seed())
    home = next(iter(env["homes"].values()))
    assert [m["name"] for m in home["members"]] == ["Alice", "Bob"]
    assert all(m["status"] == "active" for m in home["members"])
    assert home["settings"]["weekly_report_time"] == "09:30"


def test_chronicler_seed_is_optional_and_validated():
    seed = legacy_seed()
    seed["chronicler_uid"] = "1042"
    env = migrate_legacy(legacy_data(), seed)
    assert next(iter(env["homes"].values()))["roles"]["chronicler"] == 1042
    seed["chronicler_uid"] = "your_chronicler_chatid"
    env = migrate_legacy(legacy_data(), seed)
    assert next(iter(env["homes"].values()))["roles"]["chronicler"] is None


# ------------------------------------------------------------------ validation
def test_validation_rejects_home_without_admin():
    home = new_home(-100, "x")
    with pytest.raises(StoreError, match="administrator"):
        validate_envelope({"schema_version": 2, "homes": {"-100": home}, "selections": {}})


def test_validation_rejects_key_mismatch_and_duplicate_uid():
    home = new_home(-100, "x")
    home["roles"]["admins"] = [1]
    with pytest.raises(StoreError, match="does not match"):
        validate_envelope({"schema_version": 2, "homes": {"-101": home}, "selections": {}})
    home["members"] = [
        {"mid": "m1", "name": "A", "status": "active", "uid": 5},
        {"mid": "m2", "name": "B", "status": "active", "uid": 5},
    ]
    with pytest.raises(StoreError, match="linked to two members"):
        validate_envelope({"schema_version": 2, "homes": {"-100": home}, "selections": {}})


def test_validation_rejects_dangling_selection():
    home = new_home(-100, "x")
    home["roles"]["admins"] = [1]
    with pytest.raises(StoreError, match="unknown home"):
        validate_envelope({"schema_version": 2, "homes": {"-100": home}, "selections": {"1": "-999"}})


# ------------------------------------------------------------------- writes
def test_update_bumps_revision_and_persists(legacy_store):
    before = legacy_store.get_home(LEGACY_GROUP_ID)["revision"]

    def add(home):
        home["chores"]["Nicci Lopez"] += 1
        return "done"

    home, result = legacy_store.update(LEGACY_GROUP_ID, add)
    assert result == "done"
    assert home["revision"] == before + 1
    assert legacy_store.get_home(LEGACY_GROUP_ID)["chores"]["Nicci Lopez"] == 13


def test_update_refuses_to_persist_invalid_state(legacy_store):
    def corrupt(home):
        home["roles"]["admins"] = []

    with pytest.raises(StoreError):
        legacy_store.update(LEGACY_GROUP_ID, corrupt)
    assert legacy_store.get_home(LEGACY_GROUP_ID)["roles"]["admins"] == [1001]


def test_save_home_rejects_stale_snapshot(legacy_store):
    snap_a = legacy_store.get_home(LEGACY_GROUP_ID)
    snap_b = legacy_store.get_home(LEGACY_GROUP_ID)
    snap_a["name"] = "A"
    legacy_store.save_home(snap_a)
    snap_b["name"] = "B"
    with pytest.raises(StaleHomeError):
        legacy_store.save_home(snap_b)
    assert legacy_store.get_home(LEGACY_GROUP_ID)["name"] == "A"
    assert snap_a["revision"] == legacy_store.get_home(LEGACY_GROUP_ID)["revision"]


def test_create_home_and_access(legacy_store):
    home = legacy_store.create_home(-200, "Flat 2", 77, "Zoe")
    assert home["roles"]["admins"] == [77]
    assert home["members"][0] == {"mid": "m1", "name": "Zoe", "status": "active", "uid": 77}
    assert is_admin(home, 77) and has_access(home, 77) and not has_access(home, 1001)
    with pytest.raises(StoreError, match="already has a home"):
        legacy_store.create_home(-200, "again", 77, "Zoe")
    with pytest.raises(StoreError):
        legacy_store.create_home(200, "positive id", 77, "Zoe")
    assert {h["id"] for h in legacy_store.homes_for_user(77)} == {"-200"}
    assert {h["id"] for h in legacy_store.homes_for_user(1002)} == {str(LEGACY_GROUP_ID)}
    assert legacy_store.home_for_group(-200)["name"] == "Flat 2"
    assert legacy_store.home_for_group(-201) is None


def test_selection_requires_access(legacy_store):
    legacy_store.create_home(-200, "Flat 2", 77, "Zoe")
    with pytest.raises(StoreError):
        legacy_store.select_home(1002, -200)
    legacy_store.select_home(1002, LEGACY_GROUP_ID)
    assert legacy_store.selected_home(1002)["id"] == str(LEGACY_GROUP_ID)
    legacy_store.clear_selection(1002)
    assert legacy_store.selected_home(1002) is None


def test_migrate_group_rekeys_home_and_selections(legacy_store):
    legacy_store.select_home(1002, LEGACY_GROUP_ID)
    home = legacy_store.migrate_group(LEGACY_GROUP_ID, -1009999)
    assert home["id"] == "-1009999" and home["group_chat_id"] == -1009999
    assert legacy_store.home_for_group(LEGACY_GROUP_ID) is None
    assert legacy_store.selected_home(1002)["id"] == "-1009999"
    assert legacy_store.migrate_group(-1, -2) is None


def test_export_contains_only_that_home(legacy_store):
    legacy_store.create_home(-200, "Flat 2", 77, "Zoe")
    payload = json.loads(legacy_store.export_home(-200))
    assert payload["home"]["name"] == "Flat 2"
    assert "Nicci Lopez" not in json.dumps(payload)


def test_delete_home_drops_selections(legacy_store):
    legacy_store.select_home(1002, LEGACY_GROUP_ID)
    legacy_store.delete_home(LEGACY_GROUP_ID)
    with pytest.raises(HomeNotFound):
        legacy_store.get_home(LEGACY_GROUP_ID)
    assert legacy_store.read()["selections"] == {}


def test_get_store_singleton_can_be_replaced(path):
    s = Store(path)
    store_mod.set_store(s)
    try:
        assert store_mod.get_store() is s
    finally:
        store_mod.set_store(None)
