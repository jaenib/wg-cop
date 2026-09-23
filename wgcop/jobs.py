"""Scheduled jobs: weekly report, Höck reminder, chronicler + local backups, heartbeat."""
import logging
import os
import shutil
import time
from datetime import datetime, timedelta
from random import randint

import pytz
from telegram.error import TelegramError
from telegram.ext import CallbackContext

from wgcop.botconfig import (
    BACKUP_DIR,
    BACKUP_KEEP,
    BOT_HANDLER_ID,
    DATA_FILE,
    GROUP_CHAT_ID,
    _get_chronicler_chat_id,
)
from wgcop.reports import _get_chronicler_meta, _send_weekly_report, _weekly_report_is_due
from wgcop.store import load_data, save_data

logger = logging.getLogger(__name__)

def _write_local_backup():
    """Copy the data file into BACKUP_DIR and prune old backups (keeps BACKUP_KEEP most recent)."""
    if not os.path.exists(DATA_FILE):
        return
    os.makedirs(BACKUP_DIR, exist_ok=True)
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(BACKUP_DIR, f"wg_data_alpha.{timestamp}.bak")
    try:
        shutil.copy2(DATA_FILE, dest)
        logger.info("Local backup written: %s", dest)
    except Exception as exc:
        logger.warning("Failed to write local backup: %s", exc)
        return
    try:
        baks = sorted(
            (f for f in os.listdir(BACKUP_DIR) if f.endswith(".bak")),
            reverse=True,
        )
        for old in baks[BACKUP_KEEP:]:
            os.remove(os.path.join(BACKUP_DIR, old))
    except Exception as exc:
        logger.warning("Backup rotation failed: %s", exc)


async def scheduled_local_backup(context: CallbackContext) -> None:
    _write_local_backup()


async def check_weekly_penalties(context: CallbackContext) -> None:
    data = load_data()
    await _send_weekly_report(context, data)


async def send_missed_weekly_report_on_startup(context: CallbackContext) -> None:
    data = load_data()
    if not _weekly_report_is_due(data):
        return

    logger.info(
        "Weekly report was missed at the scheduled time; sending catch-up report on startup"
    )
    await _send_weekly_report(context, data)


async def send_hoeck_reminder(context: CallbackContext) -> None:
    """Fires daily at noon CET. Sends reminder if today is WG-Höck day."""
    data = load_data()
    hoeck_date_str = data.get("wg_hoeck_date")
    if not hoeck_date_str:
        return

    tz = pytz.timezone("Europe/Berlin")
    today = datetime.now(tz).date()
    try:
        hoeck_date = datetime.strptime(hoeck_date_str, "%Y-%m-%d").date()
    except ValueError:
        return

    if today != hoeck_date:
        return

    group_id = data.get("group_chat_id")
    if not group_id:
        group_id = GROUP_CHAT_ID

    try:
        await context.bot.send_message(
            chat_id=group_id,
            text="📅 Reminder: WG-Höck is today! Be there.",
        )
    except TelegramError as e:
        logger.error(f"Failed to send Höck reminder: {e}")


async def send_initial_chronicler_backup(context: CallbackContext) -> None:
    chronicler_chat_id = _get_chronicler_chat_id()
    if not chronicler_chat_id:
        logger.info("Chronicler ID is not configured; skipping initial backup dispatch.")
        return

    data = load_data()
    backup_meta = _get_chronicler_meta(data)

    if backup_meta.get("greeting_sent"):
        return

    greeting = (
        "Greetings, Chronicler! You have been entrusted with safeguarding our household's "
        "history. Here is the first archive snapshot for your special role."
    )

    try:
        await context.bot.send_message(chat_id=chronicler_chat_id, text=greeting)
        with open(DATA_FILE, "rb") as doc:
            await context.bot.send_document(
                chat_id=chronicler_chat_id,
                document=doc,
                filename=DATA_FILE,
                caption="Initial archive dispatch",
            )
    except (FileNotFoundError, TelegramError) as e:
        logger.error(f"Failed to deliver initial backup to chronicler: {e}")
        return

    backup_meta["greeting_sent"] = True
    backup_meta["last_sent"] = datetime.now(pytz.timezone("Europe/Berlin")).isoformat()
    save_data(data)


async def send_chronicler_backup(context: CallbackContext) -> None:
    chronicler_chat_id = _get_chronicler_chat_id()
    if not chronicler_chat_id:
        logger.info("Chronicler ID is not configured; skipping scheduled backup dispatch.")
        return

    data = load_data()
    backup_meta = _get_chronicler_meta(data)
    comic = randint(1, 3163)
    link = f"https://xkcd.com/{comic}/"

    try:
        with open(DATA_FILE, "rb") as doc:
            await context.bot.send_document(
                chat_id=chronicler_chat_id,
                document=doc,
                filename=DATA_FILE,
                caption=f"Weekly archive backup and an xkcd for your troubles",
            )
    except (FileNotFoundError, TelegramError) as e:
        logger.error(f"Failed to send chronicler backup: {e}")
    try:
        await context.bot.send_message(chat_id=chronicler_chat_id, text=f"{link}")
    except TelegramError as e:
        logger.error(f"Failed to send xkcd link to chronicler: {e}")
        return

    backup_meta["last_sent"] = datetime.now(pytz.timezone("Europe/Berlin")).isoformat()
    save_data(data)


def setup_chronicler_backup_job(application):
    if not _get_chronicler_chat_id():
        logger.info("Chronicler ID is not configured; chronicler backup jobs will not be scheduled.")
        return

    interval = timedelta(days=7).total_seconds()
    application.job_queue.run_repeating(
        send_chronicler_backup,
        interval=interval,
        first=interval,
        name="chronicler_backup",
    )
    application.job_queue.run_once(
        send_initial_chronicler_backup,
        when=0,
        name="chronicler_initial_backup",
    )


def setup_local_backup_job(application):
    """Schedule twice-daily local backups at 04:00 and 16:00 Berlin time."""
    tz = pytz.timezone("Europe/Berlin")
    for hour in (4, 16):
        application.job_queue.run_daily(
            scheduled_local_backup,
            time=datetime.now(tz).replace(hour=hour, minute=0, second=0, microsecond=0).timetz(),
            name=f"local_backup_{hour:02d}h",
        )
    # Write one immediately on startup so there's always a fresh copy.
    _write_local_backup()


def setup_weekly_job(application, hour=9, minute=30):
    tz = pytz.timezone("Europe/Berlin")
    target_time = datetime.now(tz).replace(hour=hour, minute=minute, second=0, microsecond=0)

    if target_time.weekday() != 0 or datetime.now(tz) > target_time:
        days_until_monday = (7 - target_time.weekday()) % 7
        if days_until_monday == 0:
            days_until_monday = 7
        target_time = target_time + timedelta(days=days_until_monday)

    current_time = datetime.now(tz)
    seconds_until_target = (target_time - current_time).total_seconds()

    for job in application.job_queue.get_jobs_by_name("weekly_penalty_check"):
        job.schedule_removal()

    application.job_queue.run_repeating(
        check_weekly_penalties,
        interval=timedelta(days=7).total_seconds(),
        first=seconds_until_target,
        name="weekly_penalty_check",
        job_kwargs={
            "misfire_grace_time": 600,
            "coalesce": True,
            "max_instances": 1,
        },
    )
    logger.info(
        f"Weekly report scheduled for {target_time.strftime('%Y-%m-%d %H:%M:%S')}"
    )


async def send_alive(context: CallbackContext) -> None:
    """Send a periodic heartbeat message to confirm the bot is running."""
    comic = randint(1, 3163)
    link = f"https://xkcd.com/{comic}/"
    try:
        await context.bot.send_message(chat_id=BOT_HANDLER_ID, text=f"I'm alive, thanks for caring, here's an xkcd for you {link}")
    except TelegramError as e:
        logger.error(f"Failed to send heartbeat: {e}")
