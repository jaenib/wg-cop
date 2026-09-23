"""Home resolution, identity, flow namespacing, roles and joining."""
import json

import pytest
from telegram.error import TelegramError

from tests.fakes import (
    FakeBot,
    FakeChat,
    FakeContext,
    FakeMessage,
    FakeUpdate,
    FakeUser,
    keyboard_labels,
    run,
    text_update,
)
from tests.legacy_fixture import LEGACY_GROUP_ID, legacy_data, legacy_seed
from wgcop import homes
from wgcop.homes import (
    CB_HOME_SELECT,
    Caller,
    HomeError,
    add_admin,
    approve_claim,
    caller_from_update,
    clear_flows,
    deny_claim,
    end_flow,
    flow_home,
    flow_state,
    home_currency,
    home_tz,
    is_group_admin,
    is_group_member,
    join_as_new_member,
    link_member,
    member_has_history,
    owns_keyboard,
    remove_admin,
    remove_member,
    request_claim,
    require_admin,
    require_home,
    resolve_home,
    role_holders,
    set_single_role,
    start_flow,
    unlink_member,
)
from wgcop.store import Store, member_for_uid

NICCI = FakeUser(1002, "Nicci", "Lopez")
STRANGER = FakeUser(4242, "Sam")
ADMIN = FakeUser(1001, "Admin")


@pytest.fixture
def store(tmp_path):
    path = tmp_path / "wg_data_alpha.json"
    path.write_text(json.dumps(legacy_data()), encoding="utf-8")
    s = Store(path)
    s.initialize(legacy_seed())
    return s


def group_update(text, user, chat_id=LEGACY_GROUP_ID):
    return text_update(text, user, FakeChat(chat_id, "supergroup", title="WG"))


# ---------------------------------------------------------------- identity
def test_caller_requires_a_personal_account():
    assert caller_from_update(text_update("hi", NICCI)).uid == 1002
    assert caller_from_update(text_update("hi", NICCI)).name == "Nicci Lopez"
    assert caller_from_update(text_update("hi", FakeUser(5, "B", is_bot=True))) is None
    anon = text_update("hi", NICCI)
    anon.message.sender_chat = FakeChat(-1, "channel")
    assert caller_from_update(anon) is None
    assert caller_from_update(FakeUpdate(message=FakeMessage("x"), user=None, chat=FakeChat(1))) is None


# -------------------------------------------------------------- resolution
def test_group_resolution(store):
    res = resolve_home(group_update("Standings", NICCI), store)
    assert res.home["id"] == str(LEGACY_GROUP_ID) and res.reason is None
    assert resolve_home(group_update("Standings", STRANGER), store).reason == "not_member"
    assert resolve_home(group_update("Standings", NICCI, chat_id=-555), store).reason == "unlinked_group"


def test_dm_resolution_single_home(store):
    res = resolve_home(text_update("Standings", NICCI), store)
    assert res.home["id"] == str(LEGACY_GROUP_ID)
    assert resolve_home(text_update("Standings", STRANGER), store).reason == "no_homes"


def test_dm_resolution_with_two_homes_needs_a_choice(store):
    second = store.create_home(-200, "Beach House", 1002, "Nicci")
    res = resolve_home(text_update("Standings", NICCI), store)
    assert res.reason == "choose" and {h["id"] for h in res.choices} == {str(LEGACY_GROUP_ID), "-200"}
    store.select_home(1002, -200)
    assert resolve_home(text_update("Standings", NICCI), store).home["id"] == second["id"]


def test_require_home_replies_with_guidance_or_picker(store):
    upd = text_update("Standings", STRANGER)
    assert run(require_home(upd, FakeContext(), store)) is None
    assert "not in any home" in upd.message.last_reply
    store.create_home(-200, "Beach House", 1002, "Nicci")
    upd = text_update("Standings", NICCI)
    assert run(require_home(upd, FakeContext(), store)) is None
    assert keyboard_labels(upd.message.last_markup) == ["Beach House", "WG"]
    assert upd.message.last_markup.inline_keyboard[0][0].callback_data == f"{CB_HOME_SELECT}-200"
    upd = group_update("Standings", STRANGER)
    assert run(require_home(upd, FakeContext(), store)) is None
    assert "/join" in upd.message.last_reply


# -------------------------------------------------------------------- flows
def test_flows_are_namespaced_per_chat(store):
    ctx = FakeContext()
    home = store.get_home(LEGACY_GROUP_ID)
    dm = start_flow(ctx, 1002, home)
    grp = start_flow(ctx, LEGACY_GROUP_ID, home)
    dm["amount"] = 5
    assert "amount" not in grp
    assert flow_state(ctx, 1002) is dm
    assert flow_home(ctx, 1002, 1002, store)["id"] == home["id"]
    assert flow_home(ctx, 1002, 4242, store) is None          # no access
    assert flow_home(ctx, 777, 1002, store) is None           # no draft in that chat
    end_flow(ctx, 1002)
    assert flow_home(ctx, 1002, 1002, store) is None
    ctx.user_data["viewer"] = "keep"
    clear_flows(ctx)
    assert ctx.user_data == {"viewer": "keep"}


def test_owns_keyboard_checks_message_id():
    msg = FakeMessage("kb")
    query = type("Q", (), {"message": msg})()
    assert not owns_keyboard(query, {})
    assert owns_keyboard(query, {"kb_msg": msg.message_id})
    assert not owns_keyboard(query, {"kb_msg": msg.message_id + 1})


# -------------------------------------------------------------------- roles
def test_admin_management_keeps_last_admin(store):
    home = store.get_home(LEGACY_GROUP_ID)
    require_admin(home, 1001)
    with pytest.raises(HomeError):
        require_admin(home, 1002)
    with pytest.raises(HomeError):
        remove_admin(home, 1001)
    add_admin(home, 1002)
    add_admin(home, 1002)  # idempotent
    assert home["roles"]["admins"] == [1001, 1002]
    remove_admin(home, 1001)
    assert home["roles"]["admins"] == [1002]
    with pytest.raises(HomeError):
        remove_admin(home, 4242)
    with pytest.raises(HomeError):
        add_admin(home, "nope")


def test_single_roles_and_holders(store):
    home = store.get_home(LEGACY_GROUP_ID)
    set_single_role(home, "chronicler", 1002)
    set_single_role(home, "handler", None)
    with pytest.raises(HomeError):
        set_single_role(home, "mayor", 1)
    with pytest.raises(HomeError):
        set_single_role(home, "chronicler", -3)
    assert role_holders(home) == {1001: ["admin"], 1002: ["chronicler"]}
    store.save_home(home)
    assert store.get_home(LEGACY_GROUP_ID)["roles"]["chronicler"] == 1002


# ------------------------------------------------------------------ joining
def test_join_as_new_member_dedupes_names(store):
    home = store.get_home(LEGACY_GROUP_ID)
    m = join_as_new_member(home, Caller(4242, "Nicci Lopez"))
    assert m["name"] == "Nicci Lopez 2" and m["uid"] == 4242
    with pytest.raises(HomeError):
        join_as_new_member(home, Caller(4242, "again"))
    store.save_home(home)
    assert member_for_uid(store.get_home(LEGACY_GROUP_ID), 4242)["name"] == "Nicci Lopez 2"


def test_claim_requires_admin_approval(store):
    home = store.get_home(LEGACY_GROUP_ID)
    linked = next(m for m in home["members"] if m.get("uid"))
    with pytest.raises(HomeError):
        request_claim(home, Caller(4242, "Sam"), linked["mid"])
    # make one member unclaimed
    target = home["members"][0]
    target.pop("uid", None)
    request_claim(home, Caller(4242, "Sam", "sam"), target["mid"])
    assert home["join_requests"]["4242"]["mid"] == target["mid"]
    with pytest.raises(HomeError):
        approve_claim(home, 999)
    assert approve_claim(home, 4242) is target
    assert target["uid"] == 4242 and home["join_requests"] == {}
    request_claim(home, Caller(4343, "Sue"), home["members"][1]["mid"]) if not home["members"][1].get("uid") else None
    if "4343" in home["join_requests"]:
        deny_claim(home, 4343)
    with pytest.raises(HomeError):
        deny_claim(home, 4343)


def test_link_and_unlink_guard_conflicts(store):
    home = store.get_home(LEGACY_GROUP_ID)
    a, b = home["members"][0], home["members"][1]
    a.pop("uid", None)
    b.pop("uid", None)
    link_member(home, a["mid"], 4242)
    with pytest.raises(HomeError):
        link_member(home, b["mid"], 4242)      # same user twice
    with pytest.raises(HomeError):
        link_member(home, a["mid"], 4343)      # member already linked elsewhere
    link_member(home, a["mid"], 4242)          # relinking same pair is fine
    unlink_member(home, a["mid"])
    assert "uid" not in a
    with pytest.raises(HomeError):
        unlink_member(home, "nope")


def test_remove_member_refuses_when_history_exists(store):
    home = store.get_home(LEGACY_GROUP_ID)
    with pytest.raises(HomeError, match="Unlink"):
        remove_member(home, home["members"][0]["mid"])
    fresh = join_as_new_member(home, Caller(4242, "Temp"))
    assert not member_has_history(home, fresh)
    remove_member(home, fresh["mid"])
    assert member_for_uid(home, 4242) is None


# --------------------------------------------------------- telegram checks
def test_group_membership_checks_use_bot_api():
    bot = FakeBot(chat_members={(-1, 1): "member", (-1, 2): "creator", (-1, 3): "kicked"})
    assert run(is_group_member(bot, -1, 1)) and not run(is_group_admin(bot, -1, 1))
    assert run(is_group_admin(bot, -1, 2))
    assert not run(is_group_member(bot, -1, 3))
    assert not run(is_group_member(bot, -1, 4))

    class Failing(FakeBot):
        async def get_chat_member(self, chat_id, user_id):
            raise TelegramError("boom")

    assert not run(is_group_member(Failing(), -1, 1))


def test_settings_helpers_fall_back(store):
    home = store.get_home(LEGACY_GROUP_ID)
    assert home_currency(home) == "CHF"
    assert home_tz(home).zone == "Europe/Berlin"
    home["settings"]["timezone"] = "Mars/Olympus"
    assert home_tz(home).zone == "Europe/Berlin"
    assert homes.new_invite_token() != homes.new_invite_token()
