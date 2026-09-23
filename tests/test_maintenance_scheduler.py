"""Delivery failures and concurrent role changes must preserve home boundaries."""
import asyncio
import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytz
from telegram.error import TelegramError

from wgcop import jobs, store as storage
from wgcop.store import Store, new_member


@pytest.fixture
def scheduled_home(tmp_path, monkeypatch):
    store = Store(tmp_path / "homes.json")
    store.initialize()
    monkeypatch.setattr(storage, "_STORE", store)
    home = store.create_home(-100, "First", 10, "Alice")

    def seed(h):
        h["members"].append(new_member(h, "Bob", uid=20))
        h["chores"] = {"Alice": 10, "Bob": 0}
        h["last_week_violators"] = {"bob": True}
        h["roles"]["chronicler"] = 30

    store.update(home["id"], seed)
    now = pytz.timezone("Europe/Berlin").localize(datetime(2026, 9, 21, 10))
    return store, home["id"], now


def test_moving_report_time_later_does_not_charge_same_week_twice(scheduled_home):
    store, hid, now = scheduled_home
    context = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))

    async def scenario():
        await jobs.weekly_report_step(context, store.get_home(hid), now)
        charged = store.get_home(hid)["penalties"].copy()
        assert charged["Bob"] == 1
        store.update(hid, lambda h: h["settings"].update(weekly_report_time="15:00"))
        await jobs.weekly_report_step(context, store.get_home(hid), now.replace(hour=16))
        assert store.get_home(hid)["penalties"] == charged
        assert context.bot.send_message.await_count == 1

    asyncio.run(scenario())


def test_report_retry_keeps_one_penalty_and_the_original_text(scheduled_home):
    store, hid, now = scheduled_home
    send = AsyncMock(side_effect=[TelegramError("offline"), None])
    context = SimpleNamespace(bot=SimpleNamespace(send_message=send))

    async def scenario():
        await jobs.weekly_report_step(context, store.get_home(hid), now)
        failed = store.get_home(hid)
        assert failed["weekly_report_meta"]["pending"]
        assert failed["penalties"]["Bob"] == 1
        await jobs.weekly_report_step(context, failed, now + timedelta(minutes=3))
        saved = store.get_home(hid)
        assert saved["penalties"] == failed["penalties"]
        assert not saved["weekly_report_meta"]["pending"]
        assert send.await_args_list[0].kwargs["text"] == send.await_args_list[1].kwargs["text"]

    asyncio.run(scenario())


def test_revoked_chronicler_gets_no_export_after_greeting(scheduled_home):
    store, hid, now = scheduled_home

    async def revoke(**kwargs):
        def change(h):
            h["roles"]["chronicler"] = 40
            h["chronicler_backup"] = {"greeting_sent": False, "last_sent": None}
        store.update(hid, change)

    bot = SimpleNamespace(send_message=AsyncMock(side_effect=revoke), send_document=AsyncMock())
    asyncio.run(jobs.chronicler_step(SimpleNamespace(bot=bot), store.get_home(hid), now))
    bot.send_document.assert_not_awaited()
    assert store.get_home(hid)["chronicler_backup"]["greeting_sent"] is False


def test_chronicler_export_contains_only_its_home(scheduled_home):
    store, hid, now = scheduled_home
    store.create_home(-200, "Other home's private name", 99, "Private member")
    exports = []

    async def capture(**kwargs):
        exports.append((kwargs["chat_id"], json.load(kwargs["document"])))

    bot = SimpleNamespace(send_message=AsyncMock(), send_document=AsyncMock(side_effect=capture))
    asyncio.run(jobs.chronicler_step(SimpleNamespace(bot=bot), store.get_home(hid), now))
    assert len(exports) == 1
    assert exports[0][0] == 30
    assert exports[0][1]["home"]["id"] == hid
    assert "Other home's private name" not in json.dumps(exports)
    assert "Private member" not in json.dumps(exports)


def test_changed_handler_keeps_its_reset_delivery_metadata(scheduled_home):
    store, hid, now = scheduled_home

    async def replace_handler(**kwargs):
        def change(h):
            h["roles"]["handler"] = 40
            h["heartbeat"] = {"last_sent": None}
        store.update(hid, change)

    bot = SimpleNamespace(send_message=AsyncMock(side_effect=replace_handler))
    asyncio.run(jobs.heartbeat_step(SimpleNamespace(bot=bot), store.get_home(hid), now))
    assert store.get_home(hid)["heartbeat"]["last_sent"] is None


def test_sweep_refreshes_roles_after_previous_step_await(scheduled_home, monkeypatch):
    store, hid, now = scheduled_home

    async def change_roles(context, home, reference=None):
        store.update(hid, lambda h: h["roles"].update(chronicler=40, handler=50))

    chronicler = AsyncMock()
    heartbeat = AsyncMock()
    monkeypatch.setattr(jobs, "weekly_report_step", change_roles)
    monkeypatch.setattr(jobs, "hoeck_step", AsyncMock())
    monkeypatch.setattr(jobs, "chronicler_step", chronicler)
    monkeypatch.setattr(jobs, "heartbeat_step", heartbeat)
    asyncio.run(jobs.sweep_home(SimpleNamespace(), store.get_home(hid), now))
    assert chronicler.await_args.args[1]["roles"]["chronicler"] == 40
    assert heartbeat.await_args.args[1]["roles"]["handler"] == 50


def test_backup_rotation_preserves_migration_archive_and_other_files(tmp_path, monkeypatch):
    data = tmp_path / "homes.json"
    data.write_text('{"private": true}')
    archive = tmp_path / "homes.json.pre-multihome.bak"
    unrelated = tmp_path / "homes.json.other.20260901-000000.bak"
    old_backup = tmp_path / "homes.json.20260901-000000.bak"
    for path in (archive, unrelated, old_backup):
        path.write_text("preserve me")
    monkeypatch.setattr(jobs, "DATA_FILE", str(data))
    monkeypatch.setattr(jobs, "BACKUP_DIR", str(tmp_path))
    monkeypatch.setattr(jobs, "BACKUP_KEEP", 1)
    jobs._write_local_backup()
    assert archive.read_text() == "preserve me"
    assert unrelated.read_text() == "preserve me"
    assert not old_backup.exists()
    backups = [p for p in tmp_path.glob("*.bak") if p not in (archive, unrelated)]
    assert len(backups) == 1
    assert backups[0].read_bytes() == data.read_bytes()
    assert backups[0].stat().st_mode & 0o077 == 0
