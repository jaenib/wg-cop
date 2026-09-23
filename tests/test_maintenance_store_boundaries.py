"""Independent checks of failure safety and cross-household store boundaries."""
import json

import pytest

from tests.legacy_fixture import legacy_data, legacy_seed
from wgcop.store import Store, StoreError, has_access


@pytest.fixture
def homes(tmp_path):
    store = Store(tmp_path / "state.json")
    store.initialize()
    store.create_home(-100, "One", 10, "Alice")
    store.create_home(-200, "Two", 20, "Bob")
    return store


@pytest.mark.parametrize("uid", [None, False, True, 0, "10"])
def test_invalid_identity_never_grants_access(homes, uid):
    assert not has_access(homes.get_home(-100), uid)
    assert homes.homes_for_user(uid) == []


@pytest.mark.parametrize("key,value", [("members", {}), ("expenses", {}), ("chores", []), ("penalties", [])])
def test_malformed_legacy_collections_do_not_get_silently_replaced(tmp_path, key, value):
    old = legacy_data()
    old[key] = value
    path = tmp_path / "legacy.json"
    original = json.dumps(old)
    path.write_text(original)
    with pytest.raises(StoreError):
        Store(path).initialize(legacy_seed())
    assert path.read_text() == original
    assert not path.with_name(path.name + ".pre-multihome.bak").exists()


def test_nonfinite_stored_numbers_fail_closed(homes):
    data = homes.read()
    data["homes"]["-100"]["expenses"] = [{"amount": float("nan")}]
    original = json.dumps(data)
    homes.path.write_text(original)
    with pytest.raises(StoreError):
        homes.initialize()
    assert homes.path.read_text() == original


def test_group_migration_collision_preserves_both_homes(homes):
    before = homes.path.read_bytes()
    with pytest.raises(StoreError):
        homes.migrate_group(-100, -200)
    assert homes.path.read_bytes() == before


def test_reads_do_not_write_or_change_revision(homes):
    original = homes.path.read_bytes()
    modified = homes.path.stat().st_mtime_ns
    for _ in range(3):
        homes.get_home(-100)
        homes.homes_for_user(10)
        homes.list_homes()
    assert homes.path.read_bytes() == original
    assert homes.path.stat().st_mtime_ns == modified


def test_saving_one_home_keeps_interleaved_changes_in_other_home(homes):
    first = homes.get_home(-100)
    homes.update(-200, lambda home: home["chores"].update(Bob=8))
    first["chores"]["Alice"] = 5
    homes.save_home(first)
    assert homes.get_home(-200)["chores"] == {"Bob": 8}
    assert homes.get_home(-100)["chores"] == {"Alice": 5}


def test_revoked_user_cannot_reuse_private_home_selection(homes):
    homes.select_home(10, -100)
    def revoke(home):
        home["roles"]["admins"] = [30]
        home["roles"]["handler"] = None
        home["members"][0].pop("uid")
    homes.update(-100, revoke)
    assert homes.selected_home(10) is None
    with pytest.raises(StoreError):
        homes.select_home(10, -100)
