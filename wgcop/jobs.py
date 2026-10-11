"""Scheduled work, data-driven per home.

One ``sweep`` job runs every minute and, for every home, performs whatever is
due according to that home's stored settings and metadata:

* weekly report: generate once per week (penalties applied inside
  ``Store.update``), then deliver the pending text to the home's group with
  exponential retry;
* WG-Höck reminder at noon local time on the configured date;
* chronicler export (that home only) on first assignment and weekly after;
* heartbeat to the home's ``handler`` every 50 hours.

Every step re-reads its home from the store right before acting, re-checks
that the recipient still holds the role after each awaited send, and only
then records delivery metadata. Nothing here needs rescheduling when a setting
changes in Telegram. The twice-daily local backup copies the whole data file.
"""
import logging
import os
import re
import shutil
import tempfile
import time
from datetime import datetime, timedelta
from random import randint

import pytz
from telegram.error import TelegramError
from telegram.ext import CallbackContext

from wgcop.botconfig import BACKUP_DIR, BACKUP_KEEP, DATA_FILE
from wgcop.homes import home_tz
from wgcop.reports import (
    _get_chronicler_meta,
    _get_weekly_report_meta,
    _parse_local_datetime,
    _weekly_report_is_due,
    generate_weekly_report,
)
from wgcop.store import HomeNotFound, StoreError, get_store

logger = logging.getLogger(__name__)

SWEEP_INTERVAL_SECONDS = 60
HEARTBEAT_INTERVAL = timedelta(hours=50)
CHRONICLER_INTERVAL = timedelta(days=7)
HOECK_HOUR = 12
MAX_RETRY_MINUTES = 60
XKCD_MAX = 3163
BACKUP_STAMP = re.compile(r"\.\d{8}-\d{6}\.bak$")


def _xkcd() -> str:
    return f"https://xkcd.com/{randint(1, XKCD_MAX)}/"


def _elapsed(last_iso, now, tz) -> timedelta | None:
    last = _parse_local_datetime(last_iso, tz)
    return None if last is None else now - last


def _fresh(home_id):
    """Latest copy of the home, or None if it vanished meanwhile."""
    try:
        return get_store().get_home(home_id)
    except HomeNotFound:
        return None


# ------------------------------------------------------------ local backup
def _write_local_backup():
    """Copy the data file into BACKUP_DIR (mode 0600) and prune old backups.

    Only files matching ``<basename>.YYYYMMDD-HHMMSS.bak`` are rotated; the
    migration archive and anything else sharing the prefix are left alone.
    """
    if not os.path.exists(DATA_FILE):
        return
    try:
        os.makedirs(BACKUP_DIR, exist_ok=True)
    except OSError as exc:
        logger.warning("Backup directory %s unavailable: %s", BACKUP_DIR, exc)
        return
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    base = os.path.basename(DATA_FILE)
    dest = os.path.join(BACKUP_DIR, f"{base}.{timestamp}.bak")
    try:
        shutil.copy2(DATA_FILE, dest)
        os.chmod(dest, 0o600)
        logger.info("Local backup written: %s", dest)
    except Exception as exc:
        logger.warning("Failed to write local backup: %s", exc)
        return
    try:
        # The single-home job named its copies "<stem>.<stamp>.bak"; rotate those too.
        stem = base[:-5] if base.endswith(".json") else base
        pattern = re.compile(r"^(?:" + re.escape(base) + "|" + re.escape(stem) + r")\.(\d{8}-\d{6})\.bak$")
        matches = {f: m.group(1) for f in os.listdir(BACKUP_DIR) if (m := pattern.match(f))}
        baks = sorted(matches, key=matches.get, reverse=True)
        for old in baks[BACKUP_KEEP:]:
            os.remove(os.path.join(BACKUP_DIR, old))
    except Exception as exc:
        logger.warning("Backup rotation failed: %s", exc)


async def scheduled_local_backup(context: CallbackContext) -> None:
    _write_local_backup()


# --------------------------------------------------------- weekly report
def _retry_due(meta, now, tz) -> bool:
    next_try = _parse_local_datetime(meta.get("next_try"), tz)
    return next_try is None or now >= next_try


async def weekly_report_step(context, home, now=None) -> str | None:
    """Generate if due, then try to deliver any pending text. Returns delivered text."""
    store = get_store()
    tz = home_tz(home)
    now = now or datetime.now(tz)
    hid = home["id"]
    settings = home.get("settings") or {}

    if settings.get("weekly_report_enabled", True) and _weekly_report_is_due(home, now):
        home, _ = store.update(hid, lambda h: generate_weekly_report(h, now))
        logger.info("Home %s: weekly report generated", hid)

    meta = _get_weekly_report_meta(home)
    pending = meta.get("pending")
    if not pending or not _retry_due(meta, now, tz):
        return None

    try:
        await context.bot.send_message(chat_id=home["group_chat_id"], text=pending)
    except TelegramError as exc:
        logger.error("Home %s: weekly report delivery failed: %s", hid, exc)

        def backoff(h):
            m = _get_weekly_report_meta(h)
            attempts = int(m.get("attempts") or 0) + 1
            m["attempts"] = attempts
            m["next_try"] = (now + timedelta(minutes=min(2 ** attempts, MAX_RETRY_MINUTES))).isoformat()

        store.update(hid, backoff)
        return None

    def delivered(h):
        m = _get_weekly_report_meta(h)
        if m.get("pending") != pending:
            return  # a newer report was generated meanwhile; leave it pending
        m["pending"] = None
        m["pending_since"] = None
        m["attempts"] = 0
        m["next_try"] = None
        m["delivered_at"] = now.isoformat()

    store.update(hid, delivered)
    logger.info("Home %s: weekly report delivered", hid)
    return pending


# --------------------------------------------------------- hoeck reminder
async def hoeck_step(context, home, now=None) -> bool:
    tz = home_tz(home)
    now = now or datetime.now(tz)
    date_str = (home.get("settings") or {}).get("wg_hoeck_date")
    if not date_str:
        return False
    try:
        hoeck_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return False
    if now.date() != hoeck_date or now.hour < HOECK_HOUR:
        return False
    if (home.get("hoeck_meta") or {}).get("reminded_for") == date_str:
        return False
    try:
        await context.bot.send_message(chat_id=home["group_chat_id"], text="📅 Reminder: WG-Höck is today! Be there.")
    except TelegramError as exc:
        logger.error("Home %s: failed to send Höck reminder: %s", home["id"], exc)
        return False

    def mark(h):
        h.setdefault("hoeck_meta", {})["reminded_for"] = date_str

    get_store().update(home["id"], mark)
    return True


# ------------------------------------------------------------- chronicler
async def _send_home_export(context, home, chat_id, caption) -> None:
    payload = get_store().export_home(home["id"])
    safe_name = "".join(c if c.isalnum() else "_" for c in home.get("name", "home"))[:40] or "home"
    filename = f"wg_{safe_name}_{datetime.now(pytz.UTC).strftime('%Y%m%d')}.json"
    with tempfile.NamedTemporaryFile("w+", suffix=".json", encoding="utf-8", delete=False) as fh:
        fh.write(payload)
        tmp_path = fh.name
    try:
        with open(tmp_path, "rb") as doc:
            await context.bot.send_document(chat_id=chat_id, document=doc, filename=filename, caption=caption)
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


def _still_chronicler(home_id, uid):
    fresh = _fresh(home_id)
    return fresh if fresh is not None and (fresh.get("roles") or {}).get("chronicler") == uid else None


async def chronicler_step(context, home, now=None) -> str | None:
    """Returns 'initial' | 'weekly' | None. Re-checks the role around every send."""
    hid = home["id"]
    chronicler = (home.get("roles") or {}).get("chronicler")
    if not chronicler:
        return None
    tz = home_tz(home)
    now = now or datetime.now(tz)
    meta = _get_chronicler_meta(home)
    is_new_holder = meta.get("uid") not in (None, chronicler)

    if not meta.get("greeting_sent") or is_new_holder:
        try:
            await context.bot.send_message(
                chat_id=chronicler,
                text=(
                    f"Greetings, Chronicler! You have been entrusted with safeguarding the history of "
                    f"'{home.get('name')}'. Here is the first archive snapshot for your special role."
                ),
            )
            fresh = _still_chronicler(hid, chronicler)
            if fresh is None:
                logger.info("Home %s: chronicler changed during greeting; export withheld", hid)
                return None
            await _send_home_export(context, fresh, chronicler, "Initial archive dispatch")
        except TelegramError as exc:
            logger.error("Home %s: failed to deliver initial chronicler backup: %s", hid, exc)
            return None

        def mark_initial(h):
            if (h.get("roles") or {}).get("chronicler") != chronicler:
                return
            m = _get_chronicler_meta(h)
            m["greeting_sent"] = True
            m["last_sent"] = now.isoformat()
            m["uid"] = chronicler

        get_store().update(hid, mark_initial)
        return "initial"

    elapsed = _elapsed(meta.get("last_sent"), now, tz)
    if elapsed is not None and elapsed < CHRONICLER_INTERVAL:
        return None
    fresh = _still_chronicler(hid, chronicler)
    if fresh is None:
        return None
    try:
        await _send_home_export(context, fresh, chronicler, "Weekly archive backup and an xkcd for your troubles")
        await context.bot.send_message(chat_id=chronicler, text=_xkcd())
    except TelegramError as exc:
        logger.error("Home %s: failed to send chronicler backup: %s", hid, exc)
        return None

    def mark_weekly(h):
        if (h.get("roles") or {}).get("chronicler") != chronicler:
            return
        m = _get_chronicler_meta(h)
        m["last_sent"] = now.isoformat()
        m["uid"] = chronicler

    get_store().update(hid, mark_weekly)
    return "weekly"


# -------------------------------------------------------------- heartbeat
async def heartbeat_step(context, home, now=None) -> bool:
    hid = home["id"]
    handler = (home.get("roles") or {}).get("handler")
    if not handler:
        return False
    tz = home_tz(home)
    now = now or datetime.now(tz)
    meta = home.get("heartbeat") or {}
    if meta.get("uid") in (None, handler):
        elapsed = _elapsed(meta.get("last_sent"), now, tz)
        if elapsed is not None and elapsed < HEARTBEAT_INTERVAL:
            return False
    try:
        await context.bot.send_message(
            chat_id=handler,
            text=f"I'm alive and watching over '{home.get('name')}', thanks for caring. Here's an xkcd for you {_xkcd()}",
        )
    except TelegramError as exc:
        logger.error("Home %s: failed to send heartbeat: %s", hid, exc)
        return False

    def mark(h):
        if (h.get("roles") or {}).get("handler") != handler:
            return  # role moved while we were sending; keep the new holder's metadata
        h["heartbeat"] = {"last_sent": now.isoformat(), "uid": handler}

    get_store().update(hid, mark)
    return True


# ------------------------------------------------------------------ sweep
async def sweep_home(context, home, now=None) -> None:
    """Run every step for one home, re-reading the home before each step so a
    role change made during an earlier awaited send is respected."""
    hid = home["id"]
    for step in (weekly_report_step, hoeck_step, chronicler_step, heartbeat_step):
        fresh = _fresh(hid)
        if fresh is None:
            return
        await step(context, fresh, now)


async def sweep(context: CallbackContext) -> None:
    try:
        home_ids = [h["id"] for h in get_store().list_homes()]
    except StoreError as exc:
        logger.error("Sweep skipped, store unreadable: %s", exc)
        return
    for hid in home_ids:
        home = _fresh(hid)
        if home is None:
            continue
        try:
            await sweep_home(context, home)
        except Exception:  # one broken home must not stop the others
            logger.exception("Sweep failed for home %s", hid)


def setup_jobs(application) -> None:
    application.job_queue.run_repeating(
        sweep,
        interval=SWEEP_INTERVAL_SECONDS,
        first=5,
        name="sweep",
        job_kwargs={"misfire_grace_time": 30, "coalesce": True, "max_instances": 1},
    )
    tz = pytz.timezone("Europe/Berlin")
    for hour in (4, 16):
        application.job_queue.run_daily(
            scheduled_local_backup,
            time=datetime.now(tz).replace(hour=hour, minute=0, second=0, microsecond=0).timetz(),
            name=f"local_backup_{hour:02d}h",
        )
    _write_local_backup()
