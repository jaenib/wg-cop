"""Admin panel handlers (relocated verbatim from maBot.py)."""
import copy
import logging
import os
import sys
from datetime import datetime

import pytz
from telegram import KeyboardButton, ReplyKeyboardMarkup, Update
from telegram.ext import CallbackContext, ConversationHandler

from wgcop.botconfig import (
    BOT_HANDLER_ID,
    CHRONICLER_ID,
    DATA_FILE,
    GI_ID,
    GROUP_CHAT_ID,
    GY_ID,
    JA_ID,
    NI_ID,
    OPENAI_API_KEY,
    RECEIPT_TOLERANCE_CHF,
    ROOT,
    TO_ID,
)
from wgcop.jobs import setup_weekly_job
from wgcop.keyboards import (
    ADMIN_BEER_COUNT,
    ADMIN_BEER_MEMBER,
    ADMIN_HOECK_DATE,
    ADMIN_REPORT_TIME,
    get_admin_keyboard,
    get_weekly_report_keyboard,
)
from wgcop.members import _get_member_name, _get_member_status, _match_member_name
from wgcop.receipts import _openai_lib
from wgcop.reports import _build_weekly_report, _get_chronicler_meta, _get_weekly_report_meta
from wgcop.store import load_data, save_data

logger = logging.getLogger(__name__)

def _secret_status(secret_value):
    text = str(secret_value or "").strip()
    if not text:
        return "missing"
    suffix = text[-4:] if len(text) >= 4 else text
    return f"configured (len={len(text)}, endswith=...{suffix})"


def _file_overview(path: str):
    if not os.path.exists(path):
        return "missing"

    size = os.path.getsize(path)
    tz = pytz.timezone("Europe/Berlin")
    mtime = datetime.fromtimestamp(os.path.getmtime(path), tz).strftime(
        "%Y-%m-%d %H:%M:%S %Z"
    )
    return f"present ({size} bytes, mtime {mtime})"


def _job_next_run_text(job):
    next_t = getattr(job, "next_t", None)
    if next_t is None:
        return "unknown"
    if isinstance(next_t, datetime):
        tz = pytz.timezone("Europe/Berlin")
        if next_t.tzinfo is None:
            next_t = tz.localize(next_t)
        else:
            next_t = next_t.astimezone(tz)
        return next_t.strftime("%Y-%m-%d %H:%M:%S %Z")
    return str(next_t)


async def admin_system_overview(update: Update, context: CallbackContext) -> None:
    if update.effective_user.id != BOT_HANDLER_ID:
        await update.message.reply_text("Unauthorized.")
        return

    data = load_data()

    members = data.get("members", []) or []
    active_members = 0
    vacating_members = 0
    other_member_status = 0
    for member in members:
        status = _get_member_status(member)
        if status == "active":
            active_members += 1
        elif status == "vacating":
            vacating_members += 1
        else:
            other_member_status += 1

    expenses = data.get("expenses", []) or []
    expense_total = 0.0
    receipt_expenses = 0
    receipt_items_total = 0
    for expense in expenses:
        try:
            expense_total += float(expense.get("amount", 0) or 0)
        except (TypeError, ValueError):
            pass
        items = expense.get("items")
        if isinstance(items, list):
            receipt_expenses += 1
            receipt_items_total += len(items)

    chores_totals = data.get("chores", {}) or {}
    total_chore_points = 0
    for points in chores_totals.values():
        try:
            total_chore_points += int(points)
        except (TypeError, ValueError):
            continue

    penalties = data.get("penalties", {}) or {}
    members_with_penalties = 0
    beers_owed_total = 0
    for owed_raw in penalties.values():
        try:
            owed = int(owed_raw)
        except (TypeError, ValueError):
            continue
        if owed > 0:
            members_with_penalties += 1
            beers_owed_total += owed

    weekly_meta = _get_weekly_report_meta(data)
    chronicler_meta = _get_chronicler_meta(data)
    data_file_path = str((ROOT / DATA_FILE).resolve())
    persistence_path = str((ROOT / "bot_persistence.pkl").resolve())

    user_content_keys = {
        "members",
        "expenses",
        "chores",
        "chore_log",
        "penalties",
        "last_week_violators",
    }
    non_user_keys = sorted(k for k in data.keys() if k not in user_content_keys)

    scheduled_jobs = [
        "weekly_penalty_check",
        "weekly_report_catchup",
        "chronicler_backup",
        "chronicler_initial_backup",
        "hoeck_reminder",
        "heartbeat",
    ]
    job_lines = []
    for job_name in scheduled_jobs:
        jobs = context.application.job_queue.get_jobs_by_name(job_name)
        if not jobs:
            job_lines.append(f"  - {job_name}: not scheduled")
            continue
        next_run = _job_next_run_text(jobs[0])
        job_lines.append(f"  - {job_name}: {len(jobs)} job(s), next={next_run}")

    lines = [
        "Admin System Overview (sanitized)",
        "",
        "Configured IDs:",
        f"  - BOT_HANDLER_ID: {BOT_HANDLER_ID}",
        f"  - GROUP_CHAT_ID (config): {GROUP_CHAT_ID}",
        f"  - group_chat_id (data override): {data.get('group_chat_id')}",
        f"  - effective weekly report chat: {data.get('group_chat_id') or GROUP_CHAT_ID}",
        f"  - CHRONICLER_ID: {CHRONICLER_ID}",
        f"  - NI_ID: {NI_ID}",
        f"  - GI_ID: {GI_ID}",
        f"  - GY_ID: {GY_ID}",
        f"  - TO_ID: {TO_ID}",
        f"  - JA_ID: {JA_ID}",
        "",
        "Runtime / Integrations:",
        f"  - python: {sys.version.split()[0]}",
        f"  - openai lib import: {bool(_openai_lib)}",
        f"  - OPENAI_API_KEY: {_secret_status(OPENAI_API_KEY)}",
        f"  - RECEIPT_TOLERANCE_CHF: {RECEIPT_TOLERANCE_CHF}",
        "",
        "Persistent Storage (no raw roommate content shown):",
        f"  - data file: {data_file_path} -> {_file_overview(data_file_path)}",
        f"  - persistence: {persistence_path} -> {_file_overview(persistence_path)}",
        f"  - top-level keys: {', '.join(sorted(data.keys()))}",
        f"  - non-user keys: {', '.join(non_user_keys) if non_user_keys else '(none)'}",
        "",
        "Dataset Counters (aggregated only):",
        f"  - members: {len(members)} (active={active_members}, vacating={vacating_members}, other={other_member_status})",
        f"  - expenses: {len(expenses)} entries, total={expense_total:.2f}, receipt_entries={receipt_expenses}, receipt_line_items={receipt_items_total}",
        f"  - chore totals map: {len(chores_totals)} members, cumulative_points={total_chore_points}",
        f"  - chore log entries: {len(data.get('chore_log', []) or [])}",
        f"  - penalties: {members_with_penalties} owing member(s), beers_owed_total={beers_owed_total}",
        f"  - last_week_violators tracked: {len(data.get('last_week_violators', {}) or {})}",
        "",
        "Scheduled Meta:",
        f"  - weekly_report_time: {data.get('weekly_report_time', '09:30')} (Europe/Berlin)",
        f"  - weekly_report_meta.last_sent: {weekly_meta.get('last_sent')}",
        f"  - chronicler_backup.greeting_sent: {chronicler_meta.get('greeting_sent')}",
        f"  - chronicler_backup.last_sent: {chronicler_meta.get('last_sent')}",
        f"  - wg_hoeck_date: {data.get('wg_hoeck_date')}",
        "",
        "Scheduled Jobs:",
        *job_lines,
    ]

    await update.message.reply_text("\n".join(lines), reply_markup=get_admin_keyboard())


# Weekly report handling
async def set_weekly_report(update: Update, context: CallbackContext) -> None:
    if update.effective_user.id != BOT_HANDLER_ID:
        await update.message.reply_text("Unauthorized.")
        return
    data = load_data()

    if update.effective_chat.type in ["group", "supergroup"]:
        data["group_chat_id"] = update.effective_chat.id
        save_data(data)
        await update.message.reply_text(
            "Weekly reports will be sent to this group every Monday!"
        )
    else:
        effective_id = data.get("group_chat_id") or GROUP_CHAT_ID
        if data.get("group_chat_id"):
            source = "set via bot command"
        elif GROUP_CHAT_ID:
            source = "from config"
        else:
            source = None

        if effective_id:
            try:
                chat = await context.bot.get_chat(effective_id)
                group_name = chat.title or str(effective_id)
            except Exception:
                group_name = str(effective_id)
            src_note = f" ({source})" if source else ""
            await update.message.reply_text(
                f"Active report group: {group_name}{src_note}\n\n"
                "To change the group, send this command from within the new group chat.",
                reply_markup=get_weekly_report_keyboard(),
            )
        else:
            await update.message.reply_text(
                "No report group configured yet. Send this command from the group chat where you want weekly reports.",
                reply_markup=get_weekly_report_keyboard(),
            )


async def admin_trigger_report(update: Update, context: CallbackContext) -> None:
    if update.effective_user.id != BOT_HANDLER_ID:
        await update.message.reply_text("Unauthorized.")
        return

    data = load_data()
    if not data.get("members") or not data.get("chores"):
        await update.message.reply_text(
            "Not enough data yet. Add members and log some chores first.",
            reply_markup=get_weekly_report_keyboard(),
        )
        return

    dry_data = copy.deepcopy(data)
    report = _build_weekly_report(dry_data)

    await update.message.reply_text(
        f"[DRY RUN — not sent to group, no data changed]\n\n{report}",
        reply_markup=get_weekly_report_keyboard(),
    )


async def admin_beer_start(update: Update, context: CallbackContext) -> int:
    if update.effective_user.id != BOT_HANDLER_ID:
        await update.message.reply_text("Unauthorized.")
        return ConversationHandler.END

    data = load_data()
    members = data.get("members", []) or []
    penalties = data.get("penalties", {}) or {}

    buttons = []
    for member in members:
        name = _get_member_name(member)
        owed = penalties.get(name, 0)
        buttons.append([KeyboardButton(f"{name} ({owed})")])
    buttons.append([KeyboardButton("Cancel")])

    await update.message.reply_text(
        "Select member to adjust beer count (current count shown):",
        reply_markup=ReplyKeyboardMarkup(buttons, resize_keyboard=True),
    )
    return ADMIN_BEER_MEMBER


async def admin_beer_member(update: Update, context: CallbackContext) -> int:
    text = update.message.text.strip()
    # Strip the " (N)" suffix from the button label
    if " (" in text and text.endswith(")"):
        text = text.rsplit(" (", 1)[0]

    data = load_data()
    members = data.get("members", []) or []
    matched = _match_member_name(members, text)
    if not matched:
        await update.message.reply_text("Member not found. Please pick from the keyboard.")
        return ADMIN_BEER_MEMBER

    member_name = _get_member_name(matched)
    penalties = data.get("penalties", {}) or {}
    current = penalties.get(member_name, 0)
    context.user_data["admin_beer_member"] = member_name

    await update.message.reply_text(
        f"{member_name} currently owes {current} beer(s).\n"
        f"Enter the new count (0 to clear):",
        reply_markup=ReplyKeyboardMarkup([[KeyboardButton("Cancel")]], resize_keyboard=True),
    )
    return ADMIN_BEER_COUNT


async def admin_beer_count(update: Update, context: CallbackContext) -> int:
    text = update.message.text.strip()
    member_name = context.user_data.get("admin_beer_member")

    try:
        count = int(text)
    except ValueError:
        await update.message.reply_text("Please enter a whole number (0 or above).")
        return ADMIN_BEER_COUNT

    if count < 0:
        await update.message.reply_text("Please enter 0 or a positive number.")
        return ADMIN_BEER_COUNT

    data = load_data()
    penalties = data.get("penalties", {}) or {}
    old = penalties.get(member_name, 0)
    if count == 0:
        penalties.pop(member_name, None)
    else:
        penalties[member_name] = count
    data["penalties"] = penalties
    save_data(data)

    await update.message.reply_text(
        f"Updated {member_name}: {old} → {count} beer(s).",
        reply_markup=get_admin_keyboard(),
    )
    return ConversationHandler.END


async def admin_hoeck_start(update: Update, context: CallbackContext) -> int:
    if update.effective_user.id != BOT_HANDLER_ID:
        await update.message.reply_text("Unauthorized.")
        return ConversationHandler.END

    data = load_data()
    current = data.get("wg_hoeck_date", "not set")
    await update.message.reply_text(
        f"Current WG-Höck date: {current}\n\n"
        "Enter the new date (DD.MM.YYYY or YYYY-MM-DD):",
        reply_markup=ReplyKeyboardMarkup([[KeyboardButton("Cancel")]], resize_keyboard=True),
    )
    return ADMIN_HOECK_DATE


async def admin_hoeck_date(update: Update, context: CallbackContext) -> int:
    text = update.message.text.strip()
    parsed = None
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d.%m.%y", "%d/%m/%Y"):
        try:
            parsed = datetime.strptime(text, fmt).date()
            break
        except ValueError:
            continue

    if not parsed:
        await update.message.reply_text(
            "Could not parse date. Please use DD.MM.YYYY or YYYY-MM-DD."
        )
        return 0

    data = load_data()
    data["wg_hoeck_date"] = parsed.isoformat()
    save_data(data)

    day_name = parsed.strftime("%A, %d.%m.%Y")
    await update.message.reply_text(
        f"WG-Höck set to {day_name}.",
        reply_markup=get_admin_keyboard(),
    )
    return ConversationHandler.END


async def admin_report_time_start(update: Update, context: CallbackContext) -> int:
    if update.effective_user.id != BOT_HANDLER_ID:
        await update.message.reply_text("Unauthorized.")
        return ConversationHandler.END

    data = load_data()
    current = data.get("weekly_report_time", "09:30")
    await update.message.reply_text(
        f"Current weekly report time: {current} (Europe/Berlin)\n\n"
        "Enter new time in HH:MM format (24h):",
        reply_markup=ReplyKeyboardMarkup([[KeyboardButton("Cancel")]], resize_keyboard=True),
    )
    return ADMIN_REPORT_TIME


async def admin_report_time_set(update: Update, context: CallbackContext) -> int:
    text = update.message.text.strip()
    try:
        h, m = text.split(":")
        h, m = int(h), int(m)
        if not (0 <= h <= 23 and 0 <= m <= 59):
            raise ValueError
    except (ValueError, AttributeError):
        await update.message.reply_text(
            "Invalid format. Please use HH:MM (e.g. 09:30 or 18:00)."
        )
        return ADMIN_REPORT_TIME

    time_str = f"{h:02d}:{m:02d}"
    data = load_data()
    data["weekly_report_time"] = time_str
    save_data(data)

    setup_weekly_job(context.application, hour=h, minute=m)

    await update.message.reply_text(
        f"Weekly report time set to {time_str} (Europe/Berlin, every Monday).",
        reply_markup=get_weekly_report_keyboard(),
    )
    return ConversationHandler.END
