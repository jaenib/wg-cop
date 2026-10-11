"""Regression cases for user-visible bookkeeping bugs, using synthetic state."""
import asyncio
import copy
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram.error import TelegramError
from telegram.ext import ConversationHandler

from wgcop.handlers import chore, edit, expense, penalties
from wgcop import receipts, reports, store as storage
from wgcop.store import Store, new_member


def update(text="", callback=None, uid=10):
    chat = SimpleNamespace(id=-100, type="supergroup")
    user = SimpleNamespace(id=uid, full_name="Different Telegram name", is_bot=False)
    message = SimpleNamespace(text=text, message_id=42, chat=chat, from_user=user,
                              sender_chat=None, reply_text=AsyncMock(), reply_html=AsyncMock())
    query = SimpleNamespace(data=callback, answer=AsyncMock(), message=message,
                            edit_message_text=AsyncMock(), edit_message_reply_markup=AsyncMock())
    return SimpleNamespace(message=message, effective_message=message,
                           callback_query=query if callback else None,
                           effective_user=user, effective_chat=chat)


def context(**values):
    return SimpleNamespace(user_data={"flow:-100": {"home_id": "-100", "kb_msg": 42, **values}}, args=[])


def draft(ctx):
    return ctx.user_data.get("flow:-100", {})


@pytest.fixture(autouse=True)
def ledger(tmp_path, monkeypatch):
    store = Store(tmp_path / "homes.json")
    store.initialize()
    monkeypatch.setattr(storage, "_STORE", store)
    store.create_home(-100, "Test Home", 10, "Alice")
    def seed(home):
        home["members"].append(new_member(home, "Bob", uid=20))
        home["expenses"] = [{"payer": "Alice", "amount": 12, "split_with": ["Alice", "Bob"]}]
        home["penalties"] = {"Alice": 2}
    store.update(-100, seed)
    return store


@pytest.mark.parametrize("value", ["nan", "inf", "-inf", "-1", "0", "0.001"])
def test_invalid_expense_amount_is_not_saved(value, ledger):
    original = ledger.read()
    ctx = context()
    result = asyncio.run(expense.expense_amount(update(value), ctx))
    assert result == expense.EXPENSE_AMOUNT
    assert "amount" not in draft(ctx)
    assert ledger.read() == original


@pytest.mark.parametrize("value", ["nan", "inf", "-inf", "-1", "0.001"])
def test_invalid_corrected_amount_does_not_touch_ledger(ledger, value):
    original = ledger.read()
    assert asyncio.run(edit.edit_entries_amount(update(value), context(edit_member="Alice", edit_index=0))) == edit.EDIT_AMOUNT
    assert ledger.read() == original


def test_splitters_button_accepts_member_objects(ledger):
    incoming = update("Splitters")
    assert asyncio.run(edit.edit_entries_menu(incoming, context())) == edit.EDIT_SPLIT
    assert "Alice, Bob" in incoming.message.reply_text.call_args.args[0]


def test_unknown_chore_member_is_rejected(ledger):
    ctx = context()
    assert asyncio.run(chore.chore_user(update("Nobody"), ctx)) == chore.CHORE_USER
    assert "member" not in draft(ctx)


def test_negative_chore_minutes_are_rejected():
    ctx = context(member="Alice")
    assert asyncio.run(chore.chore_minutes(update("-15"), ctx)) == chore.CHORE_MINUTES
    assert "points" not in draft(ctx)


def test_chore_command_uses_uid_link_not_telegram_display_name(ledger):
    ctx = context()
    ctx.args = ["2", "cleaned"]
    asyncio.run(chore.handle_chore(update(), ctx))
    assert ledger.get_home(-100)["chores"] == {"Alice": 2}


def test_unlinked_chore_command_cannot_create_ghost(ledger):
    ctx = context()
    ctx.args = ["2"]
    asyncio.run(chore.handle_chore(update(uid=99), ctx))
    assert not ledger.get_home(-100)["chores"]


def test_redemption_rechecks_current_debt(ledger):
    ctx = context(redeem_member="Alice", redeem_max=5)
    asyncio.run(penalties.redeem_count(update("4"), ctx))
    # Concurrent redemptions must never create a negative balance or increase debt.
    remaining = ledger.get_home(-100)["penalties"].get("Alice", 0)
    assert 0 <= remaining <= 2


def test_failed_confirmation_cannot_duplicate_expense(ledger):
    ctx = context(amount=10, payer="Alice", payer_mid="m1", description="Groceries", split=["m1", "m2"])
    incoming = update(callback=expense.CB_SPLIT_DONE)
    incoming.callback_query.edit_message_text.side_effect = TelegramError("Delivery failed")
    with pytest.raises(TelegramError):
        asyncio.run(expense.expense_split_cb(incoming, ctx))
    assert len(ledger.get_home(-100)["expenses"]) == 2
    assert not draft(ctx)
    assert asyncio.run(expense.expense_split_cb(update(callback=expense.CB_SPLIT_DONE), ctx)) == ConversationHandler.END
    assert len(ledger.get_home(-100)["expenses"]) == 2


def test_failed_confirmation_cannot_duplicate_chore(ledger):
    ctx = context(member="Alice", points=2)
    incoming = update("Cleaned kitchen")
    incoming.message.reply_text.side_effect = TelegramError("Delivery failed")
    with pytest.raises(TelegramError):
        asyncio.run(chore.chore_description(incoming, ctx))
    assert ledger.get_home(-100)["chores"] == {"Alice": 2}
    assert not draft(ctx)
    assert asyncio.run(chore.chore_description(update("Cleaned kitchen"), ctx)) == ConversationHandler.END
    assert ledger.get_home(-100)["chores"] == {"Alice": 2}


def test_changed_payer_at_edit_index_cannot_be_overwritten(ledger):
    original = copy.deepcopy(ledger.get_home(-100)["expenses"])
    result = asyncio.run(edit.edit_entries_amount(update("40"), context(edit_index=0, edit_member="Bob")))
    assert result == ConversationHandler.END
    assert ledger.get_home(-100)["expenses"] == original


def test_receipt_skips_non_finite_items_and_retains_discount():
    items = [{"name": "bad", "unit_price": "nan", "quantity": 1},
             {"name": "bad", "unit_price": 5, "quantity": "inf"},
             {"name": "milk", "unit_price": 2, "quantity": 2},
             {"name": "discount", "unit_price": -1, "quantity": 1}]
    normalized = receipts._normalise_receipt_items(items, "test")
    assert [(i["name"], i["amount"]) for i in normalized] == [("milk", 4), ("discount", -1)]


@pytest.mark.parametrize("value", ["25:00", "12:99", "-1:15", "bad"])
def test_invalid_stored_report_time_has_safe_default(value):
    assert reports._parse_report_time(value) == (9, 30)


def test_receipt_scan_leaves_event_loop_available_for_jobs(monkeypatch):
    job_ran = threading.Event()
    def scan(*args, **kwargs):
        assert job_ran.wait(1), "Receipt scan blocked the bot's event loop"
        return {"items": []}
    monkeypatch.setattr(expense, "extract_items_from_receipt", scan)
    incoming = update()
    incoming.message.photo = [SimpleNamespace(get_file=AsyncMock(
        return_value=SimpleNamespace(download_to_drive=AsyncMock())))]
    incoming.message.document = None
    incoming.message.reply_text.return_value = SimpleNamespace(delete=AsyncMock())
    async def scenario():
        async def scheduled_job():
            await asyncio.sleep(0.01)
            job_ran.set()
        result, _ = await asyncio.gather(expense.expense_receipt_photo(incoming, context()), scheduled_job())
        return result
    assert asyncio.run(scenario()) == expense.EXPENSE_DESCRIPTION
