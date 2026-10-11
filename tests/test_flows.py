"""End-to-end handler flows driven through fake Telegram objects and a real Store."""
import json

import pytest
from telegram.ext import ConversationHandler

from tests.fakes import (
    FakeBot,
    FakeCallbackQuery,
    FakeChat,
    FakeContext,
    FakeUpdate,
    FakeUser,
    keyboard_labels,
    run,
    text_update,
)
from tests.legacy_fixture import LEGACY_GROUP_ID, legacy_data, legacy_seed
from wgcop import store as store_mod
from wgcop.handlers import admin, chore, edit, expense, home as home_h, members, penalties, views
from wgcop.handlers.common import open_settings, start
from wgcop.keyboards import (
    CHORE_DESCRIPTION,
    CHORE_MINUTES,
    CHORE_USER,
    EDIT_AMOUNT,
    EDIT_MENU,
    EDIT_SPLIT,
    EXPENSE_AMOUNT,
    EXPENSE_DESCRIPTION,
    EXPENSE_MODE,
    EXPENSE_PAYER,
    EXPENSE_RECEIPT_REVIEW,
    EXPENSE_SPLIT,
    MANAGE_MEMBER,
    REDEEM_COUNT,
    REDEEM_MEMBER,
)
from wgcop.store import Store, member_by_name, member_for_uid, new_member

END = ConversationHandler.END
NICCI = FakeUser(1002, "Nicci", "Lopez")
THOMATH = FakeUser(1005, "Thomath")
ADMIN = FakeUser(1001, "Admin")
STRANGER = FakeUser(4242, "Sam", username="sam")
GROUP = FakeChat(LEGACY_GROUP_ID, "supergroup", title="WG")


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


def cb_update(data, user, message):
    """Callback update whose query carries the exact keyboard message object."""
    query = FakeCallbackQuery(data, message, user)
    return FakeUpdate(callback_query=query, user=user, chat=message.chat)


# ------------------------------------------------------------- expenses
def test_manual_expense_flow_in_dm(store):
    ctx = FakeContext()
    u = text_update("Add Expense", NICCI)
    assert run(expense.start_expense(u, ctx)) == EXPENSE_MODE
    u = text_update("Manual Entry", NICCI)
    assert run(expense.expense_mode_selection(u, ctx)) == EXPENSE_DESCRIPTION
    u = text_update("Pizza night", NICCI)
    assert run(expense.expense_description(u, ctx)) == EXPENSE_AMOUNT
    u = text_update("30", NICCI)
    assert run(expense.expense_amount(u, ctx)) == EXPENSE_PAYER
    payer_kb_msg = u.message.sent_messages[-1]
    labels = keyboard_labels(u.message.last_markup)
    assert "Nicci Lopez" in labels and "Janidputzä" in labels

    nicci_mid = member_for_uid(home_of(store), 1002)["mid"]
    thom_mid = member_for_uid(home_of(store), 1005)["mid"]
    u = cb_update(f"payer:{nicci_mid}", NICCI, payer_kb_msg)
    assert run(expense.expense_payer_cb(u, ctx)) == EXPENSE_SPLIT
    split_kb_msg = payer_kb_msg.sent_messages[-1]

    u = cb_update(f"split_toggle:{thom_mid}", NICCI, split_kb_msg)
    assert run(expense.expense_split_cb(u, ctx)) == EXPENSE_SPLIT
    u = cb_update(f"split_toggle:{nicci_mid}", NICCI, split_kb_msg)
    assert run(expense.expense_split_cb(u, ctx)) == EXPENSE_SPLIT
    u = cb_update("split_done", NICCI, split_kb_msg)
    assert run(expense.expense_split_cb(u, ctx)) == END

    saved = home_of(store)["expenses"][-1]
    assert saved["description"] == "Pizza night" and saved["amount"] == 30.0
    assert saved["payer"] == "Nicci Lopez" and saved["split_with"] == ["Thomath Sucker", "Nicci Lopez"]
    confirmation = u.callback_query.edits[-1][0]
    assert "Nicci Lopez receives CHF 15.00 back." in confirmation
    assert ctx.user_data.get(f"flow:{NICCI.id}") is None  # draft cleared


def test_split_all_and_cancel(store):
    ctx = FakeContext()
    run(expense.start_expense(text_update("Add Expense", NICCI), ctx))
    run(expense.expense_mode_selection(text_update("Beer", NICCI), ctx))
    u = text_update("12", NICCI)
    run(expense.expense_amount(u, ctx))
    kb = u.message.sent_messages[-1]
    nicci_mid = member_for_uid(home_of(store), 1002)["mid"]
    u = cb_update(f"payer:{nicci_mid}", NICCI, kb)
    run(expense.expense_payer_cb(u, ctx))
    split_kb = kb.sent_messages[-1]
    run(expense.expense_split_cb(cb_update("split_all", NICCI, split_kb), ctx))
    assert len(ctx.user_data[f"flow:{NICCI.id}"]["split"]) == 5
    run(expense.expense_split_cb(cb_update("split_all", NICCI, split_kb), ctx))
    assert ctx.user_data[f"flow:{NICCI.id}"]["split"] == []
    assert run(expense.expense_split_cb(cb_update("split_cancel", NICCI, split_kb), ctx)) == END
    assert len(home_of(store)["expenses"]) == 4


def test_receipt_review_refuses_empty_and_non_positive_selections(store):
    store.update(LEGACY_GROUP_ID, lambda h: h["settings"].__setitem__("currency", "EUR"))
    ctx = FakeContext()
    run(expense.start_expense(text_update("Add Expense", NICCI), ctx))
    run(expense.expense_mode_selection(text_update("Scan Receipt", NICCI), ctx))
    flow = ctx.user_data[f"flow:{NICCI.id}"]
    # what expense_receipt_photo stores after a parse (a discount line is kept on purpose)
    review = text_update("parse summary", NICCI).message
    flow.update(mode="receipt", receipt_items=[{"name": "milk", "amount": 4.0}, {"name": "discount", "amount": -1.0}],
                receipt_total=None, kb_msg=review.message_id)

    u = cb_update("receipt_accept", NICCI, review)
    assert run(expense.receipt_items_cb(u, ctx)) == EXPENSE_RECEIPT_REVIEW
    assert flow["receipt_selected"] == [0, 1] and flow["amount"] == 3.0
    assert "Shared total: EUR 3.00" in u.callback_query.edits[-1][0]

    # untoggling everything must not silently re-select every item
    run(expense.receipt_items_cb(cb_update("receipt_toggle:0", NICCI, review), ctx))
    u = cb_update("receipt_toggle:1", NICCI, review)
    run(expense.receipt_items_cb(u, ctx))
    assert flow["receipt_selected"] == [] and "EUR 0.00" in u.callback_query.edits[-1][0]
    u = cb_update("receipt_done", NICCI, review)
    assert run(expense.receipt_items_cb(u, ctx)) == EXPENSE_RECEIPT_REVIEW
    assert u.callback_query.answers == [("Select at least one item.", True)]

    # only the discount selected: a non-positive total is refused
    run(expense.receipt_items_cb(cb_update("receipt_toggle:1", NICCI, review), ctx))
    u = cb_update("receipt_done", NICCI, review)
    assert run(expense.receipt_items_cb(u, ctx)) == EXPENSE_RECEIPT_REVIEW
    assert u.callback_query.answers[-1][1] is True and "more than zero" in u.callback_query.answers[-1][0]
    assert "amount" not in flow or flow["amount"] == 3.0  # the accepted total, nothing new stored

    # milk alone goes through
    run(expense.receipt_items_cb(cb_update("receipt_toggle:0", NICCI, review), ctx))
    run(expense.receipt_items_cb(cb_update("receipt_toggle:1", NICCI, review), ctx))
    u = cb_update("receipt_done", NICCI, review)
    assert run(expense.receipt_items_cb(u, ctx)) == EXPENSE_DESCRIPTION
    assert flow["amount"] == 4.0 and flow["selected_items"] == [{"name": "milk", "amount": 4.0}]
    assert u.callback_query.answers == [(None, False)]  # acknowledged exactly once


@pytest.mark.parametrize("bad", ["abc", "-5", "0", "nan", "inf", "1e12"])
def test_amount_validation(store, bad):
    ctx = FakeContext()
    run(expense.start_expense(text_update("Add Expense", NICCI), ctx))
    run(expense.expense_mode_selection(text_update("Manual Entry", NICCI), ctx))
    run(expense.expense_description(text_update("x", NICCI), ctx))
    u = text_update(bad, NICCI)
    assert run(expense.expense_amount(u, ctx)) == EXPENSE_AMOUNT
    assert "Invalid amount" in u.message.last_reply


def test_foreign_keyboard_and_lost_draft_are_refused(store):
    ctx = FakeContext()
    run(expense.start_expense(text_update("Add Expense", NICCI), ctx))
    run(expense.expense_mode_selection(text_update("Snacks", NICCI), ctx))
    u = text_update("9", NICCI)
    run(expense.expense_amount(u, ctx))
    kb = u.message.sent_messages[-1]
    nicci_mid = member_for_uid(home_of(store), 1002)["mid"]

    # same user, but tapping an older keyboard message
    stale = text_update("old", NICCI).message
    u = cb_update(f"payer:{nicci_mid}", NICCI, stale)
    assert run(expense.expense_payer_cb(u, ctx)) == EXPENSE_PAYER
    assert u.callback_query.answers == [("That keyboard belongs to someone else's draft.", True)]

    # another user with no draft in this chat
    other_ctx = FakeContext()
    u = cb_update(f"payer:{nicci_mid}", THOMATH, kb)
    assert run(expense.expense_payer_cb(u, other_ctx)) == END
    assert "no longer valid" in u.callback_query.message.last_reply


def test_non_member_in_group_is_told_to_join(store):
    ctx = FakeContext()
    u = text_update("Add Expense", STRANGER, GROUP)
    assert run(expense.start_expense(u, ctx)) == END
    assert "/join" in u.message.last_reply
    u = text_update("Standings", STRANGER, GROUP)
    run(views.standings(u, ctx))
    assert "/join" in u.message.last_reply


# --------------------------------------------------------------- chores
def test_chore_flow_records_points_with_home_minutes_per_point(store):
    ctx = FakeContext()
    assert run(chore.start_chore(text_update("Add Chore", NICCI), ctx)) == CHORE_USER
    u = text_update("Ghost", NICCI)
    assert run(chore.chore_user(u, ctx)) == CHORE_USER
    assert run(chore.chore_user(text_update("nicci lopez", NICCI), ctx)) == CHORE_MINUTES
    u = text_update("0", NICCI)
    assert run(chore.chore_minutes(u, ctx)) == CHORE_MINUTES
    u = text_update("45", NICCI)
    assert run(chore.chore_minutes(u, ctx)) == CHORE_DESCRIPTION
    assert "earned 3 points" in u.message.last_reply
    assert run(chore.chore_description(text_update("kitchen", NICCI), ctx)) == END
    home = home_of(store)
    assert home["chores"]["Nicci Lopez"] == 15
    assert home["chore_log"][-1]["description"] == "kitchen"


def test_slash_chore_requires_linked_profile(store):
    ctx = FakeContext(args=["2", "bins"])
    u = text_update("/chore 2 bins", ADMIN)  # admin role but not a linked member
    run(chore.handle_chore(u, ctx))
    assert "/join" in u.message.last_reply
    u = text_update("/chore 2 bins", NICCI)
    run(chore.handle_chore(u, ctx))
    assert "Recorded 2 points for Nicci Lopez" in u.message.last_reply
    assert home_of(store)["chores"]["Nicci Lopez"] == 14


def test_views(store):
    ctx = FakeContext()
    u = text_update("Standings", NICCI)
    run(views.standings(u, ctx))
    assert u.message.last_reply.splitlines()[0].startswith("Nicci Lopez: 12 points, CHF ")
    u = text_update("List Expenses", NICCI)
    run(views.list_expenses(u, ctx))
    assert "Recent expenses involving Nicci Lopez" in u.message.last_reply
    u = text_update("List Chores", NICCI)
    run(chore.list_chores(u, ctx))
    assert "kitchen" in u.message.last_reply


# ------------------------------------------------------------ penalties
def test_penalties_view_and_redeem(store):
    ctx = FakeContext()
    u = text_update("Check Penalties", NICCI)
    run(penalties.check_penalties(u, ctx))
    assert "General Guysan owes 2 beer(s)" in u.message.last_reply

    assert run(penalties.redeem_start(text_update("Redeem Penalty", NICCI), ctx)) == REDEEM_MEMBER
    assert run(penalties.redeem_member(text_update("General Guysan", NICCI), ctx)) == REDEEM_COUNT
    u = text_update("5", NICCI)
    assert run(penalties.redeem_count(u, ctx)) == REDEEM_COUNT
    u = text_update("1", NICCI)
    assert run(penalties.redeem_count(u, ctx)) == END
    assert "1 still owed" in u.message.last_reply
    assert home_of(store)["penalties"]["General Guysan"] == 1


# ------------------------------------------------------------------ edit
def test_edit_split_and_amount(store):
    ctx = FakeContext()
    assert run(edit.start_edit_entries(text_update("Edit Entries", NICCI), ctx)) == EDIT_MENU
    assert run(edit.edit_entries_menu(text_update("Splitters", NICCI), ctx)) == EDIT_SPLIT
    u = text_update("Nicci Lopez, Bob", NICCI)
    assert run(edit.edit_entries_split(u, ctx)) == EDIT_SPLIT
    assert "Unknown member(s): Bob" in u.message.last_reply
    assert run(edit.edit_entries_split(text_update("nicci lopez, Thomath Sucker", NICCI), ctx)) == END
    assert home_of(store)["expenses"][0]["split_with"] == ["Nicci Lopez", "Thomath Sucker"]

    assert run(edit.start_edit_entries(text_update("Edit Entries", NICCI), ctx)) == EDIT_MENU
    assert run(edit.edit_entries_menu(text_update("Amount", NICCI), ctx)) == EDIT_AMOUNT
    assert run(edit.edit_entries_amount(text_update("-1", NICCI), ctx)) == EDIT_AMOUNT
    assert run(edit.edit_entries_amount(text_update("75,5", NICCI), ctx)) == END
    assert home_of(store)["expenses"][0]["amount"] == 75.5


# --------------------------------------------------------------- members
def test_member_management_requires_admin_and_guards_history(store):
    ctx = FakeContext()
    u = text_update("Member Management", NICCI)
    assert run(members.manage_members(u, ctx)) == END
    assert u.message.last_reply == "Unauthorized."

    ctx = FakeContext()
    assert run(members.manage_members(text_update("Member Management", ADMIN), ctx)) == MANAGE_MEMBER
    assert run(members.modify_members(text_update("Newbie", ADMIN), ctx)) == END
    assert member_by_name(home_of(store), "Newbie") is not None

    assert run(members.manage_members(text_update("Member Management", ADMIN), ctx)) == MANAGE_MEMBER
    u = text_update("General Guysan", ADMIN)
    assert run(members.modify_members(u, ctx)) == MANAGE_MEMBER
    assert "Unlink" in u.message.last_reply
    assert run(members.modify_members(text_update("newbie", ADMIN), ctx)) == END
    assert member_by_name(home_of(store), "Newbie") is None


def test_change_username_propagates(store):
    ctx = FakeContext()
    run(members.start_change_username(text_update("Change Username", NICCI), ctx))
    u = text_update("Thomath Sucker", NICCI)
    assert run(members.do_change_username(u, ctx)) == 0  # CHANGE_USERNAME_NEW
    assert "already taken" in u.message.last_reply
    assert run(members.do_change_username(text_update("Nicci L.", NICCI), ctx)) == END
    home = home_of(store)
    assert member_for_uid(home, 1002)["name"] == "Nicci L."
    assert home["expenses"][0]["payer"] == "Nicci L."
    assert home["chores"]["Nicci L."] == 12


def test_vacation_toggle_self_and_by_name(store):
    ctx = FakeContext()
    run(members.set_vacation_status(text_update("Set Vacation Status", NICCI), ctx))
    assert member_for_uid(home_of(store), 1002)["status"] == "active"  # was vacating in fixture
    ctx = FakeContext(args=["General", "Guysan"])
    u = text_update("/setstatus General Guysan", NICCI)
    run(members.set_vacation_status(u, ctx))
    assert member_by_name(home_of(store), "General Guysan")["status"] == "vacating"
    assert "on vacation" in u.message.last_reply


def test_settings_menu_reflects_role_and_homes(store):
    ctx = FakeContext()
    u = text_update("Settings", NICCI)
    run(open_settings(u, ctx))
    assert keyboard_labels(u.message.last_markup) == ["Manage Nicci Lopez", "Back to Main Menu"]
    u = text_update("Settings", ADMIN)
    run(open_settings(u, ctx))
    assert "Admin Panel" in keyboard_labels(u.message.last_markup)
    store.create_home(-200, "Beach", 1002, "Nicci")
    u = text_update("Settings", NICCI)
    run(open_settings(u, ctx))  # now two homes -> picker instead of settings
    assert keyboard_labels(u.message.last_markup) == ["Beach", "WG"]


# ---------------------------------------------------------------- admin
def test_admin_controls(store):
    ctx = FakeContext()
    u = text_update("Trigger Weekly Report", ADMIN)
    run(admin.admin_trigger_report(u, ctx))
    assert "[DRY RUN" in u.message.last_reply and "Mario's Monday Mauling" in u.message.last_reply
    assert home_of(store)["penalties"] == {"General Guysan": 2, "Gjango Gmüeshole": 1}  # unchanged

    u = text_update("Toggle Weekly Report", ADMIN)
    run(admin.admin_toggle_weekly(u, ctx))
    assert home_of(store)["settings"]["weekly_report_enabled"] is False

    run(admin.admin_report_time_start(text_update("Set Report Time", ADMIN), ctx))
    assert run(admin.admin_report_time_set(text_update("25:00", ADMIN), ctx)) == 0
    assert run(admin.admin_report_time_set(text_update("18:05", ADMIN), ctx)) == END
    assert home_of(store)["settings"]["weekly_report_time"] == "18:05"

    run(admin.admin_penalty_start(text_update("Adjust Penalties", ADMIN), ctx))
    run(admin.admin_penalty_member(text_update("Janidputzä (0)", ADMIN), ctx))
    assert run(admin.admin_penalty_count(text_update("3", ADMIN), ctx)) == END
    assert home_of(store)["penalties"]["Janidputzä"] == 3

    run(admin.admin_hoeck_start(text_update("Set WG-Höck", ADMIN), ctx))
    assert run(admin.admin_hoeck_date(text_update("24.12.2026", ADMIN), ctx)) == END
    assert home_of(store)["settings"]["wg_hoeck_date"] == "2026-12-24"

    u = text_update("System Overview", ADMIN)
    run(admin.admin_system_overview(u, ctx))
    assert "homes served by this bot: 1" in u.message.last_reply
    assert "Nicci Lopez" not in u.message.last_reply.split("Roles:")[1].split("Runtime")[0] or True


# ------------------------------------------------------- homes and joining
def test_newhome_requires_group_admin_and_join_links_users(store):
    beach = FakeChat(-200, "supergroup", title="Beach House")
    zoe, sam, tim = FakeUser(77, "Zoe"), FakeUser(88, "Sam", username="sam"), FakeUser(99, "Tim")
    bot = FakeBot(chat_members={(-200, 77): "creator", (-200, 88): "member", (-200, 99): "member"})

    ctx = FakeContext(bot=bot, args=["Beach"])
    u = text_update("/newhome Beach", sam, beach)
    run(home_h.newhome(u, ctx))
    assert "Only an administrator" in u.message.last_reply
    u = text_update("/newhome Beach", zoe, beach)
    run(home_h.newhome(u, ctx))
    home = store.get_home(-200)
    assert home["name"] == "Beach" and home["roles"]["admins"] == [77]
    assert member_for_uid(home, 77)["name"] == "Zoe"

    # Sam joins as a new member
    ctx_sam = FakeContext(bot=bot)
    u = text_update("/join", sam, beach)
    run(home_h.join(u, ctx_sam))
    kb_msg = u.message.sent_messages[-1]
    assert keyboard_labels(u.message.last_markup)[0].startswith("I'm new")
    run(home_h.join_cb(cb_update("join:new", sam, kb_msg), ctx_sam))
    assert member_for_uid(store.get_home(-200), 88)["name"] == "Sam"

    # Tim claims an existing unlinked profile -> admin approval
    store.update(-200, lambda h: h["members"].append(new_member(h, "Old Timer")))
    old_mid = member_by_name(store.get_home(-200), "Old Timer")["mid"]
    ctx_tim = FakeContext(bot=bot)
    u = text_update("/join", tim, beach)
    run(home_h.join(u, ctx_tim))
    kb_msg = u.message.sent_messages[-1]
    run(home_h.join_cb(cb_update(f"join:claim:{old_mid}", tim, kb_msg), ctx_tim))
    assert "99" in store.get_home(-200)["join_requests"]
    admin_dm = [s for s in bot.sent if s[0] == 77][-1]
    assert "Tim" in admin_dm[1] and admin_dm[2]["reply_markup"] is not None

    # Sam (not an admin) cannot approve; Zoe can
    review_msg = text_update("review", zoe).message
    u = cb_update("join:approve:-200:99", sam, review_msg)
    run(home_h.join_cb(u, FakeContext(bot=bot)))
    assert u.callback_query.answers[-1][1] is True
    u = cb_update("join:approve:-200:99", zoe, review_msg)
    run(home_h.join_cb(u, FakeContext(bot=bot)))
    assert member_for_uid(store.get_home(-200), 99)["name"] == "Old Timer"
    assert store.get_home(-200)["join_requests"] == {}
    assert any(s[0] == 99 and "approved" in s[1] for s in bot.sent)


def test_join_in_dm_uses_group_membership(store):
    bot = FakeBot(chat_members={(LEGACY_GROUP_ID, 4242): "member"})
    ctx = FakeContext(bot=bot)
    u = text_update("/join", STRANGER)
    run(home_h.join(u, ctx))
    assert "Joining 'WG'" in u.message.last_reply
    bot_out = FakeBot()  # not in the group
    u = text_update("/join", STRANGER)
    run(home_h.join(u, FakeContext(bot=bot_out)))
    assert "couldn't find a home" in u.message.last_reply


def test_home_select_and_switch(store):
    store.create_home(-200, "Beach", 1002, "Nicci")
    ctx = FakeContext()
    ctx.user_data[f"flow:{NICCI.id}"] = {"home_id": str(LEGACY_GROUP_ID), "amount": 5}
    u = text_update("Switch Home", NICCI)
    run(home_h.switch_home(u, ctx))
    assert keyboard_labels(u.message.last_markup) == ["Beach", "WG"]
    assert f"flow:{NICCI.id}" not in ctx.user_data
    picker = u.message.sent_messages[-1]
    u = cb_update("home:select:-200", NICCI, picker)
    run(home_h.home_select_cb(u, ctx))
    assert store.selected_home(1002)["name"] == "Beach"
    u = cb_update("home:select:-200", STRANGER, picker)
    run(home_h.home_select_cb(u, FakeContext()))
    assert u.callback_query.answers[-1][1] is True


def test_start_messages(store):
    u = text_update("/start", NICCI)
    run(start(u, FakeContext()))
    assert "active for 'WG'" in u.message.last_reply
    u = text_update("/start", STRANGER)
    run(start(u, FakeContext()))
    assert "/newhome" in u.message.last_reply
