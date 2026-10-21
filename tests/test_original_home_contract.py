"""Contract: the original home keeps working for everyone who used it before multi-home.

On migration the household of the single-home bot becomes the first home. Before
multi-home, anyone in its Telegram group could book food there, typed in or scanned
from a receipt, and a private chat with the bot worked too. Two later changes each
locked original members out:

* multi-home (#48) turned away anyone whose Telegram account was not linked to a
  profile ("Send /join here"); migration links only the accounts it can derive
  from the config ids, so an original member without one could no longer book;
* the leftover-keyboard fix (#52) threw away a draft when text arrived while it
  waited for a button tap (payer, split, receipt review); the next tap then said
  "This draft is no longer valid".

These tests build the home through the real legacy migration and drive the real
handler stack (Application.process_update over an offline transport) for every
original member. CI runs them before every deployment (deploy.yml: deploy needs
test). Do not relax an assertion to make a change pass: a failure here means an
original member can no longer book.
"""
import asyncio
import json

import pytest
from telegram.ext import Application

from tests.legacy_fixture import LEGACY_GROUP_ID, legacy_data, legacy_seed
from tests.telegram_driver import TelegramDriver
from wgcop import keyboards as kb, store as storage
from wgcop.app import register_handlers
from wgcop.handlers import expense as expense_handlers
from wgcop.homes import CB_HOME_SELECT
from wgcop.store import Store, member_for_uid

GROUP = LEGACY_GROUP_ID
ADMIN_UID = 1001                              # BOT_HANDLER_ID: admin role, no member profile
MEMBER_UIDS = (1002, 1003, 1004, 1005, 1006)  # the five legacy profiles
ORIGINAL_UIDS = (ADMIN_UID, *MEMBER_UIDS)
RECEIPT = {
    "items": [{"name": "Bread", "amount": 4.5}, {"name": "Cheese", "amount": 8.0}],
    "receipt_total": 12.5,
    "description": "Migros groceries",
}


def migrate(tmp_path, monkeypatch, seed=None) -> Store:
    path = tmp_path / "wg_data_alpha.json"
    path.write_text(json.dumps(legacy_data()), encoding="utf-8")
    store = Store(path)
    assert store.initialize(legacy_seed() if seed is None else seed) == "migrated"
    monkeypatch.setattr(storage, "_STORE", store)
    return store


@pytest.fixture
def receipt_ocr(monkeypatch):
    def scan(path, mime_type="image/jpeg"):
        return {**RECEIPT, "items": [dict(item) for item in RECEIPT["items"]]}

    monkeypatch.setattr(expense_handlers, "extract_items_from_receipt", scan)


class Session:
    """The running bot as its users see it: they type, send photos and tap buttons."""

    def __init__(self, app, driver):
        self.app, self.driver = app, driver

    async def _put(self, update):
        await self.app.update_queue.put(update)
        await self.app.update_queue.join()

    async def say(self, uid, cid, text):
        await self._put(self.driver.message_update(self.app.bot, uid, cid, text))

    async def photo(self, uid, cid):
        await self._put(self.driver.photo_update(self.app.bot, uid, cid))

    def draft(self, uid, cid) -> dict:
        return self.app.user_data.get(uid, {}).get(f"flow:{cid}") or {}

    async def tap(self, uid, cid, data):
        """Tap a button on the inline keyboard the user's draft is waiting on."""
        keyboard = self.draft(uid, cid).get("kb_msg")
        assert keyboard, f"user {uid} has no keyboard to tap in chat {cid}; bot said {self.replies(cid)[-3:]}"
        await self._put(self.driver.callback_update(self.app.bot, uid, cid, keyboard, data))

    async def tap_last_message(self, uid, cid, data):
        await self._put(self.driver.callback_update(self.app.bot, uid, cid, self.driver.next_message, data))

    def replies(self, cid):
        return self.driver.messages(cid)


def run_bot(scenario):
    async def main():
        driver = TelegramDriver()
        app = (Application.builder().token("123456:TEST-TOKEN-NOT-REAL")
               .request(driver).get_updates_request(driver).build())
        register_handlers(app)
        errors = []

        async def on_error(update, context):
            errors.append(context.error)

        app.add_error_handler(on_error)
        async with app:
            await app.start()
            try:
                await scenario(Session(app, driver))
            finally:
                await app.stop()
        assert not errors, errors

    asyncio.run(main())


async def book_food(s, uid, cid, description, amount="20", payer_mid="m1"):
    for text in (kb.BTN_ADD_EXPENSE, kb.BTN_MANUAL_ENTRY, description, amount):
        await s.say(uid, cid, text)
    await s.tap(uid, cid, kb.CB_PAYER_PREFIX + payer_mid)
    await s.tap(uid, cid, kb.CB_SPLIT_ALL)
    await s.tap(uid, cid, kb.CB_SPLIT_DONE)


def booked(store, description, home_id=GROUP):
    return [e for e in store.get_home(home_id)["expenses"] if e["description"] == description]


# ------------------------------------------------------------- every member
@pytest.mark.parametrize("uid", ORIGINAL_UIDS)
def test_every_original_member_books_food_in_the_group(tmp_path, monkeypatch, uid):
    store = migrate(tmp_path, monkeypatch)
    run_bot(lambda s: book_food(s, uid, GROUP, f"Food {uid}"))
    [expense] = booked(store, f"Food {uid}")
    assert expense["amount"] == 20.0 and len(expense["split_with"]) == len(MEMBER_UIDS)


@pytest.mark.parametrize("uid", ORIGINAL_UIDS)
def test_every_original_member_books_food_in_a_private_chat(tmp_path, monkeypatch, uid):
    store = migrate(tmp_path, monkeypatch)
    run_bot(lambda s: book_food(s, uid, uid, f"Food {uid}"))
    assert len(booked(store, f"Food {uid}")) == 1


@pytest.mark.parametrize("where", ["group", "private"])
def test_receipt_scan_books_food(tmp_path, monkeypatch, receipt_ocr, where):
    store = migrate(tmp_path, monkeypatch)
    uid = 1006
    cid = GROUP if where == "group" else uid

    async def scenario(s):
        await s.say(uid, cid, kb.BTN_ADD_EXPENSE)
        await s.say(uid, cid, kb.BTN_SCAN_RECEIPT)
        await s.photo(uid, cid)
        await s.tap(uid, cid, kb.CB_RECEIPT_ACCEPT)
        await s.tap(uid, cid, kb.CB_RECEIPT_DONE)
        await s.say(uid, cid, RECEIPT["description"])  # the suggested-description button
        await s.tap(uid, cid, kb.CB_PAYER_PREFIX + "m3")
        await s.tap(uid, cid, kb.CB_SPLIT_ALL)
        await s.tap(uid, cid, kb.CB_SPLIT_DONE)

    run_bot(scenario)
    [expense] = booked(store, RECEIPT["description"])
    assert expense["amount"] == 12.5 and expense["payer"] == "Janidputzä"
    assert [item["name"] for item in expense["items"]] == ["Bread", "Cheese"]


# ------------------------------------------------- the two lockouts, pinned
def test_original_member_without_a_linked_account_still_books_in_the_group(tmp_path, monkeypatch):
    seed = legacy_seed()
    del seed["member_uids"]["General Guysan"]  # no config id, no uid in the data: stays unlinked
    store = migrate(tmp_path, monkeypatch, seed)
    assert member_for_uid(store.get_home(GROUP), 1004) is None

    async def scenario(s):
        await book_food(s, 1004, GROUP, "Unlinked food")
        # A private chat cannot tell which home is meant: explain how to link, never stay silent.
        await s.say(1004, 1004, kb.BTN_ADD_EXPENSE)
        assert "/join" in s.replies(1004)[-1]

    run_bot(scenario)
    assert len(booked(store, "Unlinked food")) == 1


@pytest.mark.parametrize("where", ["group", "private"])
def test_text_typed_while_the_draft_waits_for_a_tap_keeps_the_draft(tmp_path, monkeypatch, receipt_ocr, where):
    store = migrate(tmp_path, monkeypatch)
    uid = 1002
    cid = GROUP if where == "group" else uid

    async def scenario(s):
        await s.say(uid, cid, kb.BTN_ADD_EXPENSE)
        await s.say(uid, cid, kb.BTN_SCAN_RECEIPT)
        await s.photo(uid, cid)
        await s.say(uid, cid, "yes")             # receipt review waits for a tap
        await s.tap(uid, cid, kb.CB_RECEIPT_ACCEPT)
        await s.say(uid, cid, "looks right")
        await s.tap(uid, cid, kb.CB_RECEIPT_DONE)
        await s.say(uid, cid, RECEIPT["description"])
        await s.say(uid, cid, "Nicci")           # payer waits for a tap
        await s.tap(uid, cid, kb.CB_PAYER_PREFIX + "m4")
        await s.say(uid, cid, "everyone")        # split waits for a tap
        await s.tap(uid, cid, kb.CB_SPLIT_ALL)
        await s.tap(uid, cid, kb.CB_SPLIT_DONE)
        assert not any("no longer valid" in text for text in s.replies(cid))

    run_bot(scenario)
    [expense] = booked(store, RECEIPT["description"])
    assert expense["payer"] == "Nicci Lopez"


# ----------------------------------------------------------- other homes
def test_a_second_home_leaves_the_original_home_bookable(tmp_path, monkeypatch):
    store = migrate(tmp_path, monkeypatch)
    store.create_home(-200, "Beach House", 1002, "Nicci")  # Nicci now belongs to two homes

    async def scenario(s):
        await book_food(s, 1002, GROUP, "Group food")       # the group decides the home
        await s.say(1002, 1002, kb.BTN_ADD_EXPENSE)          # a private chat asks which home
        assert "several homes" in s.replies(1002)[-1]
        await s.tap_last_message(1002, 1002, f"{CB_HOME_SELECT}{GROUP}")
        await book_food(s, 1002, 1002, "Private food")

    run_bot(scenario)
    assert len(booked(store, "Group food")) == 1 and len(booked(store, "Private food")) == 1
    assert store.get_home(-200)["expenses"] == []
