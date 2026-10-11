"""Admin panel: overview, weekly report controls, penalty adjustment, Höck date."""
import copy
import logging
import os
import sys
from datetime import datetime

from telegram import KeyboardButton, ReplyKeyboardMarkup, Update
from telegram.ext import CallbackContext, ConversationHandler

from wgcop.botconfig import DATA_FILE, OPENAI_API_KEY, RECEIPT_TOLERANCE_CHF
from wgcop.handlers.common import admin_home, cancel, continue_admin_flow
from wgcop.homes import display_name_for_uid, end_flow, home_tz, role_holders, start_flow
from wgcop.keyboards import (
    ADMIN_HOECK_DATE,
    ADMIN_PENALTY_COUNT,
    ADMIN_PENALTY_MEMBER,
    ADMIN_REPORT_TIME,
    get_admin_keyboard,
    get_cancel_keyboard,
    get_weekly_report_keyboard,
)
from wgcop.members import _match_member_name
from wgcop.receipts import _openai_lib
from wgcop.reports import _build_weekly_report, _get_chronicler_meta, _get_weekly_report_meta, penalty_unit
from wgcop.store import get_store

logger = logging.getLogger(__name__)


# --------------------------------------------------------------- overview
def _secret_status(secret_value):
    text = str(secret_value or "").strip()
    if not text:
        return "missing"
    suffix = text[-4:] if len(text) >= 4 else text
    return f"configured (len={len(text)}, endswith=...{suffix})"


def _file_overview(path: str, tz):
    if not os.path.exists(path):
        return "missing"
    size = os.path.getsize(path)
    mtime = datetime.fromtimestamp(os.path.getmtime(path), tz).strftime("%Y-%m-%d %H:%M:%S %Z")
    return f"present ({size} bytes, mtime {mtime})"


def _job_next_run_text(job, tz):
    next_t = getattr(job, "next_t", None)
    if next_t is None:
        return "unknown"
    if isinstance(next_t, datetime):
        next_t = tz.localize(next_t) if next_t.tzinfo is None else next_t.astimezone(tz)
        return next_t.strftime("%Y-%m-%d %H:%M:%S %Z")
    return str(next_t)


async def admin_system_overview(update: Update, context: CallbackContext) -> None:
    home = await admin_home(update, context)
    if home is None:
        return
    tz = home_tz(home)
    members = home.get("members", []) or []
    active = sum(1 for m in members if m.get("status") == "active")
    vacating = sum(1 for m in members if m.get("status") == "vacating")
    linked = sum(1 for m in members if m.get("uid"))

    expenses = home.get("expenses", []) or []
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

    total_chore_points = sum(int(p) for p in (home.get("chores") or {}).values() if isinstance(p, (int, float)))
    penalties = home.get("penalties", {}) or {}
    owing = {k: int(v) for k, v in penalties.items() if isinstance(v, (int, float)) and v > 0}

    weekly_meta = _get_weekly_report_meta(home)
    chronicler_meta = _get_chronicler_meta(home)
    settings = home.get("settings") or {}
    role_lines = [
        f"  - {display_name_for_uid(home, uid)} ({uid}): {', '.join(names)}"
        for uid, names in sorted(role_holders(home).items())
    ]

    job_lines = []
    for job_name in ("sweep", "local_backup_04h", "local_backup_16h"):
        jobs = context.application.job_queue.get_jobs_by_name(job_name)
        if not jobs:
            job_lines.append(f"  - {job_name}: not scheduled")
        else:
            job_lines.append(f"  - {job_name}: next={_job_next_run_text(jobs[0], tz)}")

    data_file_path = os.path.abspath(DATA_FILE)
    store = get_store()
    home_count = len(store.list_homes())

    lines = [
        f"Admin System Overview — '{home['name']}' (sanitized)",
        "",
        "Home:",
        f"  - id / group: {home['id']}",
        f"  - revision: {home.get('revision')}",
        f"  - settings: {', '.join(f'{k}={v}' for k, v in sorted(settings.items()))}",
        "Roles:",
        *(role_lines or ["  - (none)"]),
        f"  - pending join requests: {len(home.get('join_requests') or {})}",
        "",
        "Runtime / Integrations:",
        f"  - python: {sys.version.split()[0]}",
        f"  - openai lib import: {bool(_openai_lib)}",
        f"  - OPENAI_API_KEY: {_secret_status(OPENAI_API_KEY)}",
        f"  - RECEIPT_TOLERANCE_CHF: {RECEIPT_TOLERANCE_CHF}",
        f"  - homes served by this bot: {home_count}",
        "",
        "Persistent Storage (no raw roommate content shown):",
        f"  - data file: {data_file_path} -> {_file_overview(data_file_path, tz)}",
        f"  - persistence: {_file_overview('bot_persistence.pkl', tz)}",
        "",
        "Dataset Counters (aggregated only):",
        f"  - members: {len(members)} (active={active}, vacating={vacating}, linked={linked})",
        f"  - expenses: {len(expenses)} entries, total={expense_total:.2f}, receipt_entries={receipt_expenses}, receipt_line_items={receipt_items_total}",
        f"  - chore totals map: {len(home.get('chores') or {})} members, cumulative_points={total_chore_points}",
        f"  - chore log entries: {len(home.get('chore_log', []) or [])}",
        f"  - penalties: {len(owing)} owing member(s), total={sum(owing.values())}",
        f"  - last_week_violators tracked: {len(home.get('last_week_violators', {}) or {})}",
        "",
        "Scheduled Meta:",
        f"  - weekly_report_meta.last_sent: {weekly_meta.get('last_sent')}",
        f"  - weekly_report_meta.pending: {'yes' if weekly_meta.get('pending') else 'no'} (attempts={weekly_meta.get('attempts', 0)})",
        f"  - chronicler_backup.greeting_sent: {chronicler_meta.get('greeting_sent')}",
        f"  - chronicler_backup.last_sent: {chronicler_meta.get('last_sent')}",
        f"  - heartbeat.last_sent: {(home.get('heartbeat') or {}).get('last_sent')}",
        "",
        "Scheduled Jobs:",
        *job_lines,
    ]
    await update.message.reply_text("\n".join(lines), reply_markup=get_admin_keyboard())


# ----------------------------------------------------------- weekly report
async def admin_trigger_report(update: Update, context: CallbackContext) -> None:
    home = await admin_home(update, context)
    if home is None:
        return
    if not home.get("members") or not home.get("chores"):
        await update.message.reply_text(
            "Not enough data yet. Add members and log some chores first.", reply_markup=get_weekly_report_keyboard()
        )
        return
    report = _build_weekly_report(copy.deepcopy(home))
    await update.message.reply_text(
        f"[DRY RUN — not sent to group, no data changed]\n\n{report}", reply_markup=get_weekly_report_keyboard()
    )


async def admin_toggle_weekly(update: Update, context: CallbackContext) -> None:
    home = await admin_home(update, context)
    if home is None:
        return

    def apply(h):
        s = h.setdefault("settings", {})
        s["weekly_report_enabled"] = not s.get("weekly_report_enabled", True)
        return s["weekly_report_enabled"]

    _, enabled = get_store().update(home["id"], apply)
    await update.message.reply_text(
        f"Weekly report is now {'enabled' if enabled else 'disabled'}.", reply_markup=get_weekly_report_keyboard()
    )


async def admin_report_time_start(update: Update, context: CallbackContext) -> int:
    home = await admin_home(update, context)
    if home is None:
        return ConversationHandler.END
    start_flow(context, update.effective_chat.id, home)
    current = home["settings"].get("weekly_report_time", "09:30")
    await update.message.reply_text(
        f"Current weekly report time: {current} ({home['settings'].get('timezone', 'Europe/Berlin')})\n\n"
        "Enter new time in HH:MM format (24h):",
        reply_markup=get_cancel_keyboard(),
    )
    return ADMIN_REPORT_TIME


async def admin_report_time_set(update: Update, context: CallbackContext) -> int:
    home, flow = await continue_admin_flow(update, context)
    if home is None:
        return ConversationHandler.END
    text = update.message.text.strip()
    if text.lower() == "cancel":
        return await cancel(update, context)
    try:
        h, m = text.split(":")
        h, m = int(h), int(m)
        if not (0 <= h <= 23 and 0 <= m <= 59):
            raise ValueError
    except (ValueError, AttributeError):
        await update.message.reply_text("Invalid format. Please use HH:MM (e.g. 09:30 or 18:00).")
        return ADMIN_REPORT_TIME

    time_str = f"{h:02d}:{m:02d}"
    get_store().update(home["id"], lambda hm: hm["settings"].__setitem__("weekly_report_time", time_str))
    end_flow(context, update.effective_chat.id)
    await update.message.reply_text(
        f"Weekly report time set to {time_str} ({home['settings'].get('timezone', 'Europe/Berlin')}, every Monday).",
        reply_markup=get_weekly_report_keyboard(),
    )
    return ConversationHandler.END


# -------------------------------------------------------------- penalties
async def admin_penalty_start(update: Update, context: CallbackContext) -> int:
    home = await admin_home(update, context)
    if home is None:
        return ConversationHandler.END
    start_flow(context, update.effective_chat.id, home)
    penalties = home.get("penalties", {}) or {}
    buttons = [[KeyboardButton(f"{m['name']} ({penalties.get(m['name'], 0)})")] for m in home["members"]]
    buttons.append([KeyboardButton("Cancel")])
    await update.message.reply_text(
        f"Select member to adjust {penalty_unit(home)} count (current count shown):",
        reply_markup=ReplyKeyboardMarkup(buttons, resize_keyboard=True),
    )
    return ADMIN_PENALTY_MEMBER


async def admin_penalty_member(update: Update, context: CallbackContext) -> int:
    home, flow = await continue_admin_flow(update, context)
    if home is None:
        return ConversationHandler.END
    text = update.message.text.strip()
    if text.lower() == "cancel":
        return await cancel(update, context)
    if " (" in text and text.endswith(")"):
        text = text.rsplit(" (", 1)[0]
    matched = _match_member_name(home["members"], text)
    if not matched:
        await update.message.reply_text("Member not found. Please pick from the keyboard.")
        return ADMIN_PENALTY_MEMBER
    name = matched["name"]
    current = (home.get("penalties", {}) or {}).get(name, 0)
    flow["admin_penalty_member"] = name
    await update.message.reply_text(
        f"{name} currently owes {current} {penalty_unit(home)}(s).\nEnter the new count (0 to clear):",
        reply_markup=get_cancel_keyboard(),
    )
    return ADMIN_PENALTY_COUNT


async def admin_penalty_count(update: Update, context: CallbackContext) -> int:
    home, flow = await continue_admin_flow(update, context)
    if home is None:
        return ConversationHandler.END
    text = update.message.text.strip()
    if text.lower() == "cancel":
        return await cancel(update, context)
    name = flow.get("admin_penalty_member")
    try:
        count = int(text)
    except ValueError:
        await update.message.reply_text("Please enter a whole number (0 or above).")
        return ADMIN_PENALTY_COUNT
    if count < 0 or count > 1000:
        await update.message.reply_text("Please enter a number between 0 and 1000.")
        return ADMIN_PENALTY_COUNT

    def apply(h):
        penalties = h.setdefault("penalties", {})
        old = penalties.get(name, 0)
        if count == 0:
            penalties.pop(name, None)
        else:
            penalties[name] = count
        return old

    _, old = get_store().update(home["id"], apply)
    end_flow(context, update.effective_chat.id)
    await update.message.reply_text(
        f"Updated {name}: {old} → {count} {penalty_unit(home)}(s).", reply_markup=get_admin_keyboard()
    )
    return ConversationHandler.END


# ------------------------------------------------------------------ hoeck
async def admin_hoeck_start(update: Update, context: CallbackContext) -> int:
    home = await admin_home(update, context)
    if home is None:
        return ConversationHandler.END
    start_flow(context, update.effective_chat.id, home)
    current = home["settings"].get("wg_hoeck_date") or "not set"
    await update.message.reply_text(
        f"Current WG-Höck date: {current}\n\nEnter the new date (DD.MM.YYYY or YYYY-MM-DD), or 'clear':",
        reply_markup=get_cancel_keyboard(),
    )
    return ADMIN_HOECK_DATE


async def admin_hoeck_date(update: Update, context: CallbackContext) -> int:
    home, flow = await continue_admin_flow(update, context)
    if home is None:
        return ConversationHandler.END
    text = update.message.text.strip()
    if text.lower() == "cancel":
        return await cancel(update, context)
    if text.lower() == "clear":
        get_store().update(home["id"], lambda h: h["settings"].__setitem__("wg_hoeck_date", None))
        end_flow(context, update.effective_chat.id)
        await update.message.reply_text("WG-Höck date cleared.", reply_markup=get_admin_keyboard())
        return ConversationHandler.END
    parsed = None
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d.%m.%y", "%d/%m/%Y"):
        try:
            parsed = datetime.strptime(text, fmt).date()
            break
        except ValueError:
            continue
    if not parsed:
        await update.message.reply_text("Could not parse date. Please use DD.MM.YYYY or YYYY-MM-DD.")
        return ADMIN_HOECK_DATE

    get_store().update(home["id"], lambda h: h["settings"].__setitem__("wg_hoeck_date", parsed.isoformat()))
    end_flow(context, update.effective_chat.id)
    await update.message.reply_text(
        f"WG-Höck set to {parsed.strftime('%A, %d.%m.%Y')}.", reply_markup=get_admin_keyboard()
    )
    return ConversationHandler.END
