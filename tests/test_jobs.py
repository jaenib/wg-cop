"""The data-driven sweep: weekly report once per week with retried delivery,
Höck reminder, chronicler export, heartbeat, and local backups."""
import json
import os
from datetime import datetime, timedelta

import pytest
import pytz
from telegram.error import TelegramError

from tests.fakes import FakeContext, run
from tests.legacy_fixture import LEGACY_GROUP_ID, legacy_data, legacy_seed
from wgcop import jobs
from wgcop import store as store_mod
from wgcop.store import Store

BERLIN = pytz.timezone("Europe/Berlin")
TUESDAY = BERLIN.localize(datetime(2026, 9, 22, 10, 0))


@pytest.fixture
def store(tmp_path):
    path = tmp_path / "wg_data_alpha.json"
    path.write_text(json.dumps(legacy_data()), encoding="utf-8")
    s = Store(path)
    s.initialize(legacy_seed())
    # make the report due: last_sent long ago
    s.update(LEGACY_GROUP_ID, lambda h: h["weekly_report_meta"].__setitem__("last_sent", None))
    store_mod.set_store(s)
    yield s
    store_mod.set_store(None)


def home(store):
    return store.get_home(LEGACY_GROUP_ID)


# ------------------------------------------------------------ weekly report
def test_weekly_report_generated_once_and_delivered(store):
    ctx = FakeContext()
    text = run(jobs.weekly_report_step(ctx, home(store), TUESDAY))
    assert text and "Mario's Monday Mauling, 2026-09-22" in text
    assert ctx.bot.sent[-1][0] == LEGACY_GROUP_ID
    h = home(store)
    assert h["penalties"]["Gjango Gmüeshole"] == 2      # flagged last week -> +1
    assert h["penalties"]["General Guysan"] == 2        # first warning, no new beer
    assert h["last_week_violators"] == {"gjango gmüeshole": True, "general guysan": True, "janidputzä": True}
    assert h["weekly_report_meta"]["pending"] is None

    # same week again: nothing happens, no double penalty
    assert run(jobs.weekly_report_step(ctx, h, TUESDAY + timedelta(hours=3))) is None
    assert len(ctx.bot.sent) == 1
    assert home(store)["penalties"]["Gjango Gmüeshole"] == 2


def test_weekly_report_delivery_is_retried_without_reapplying_penalties(store):
    ctx = FakeContext()
    ctx.bot.fail_send = TelegramError("group unreachable")
    assert run(jobs.weekly_report_step(ctx, home(store), TUESDAY)) is None
    h = home(store)
    assert h["penalties"]["Gjango Gmüeshole"] == 2
    meta = h["weekly_report_meta"]
    assert meta["pending"] and meta["attempts"] == 1 and meta["next_try"]

    # too early for the retry
    assert run(jobs.weekly_report_step(ctx, h, TUESDAY + timedelta(minutes=1))) is None
    assert home(store)["weekly_report_meta"]["attempts"] == 1

    ctx.bot.fail_send = None
    delivered = run(jobs.weekly_report_step(ctx, home(store), TUESDAY + timedelta(minutes=5)))
    assert delivered == meta["pending"]
    h = home(store)
    assert h["weekly_report_meta"]["pending"] is None
    assert h["penalties"]["Gjango Gmüeshole"] == 2      # applied exactly once


def test_weekly_report_respects_disable_and_time(store):
    ctx = FakeContext()
    store.update(LEGACY_GROUP_ID, lambda h: h["settings"].__setitem__("weekly_report_enabled", False))
    assert run(jobs.weekly_report_step(ctx, home(store), TUESDAY)) is None
    assert ctx.bot.sent == []
    store.update(LEGACY_GROUP_ID, lambda h: h["settings"].__setitem__("weekly_report_enabled", True))
    monday_early = BERLIN.localize(datetime(2026, 9, 21, 9, 0))  # report time is 13:12
    assert run(jobs.weekly_report_step(ctx, home(store), monday_early)) is None
    monday_late = BERLIN.localize(datetime(2026, 9, 21, 13, 13))
    assert run(jobs.weekly_report_step(ctx, home(store), monday_late))


# ------------------------------------------------------------------- hoeck
def test_hoeck_reminder_fires_once_at_noon(store):
    ctx = FakeContext()
    store.update(LEGACY_GROUP_ID, lambda h: h["settings"].__setitem__("wg_hoeck_date", "2026-09-22"))
    assert run(jobs.hoeck_step(ctx, home(store), TUESDAY)) is False       # 10:00
    noon = BERLIN.localize(datetime(2026, 9, 22, 12, 30))
    assert run(jobs.hoeck_step(ctx, home(store), noon)) is True
    assert "WG-Höck is today" in ctx.bot.sent[-1][1]
    assert run(jobs.hoeck_step(ctx, home(store), noon + timedelta(hours=1))) is False
    assert len(ctx.bot.sent) == 1


# -------------------------------------------------------------- chronicler
def test_chronicler_initial_then_weekly_export_of_this_home_only(store):
    store.create_home(-200, "Beach", 77, "Zoe")
    store.update(LEGACY_GROUP_ID, lambda h: h["roles"].__setitem__("chronicler", 1002))
    store.update(LEGACY_GROUP_ID, lambda h: h["chronicler_backup"].__setitem__("greeting_sent", False))
    ctx = FakeContext()
    assert run(jobs.chronicler_step(ctx, home(store), TUESDAY)) == "initial"
    assert ctx.bot.sent[-1][0] == 1002 and "Chronicler" in ctx.bot.sent[-1][1]
    chat_id, filename, caption, payload = ctx.bot.documents[-1]
    assert chat_id == 1002 and filename.startswith("wg_WG_") and caption == "Initial archive dispatch"
    exported = json.loads(payload)
    assert exported["home"]["name"] == "WG" and "Beach" not in payload.decode()
    assert home(store)["chronicler_backup"]["greeting_sent"] is True

    assert run(jobs.chronicler_step(ctx, home(store), TUESDAY + timedelta(days=1))) is None
    assert run(jobs.chronicler_step(ctx, home(store), TUESDAY + timedelta(days=8))) == "weekly"
    assert ctx.bot.sent[-1][1].startswith("https://xkcd.com/")


def test_chronicler_without_role_is_silent(store):
    ctx = FakeContext()
    assert run(jobs.chronicler_step(ctx, home(store), TUESDAY)) is None
    assert ctx.bot.sent == [] and ctx.bot.documents == []


# --------------------------------------------------------------- heartbeat
def test_heartbeat_every_50_hours_to_handler(store):
    ctx = FakeContext()
    assert run(jobs.heartbeat_step(ctx, home(store), TUESDAY)) is True
    assert ctx.bot.sent[-1][0] == 1001 and "alive" in ctx.bot.sent[-1][1]
    assert run(jobs.heartbeat_step(ctx, home(store), TUESDAY + timedelta(hours=49))) is False
    assert run(jobs.heartbeat_step(ctx, home(store), TUESDAY + timedelta(hours=51))) is True
    store.update(LEGACY_GROUP_ID, lambda h: h["roles"].__setitem__("handler", None))
    assert run(jobs.heartbeat_step(ctx, home(store), TUESDAY + timedelta(hours=200))) is False


# ------------------------------------------------------------------- sweep
def test_sweep_isolates_failures_per_home(store, monkeypatch):
    store.create_home(-200, "Beach", 77, "Zoe")
    seen = []

    async def fake_sweep_home(context, h, now=None):
        seen.append(h["id"])
        if h["id"] == str(LEGACY_GROUP_ID):
            raise RuntimeError("boom")

    monkeypatch.setattr(jobs, "sweep_home", fake_sweep_home)
    run(jobs.sweep(FakeContext()))
    assert set(seen) == {str(LEGACY_GROUP_ID), "-200"}


def test_send_failures_do_not_mark_state(store):
    ctx = FakeContext()
    ctx.bot.fail_send = TelegramError("nope")
    assert run(jobs.heartbeat_step(ctx, home(store), TUESDAY)) is False
    assert "heartbeat" not in home(store)
    store.update(LEGACY_GROUP_ID, lambda h: h["settings"].__setitem__("wg_hoeck_date", "2026-09-22"))
    noon = BERLIN.localize(datetime(2026, 9, 22, 12, 30))
    assert run(jobs.hoeck_step(ctx, home(store), noon)) is False
    assert "hoeck_meta" not in home(store)


# ------------------------------------------------------------ local backup
def test_local_backup_rotates(store, tmp_path, monkeypatch):
    backup_dir = tmp_path / "backups"
    monkeypatch.setattr(jobs, "DATA_FILE", str(store.path))
    monkeypatch.setattr(jobs, "BACKUP_DIR", str(backup_dir))
    monkeypatch.setattr(jobs, "BACKUP_KEEP", 3)
    stamps = iter(f"20260922-{i:06d}" for i in range(10))
    monkeypatch.setattr(jobs.time, "strftime", lambda fmt: next(stamps))
    for _ in range(5):
        jobs._write_local_backup()
    files = sorted(os.listdir(backup_dir))
    assert len(files) == 3 and files[-1].endswith("000004.bak")
