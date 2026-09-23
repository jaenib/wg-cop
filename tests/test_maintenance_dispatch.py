"""Exercise Telegram routing, including overlapping chats and interrupted drafts."""
import asyncio

import pytest
from telegram.ext import Application

from tests.telegram_driver import TelegramDriver
from wgcop import keyboards as kb, store as storage
from wgcop.app import register_handlers
from wgcop.homes import CB_HOME_SELECT
from wgcop.store import Store, new_member


@pytest.fixture
def homes(tmp_path, monkeypatch):
    store = Store(tmp_path / "homes.json")
    store.initialize()
    monkeypatch.setattr(storage, "_STORE", store)
    store.create_home(-100, "First", 10, "Alice")
    store.create_home(-200, "Second", 10, "Alice")
    store.update(-100, lambda h: h["members"].append(new_member(h, "Bob", uid=20)))
    store.select_home(10, -100)
    return store


async def run_bot(scenario):
    driver = TelegramDriver()
    app = (Application.builder().token("123456:TEST-TOKEN-NOT-REAL")
           .request(driver).get_updates_request(driver).job_queue(None).build())
    register_handlers(app)
    errors = []

    async def catch_error(update, context):
        errors.append(context.error)

    app.add_error_handler(catch_error)
    async with app:
        await scenario(app, driver)
    assert not errors, errors


async def message(app, driver, cid, text, uid=10):
    await app.process_update(driver.message_update(app.bot, uid, cid, text))


async def callback(app, driver, cid, mid, data, uid=10):
    await app.process_update(driver.callback_update(app.bot, uid, cid, mid, data))


async def expense_to_payer(app, driver, cid, description, uid=10):
    for text in (kb.BTN_ADD_EXPENSE, kb.BTN_MANUAL_ENTRY, description, "12.50"):
        await message(app, driver, cid, text, uid)
    flow = app.user_data[uid][f"flow:{cid}"]
    assert flow["amount"] == 12.5
    return flow["kb_msg"]


def test_same_user_can_enter_expenses_in_two_groups(homes):
    async def scenario(app, driver):
        first = await expense_to_payer(app, driver, -100, "First groceries")
        second = await expense_to_payer(app, driver, -200, "Second groceries")
        for cid, mid in ((-100, first), (-200, second)):
            await callback(app, driver, cid, mid, kb.CB_PAYER_PREFIX + "m1")
            split = app.user_data[10][f"flow:{cid}"]["kb_msg"]
            await callback(app, driver, cid, split, kb.CB_SPLIT_ALL)
            await callback(app, driver, cid, split, kb.CB_SPLIT_DONE)
        assert homes.get_home(-100)["expenses"][0]["description"] == "First groceries"
        assert homes.get_home(-200)["expenses"][0]["description"] == "Second groceries"
        assert len(homes.get_home(-100)["expenses"]) == 1
        assert len(homes.get_home(-200)["expenses"]) == 1
    asyncio.run(run_bot(scenario))


def test_another_members_keyboard_cannot_change_own_draft(homes):
    async def scenario(app, driver):
        alice = await expense_to_payer(app, driver, -100, "Alice groceries")
        bob = await expense_to_payer(app, driver, -100, "Bob groceries", uid=20)
        await callback(app, driver, -100, alice, kb.CB_PAYER_PREFIX + "m1", uid=20)
        assert "payer" not in app.user_data[20]["flow:-100"]
        assert app.user_data[20]["flow:-100"]["kb_msg"] == bob
        await callback(app, driver, -100, bob, kb.CB_PAYER_PREFIX + "m2", uid=20)
        assert app.user_data[20]["flow:-100"]["payer"] == "Bob"
        assert "payer" not in app.user_data[10]["flow:-100"]
    asyncio.run(run_bot(scenario))


def test_private_home_switch_preserves_other_chat_draft(homes):
    async def scenario(app, driver):
        await expense_to_payer(app, driver, -100, "Group draft")
        await expense_to_payer(app, driver, 10, "Private draft")
        await message(app, driver, 10, kb.BTN_SWITCH_HOME)
        await callback(app, driver, 10, driver.next_message, CB_HOME_SELECT + "-200")
        assert app.user_data[10].get("flow:-100", {}).get("description") == "Group draft"
        assert not app.user_data[10].get("flow:10")
        assert homes.selected_home(10)["id"] == "-200"
    asyncio.run(run_bot(scenario))


def test_home_switch_allows_immediate_new_conversation(homes):
    async def scenario(app, driver):
        await expense_to_payer(app, driver, 10, "Old draft")
        await message(app, driver, 10, kb.BTN_SWITCH_HOME)
        await callback(app, driver, 10, driver.next_message, CB_HOME_SELECT + "-200")
        await message(app, driver, 10, kb.BTN_ADD_CHORE)
        await message(app, driver, 10, "Alice")
        await message(app, driver, 10, "30")
        await message(app, driver, 10, "Cleaned kitchen")
        assert homes.get_home(-200)["chores"]["Alice"] == 2
        assert not homes.get_home(-100)["chores"]
    asyncio.run(run_bot(scenario))


def test_menu_switch_does_not_feed_new_chore_into_old_expense(homes):
    async def scenario(app, driver):
        await message(app, driver, 10, kb.BTN_ADD_EXPENSE)
        await message(app, driver, 10, kb.BTN_MANUAL_ENTRY)
        await message(app, driver, 10, kb.BTN_SETTINGS)
        await message(app, driver, 10, kb.BTN_BACK_MAIN)
        await message(app, driver, 10, kb.BTN_ADD_CHORE)
        await message(app, driver, 10, "Alice")
        await message(app, driver, 10, "30")
        await message(app, driver, 10, "Cleaned kitchen")
        assert homes.get_home(-100)["chores"].get("Alice") == 2
        assert not homes.get_home(-100)["expenses"]
    asyncio.run(run_bot(scenario))


def test_newhome_requires_telegram_group_admin(homes):
    async def scenario(app, driver):
        driver.members[(-300, 30)] = "member"
        await message(app, driver, -300, "/newhome Third", uid=30)
        assert homes.home_for_group(-300) is None
        driver.members[(-300, 30)] = "administrator"
        await message(app, driver, -300, "/newhome Third", uid=30)
        assert homes.home_for_group(-300)["roles"]["admins"] == [30]
        assert homes.get_home(-100)["roles"]["admins"] == [10]
    asyncio.run(run_bot(scenario))


def test_existing_profile_claim_requires_that_homes_admin(homes):
    homes.update(-100, lambda h: h["members"].append(new_member(h, "Legacy profile")))
    homes.create_home(-300, "Third", 40, "Other admin")

    async def scenario(app, driver):
        driver.members[(-100, 30)] = "member"
        await message(app, driver, -100, "/join", uid=30)
        join_mid = driver.next_message
        await callback(app, driver, -100, join_mid, kb.CB_JOIN_CLAIM_PREFIX + "m3", uid=30)
        assert homes.get_home(-100)["members"][2].get("uid") is None
        assert "30" in homes.get_home(-100)["join_requests"]
        await callback(app, driver, 40, driver.next_message, kb.CB_JOIN_APPROVE_PREFIX + "-100:30", uid=40)
        assert homes.get_home(-100)["members"][2].get("uid") is None
        await callback(app, driver, 10, driver.next_message, kb.CB_JOIN_APPROVE_PREFIX + "-100:30")
        assert homes.get_home(-100)["members"][2]["uid"] == 30
        assert "30" not in homes.get_home(-100)["join_requests"]
    asyncio.run(run_bot(scenario))


def test_join_rechecks_group_membership_when_button_is_pressed(homes):
    async def scenario(app, driver):
        driver.members[(-100, 30)] = "member"
        await message(app, driver, -100, "/join", uid=30)
        join_mid = driver.next_message
        driver.members[(-100, 30)] = "left"
        await callback(app, driver, -100, join_mid, kb.CB_JOIN_NEW, uid=30)
        assert all(m.get("uid") != 30 for m in homes.get_home(-100)["members"])
    asyncio.run(run_bot(scenario))


@pytest.mark.parametrize("entry,answer", [
    (kb.BTN_SET_REPORT_TIME, "15:15"),
    (kb.BTN_MEMBER_MANAGEMENT, "Unauthorized new member"),
    (kb.BTN_SET_HOECK, "2026-10-01"),
])
def test_revoked_admin_cannot_finish_pending_admin_flow(homes, entry, answer):
    async def scenario(app, driver):
        await message(app, driver, -100, entry)
        homes.update(-100, lambda h: h["roles"].update(admins=[20]))
        before = homes.get_home(-100)
        await message(app, driver, -100, answer)
        assert homes.get_home(-100) == before
    asyncio.run(run_bot(scenario))
