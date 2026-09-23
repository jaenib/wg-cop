"""Admin menus that replace the hard-coded roles and constants: Manage Roles, Home Settings."""
import json

import pytest
from telegram.ext import ConversationHandler

from tests.fakes import FakeContext, FakeUser, keyboard_labels, run, text_update
from tests.legacy_fixture import LEGACY_GROUP_ID, legacy_data, legacy_seed
from wgcop import store as store_mod
from wgcop.handlers import roles, settings
from wgcop.homes import Caller, request_claim
from wgcop.keyboards import HOME_SETTINGS_MENU, HOME_SETTINGS_VALUE, ROLES_MENU, ROLES_PICK_USER
from wgcop.store import Store, member_for_uid

END = ConversationHandler.END
ADMIN = FakeUser(1001, "Admin")
NICCI = FakeUser(1002, "Nicci", "Lopez")


@pytest.fixture
def store(tmp_path):
    path = tmp_path / "wg_data_alpha.json"
    path.write_text(json.dumps(legacy_data()), encoding="utf-8")
    s = Store(path)
    s.initialize(legacy_seed())
    store_mod.set_store(s)
    yield s
    store_mod.set_store(None)


def home_of(store):
    return store.get_home(LEGACY_GROUP_ID)


# ------------------------------------------------------------------- roles
def test_roles_menu_requires_admin_and_shows_summary(store):
    u = text_update("Manage Roles", NICCI)
    assert run(roles.open_roles(u, FakeContext())) == END
    assert u.message.last_reply == "Unauthorized."
    u = text_update("Manage Roles", ADMIN)
    assert run(roles.open_roles(u, FakeContext())) == ROLES_MENU
    assert "Admins: user 1001" in u.message.last_reply
    assert "Chronicler (receives backups): not set" in u.message.last_reply
    assert "Handler (receives heartbeats): user 1001" in u.message.last_reply


def _unlink(store, name):
    def apply(h):
        for m in h["members"]:
            if m["name"] == name:
                m.pop("uid", None)

    store.update(LEGACY_GROUP_ID, apply)


def test_add_and_remove_admin_with_last_admin_protection(store):
    _unlink(store, "General Guysan")
    ctx = FakeContext()
    run(roles.open_roles(text_update("Manage Roles", ADMIN), ctx))
    u = text_update("Add Admin", ADMIN)
    assert run(roles.roles_menu(u, ctx)) == ROLES_PICK_USER
    labels = keyboard_labels(u.message.last_markup)
    assert "Nicci Lopez" in labels and "General Guysan" not in labels  # unlinked members not offered
    u = text_update("Ghost", ADMIN)
    assert run(roles.roles_pick(u, ctx)) == ROLES_PICK_USER
    u = text_update("Nicci Lopez", ADMIN)
    assert run(roles.roles_pick(u, ctx)) == ROLES_MENU
    assert home_of(store)["roles"]["admins"] == [1001, 1002]

    # Nicci (now admin) removes herself -> leaves the panel
    ctx2 = FakeContext()
    run(roles.open_roles(text_update("Manage Roles", NICCI), ctx2))
    run(roles.roles_menu(text_update("Remove Admin", NICCI), ctx2))
    u = text_update("Nicci Lopez", NICCI)
    assert run(roles.roles_pick(u, ctx2)) == END
    assert home_of(store)["roles"]["admins"] == [1001]
    assert "left the admin panel" in u.message.last_reply

    # the last admin cannot be removed
    run(roles.roles_menu(text_update("Remove Admin", ADMIN), ctx))
    u = text_update("user 1001", ADMIN)
    assert run(roles.roles_pick(u, ctx)) == ROLES_MENU
    assert "at least one administrator" in u.message.last_reply
    assert home_of(store)["roles"]["admins"] == [1001]


def test_set_and_clear_single_roles(store):
    ctx = FakeContext()
    run(roles.open_roles(text_update("Manage Roles", ADMIN), ctx))
    run(roles.roles_menu(text_update("Set Chronicler", ADMIN), ctx))
    u = text_update("Nicci Lopez", ADMIN)
    assert run(roles.roles_pick(u, ctx)) == ROLES_MENU
    assert home_of(store)["roles"]["chronicler"] == 1002
    assert ctx.bot.sent[-1][0] == 1002 and "chronicler" in ctx.bot.sent[-1][1]
    run(roles.roles_menu(text_update("Set Chronicler", ADMIN), ctx))
    run(roles.roles_pick(text_update("None", ADMIN), ctx))
    assert home_of(store)["roles"]["chronicler"] is None
    run(roles.roles_menu(text_update("Set Handler", ADMIN), ctx))
    run(roles.roles_pick(text_update("Thomath Sucker", ADMIN), ctx))
    assert home_of(store)["roles"]["handler"] == 1005


def test_unlink_member_keeps_bookkeeping(store):
    ctx = FakeContext()
    run(roles.open_roles(text_update("Manage Roles", ADMIN), ctx))
    run(roles.roles_menu(text_update("Unlink Member", ADMIN), ctx))
    run(roles.roles_pick(text_update("Thomath Sucker", ADMIN), ctx))
    home = home_of(store)
    assert member_for_uid(home, 1005) is None
    assert home["chores"]["Thomath Sucker"] == 9


def test_join_requests_are_listed_with_review_buttons(store):
    ctx = FakeContext()
    run(roles.open_roles(text_update("Manage Roles", ADMIN), ctx))
    u = text_update("Join Requests", ADMIN)
    run(roles.roles_menu(u, ctx))
    assert "No pending join requests" in u.message.last_reply
    _unlink(store, "General Guysan")
    unlinked = next(m for m in home_of(store)["members"] if not m.get("uid"))
    store.update(LEGACY_GROUP_ID, lambda h: request_claim(h, Caller(4242, "Sam", "sam"), unlinked["mid"]))
    u = text_update("Join Requests", ADMIN)
    assert run(roles.roles_menu(u, ctx)) == ROLES_MENU
    review = u.message.replies[-2]
    assert "Sam (@sam)" in review[0] and unlinked["name"] in review[0]
    assert review[1].inline_keyboard[0][0].callback_data == f"join:approve:{LEGACY_GROUP_ID}:4242"


def test_revoked_admin_cannot_continue_roles_flow(store):
    ctx = FakeContext()
    run(roles.open_roles(text_update("Manage Roles", ADMIN), ctx))
    store.update(LEGACY_GROUP_ID, lambda h: h["roles"].update(admins=[1002]))
    u = text_update("Add Admin", ADMIN)
    assert run(roles.roles_menu(u, ctx)) == END
    assert "no longer an administrator" in u.message.last_reply


# ---------------------------------------------------------------- settings
def test_home_settings_menu_and_validation(store):
    ctx = FakeContext()
    u = text_update("Home Settings", ADMIN)
    assert run(settings.open_home_settings(u, ctx)) == HOME_SETTINGS_MENU
    assert "Currency: CHF" in u.message.last_reply

    assert run(settings.home_settings_menu(text_update("Timezone", ADMIN), ctx)) == HOME_SETTINGS_VALUE
    u = text_update("Mars/Olympus", ADMIN)
    assert run(settings.home_settings_value(u, ctx)) == HOME_SETTINGS_VALUE
    assert "Unknown timezone" in u.message.last_reply
    assert run(settings.home_settings_value(text_update("europe/zurich", ADMIN), ctx)) == HOME_SETTINGS_MENU
    assert home_of(store)["settings"]["timezone"] == "Europe/Zurich"

    run(settings.home_settings_menu(text_update("Minutes per Point", ADMIN), ctx))
    assert run(settings.home_settings_value(text_update("0", ADMIN), ctx)) == HOME_SETTINGS_VALUE
    assert run(settings.home_settings_value(text_update("20", ADMIN), ctx)) == HOME_SETTINGS_MENU
    assert home_of(store)["settings"]["minutes_per_point"] == 20

    run(settings.home_settings_menu(text_update("Penalty Threshold", ADMIN), ctx))
    assert run(settings.home_settings_value(text_update("6", ADMIN), ctx)) == HOME_SETTINGS_MENU
    run(settings.home_settings_menu(text_update("Penalty Unit", ADMIN), ctx))
    assert run(settings.home_settings_value(text_update("pizza", ADMIN), ctx)) == HOME_SETTINGS_MENU
    run(settings.home_settings_menu(text_update("Currency", ADMIN), ctx))
    assert run(settings.home_settings_value(text_update("EUR", ADMIN), ctx)) == HOME_SETTINGS_MENU
    run(settings.home_settings_menu(text_update("Home Name", ADMIN), ctx))
    assert run(settings.home_settings_value(text_update("  ", ADMIN), ctx)) == HOME_SETTINGS_VALUE
    assert run(settings.home_settings_value(text_update("WG Sonnenberg", ADMIN), ctx)) == HOME_SETTINGS_MENU

    home = home_of(store)
    assert home["name"] == "WG Sonnenberg"
    assert home["settings"]["penalty_threshold_points"] == 6
    assert home["settings"]["penalty_unit"] == "pizza"
    assert home["settings"]["currency"] == "EUR"

    # Cancel returns to the menu; unknown choice is refused; non-admin blocked
    run(settings.home_settings_menu(text_update("Currency", ADMIN), ctx))
    assert run(settings.home_settings_value(text_update("Cancel", ADMIN), ctx)) == HOME_SETTINGS_MENU
    assert run(settings.home_settings_menu(text_update("Bogus", ADMIN), ctx)) == HOME_SETTINGS_MENU
    u = text_update("Home Settings", NICCI)
    assert run(settings.open_home_settings(u, FakeContext())) == END


def test_settings_take_effect_in_bookkeeping(store):
    from wgcop.handlers import chore, penalties

    store.update(LEGACY_GROUP_ID, lambda h: h["settings"].update(minutes_per_point=30, penalty_unit="pizza", currency="EUR"))
    ctx = FakeContext()
    run(chore.start_chore(text_update("Add Chore", NICCI), ctx))
    run(chore.chore_user(text_update("Nicci Lopez", NICCI), ctx))
    u = text_update("60", NICCI)
    run(chore.chore_minutes(u, ctx))
    assert "earned 2 points" in u.message.last_reply
    u = text_update("Check Penalties", NICCI)
    run(penalties.check_penalties(u, ctx))
    assert "pizza(s)" in u.message.last_reply
