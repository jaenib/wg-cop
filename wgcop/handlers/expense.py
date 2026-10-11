"""Expense conversation: manual entry, receipt scan, payer and split selection.

Draft state lives in the per-chat flow (see wgcop.homes). Inline keyboards are
bound to the draft through the message id, so another member tapping your
keyboard in a group gets a polite refusal instead of editing your draft.
"""
import asyncio
import html
import logging
import math
import os
import tempfile
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.ext import CallbackContext, ConversationHandler

from wgcop.formatting import format_currency, format_signed_currency
from wgcop.handlers.common import answers_callback, cancel, continue_flow
from wgcop.homes import end_flow, home_currency, home_tz, owns_keyboard, require_home, start_flow
from wgcop.keyboards import (
    BTN_CANCEL,
    BTN_MANUAL_ENTRY,
    BTN_SCAN_RECEIPT,
    CB_PAYER_PREFIX,
    CB_RECEIPT_ACCEPT,
    CB_RECEIPT_CANCEL,
    CB_RECEIPT_DONE,
    CB_RECEIPT_MANUAL,
    CB_RECEIPT_TOGGLE_PREFIX,
    CB_SPLIT_ALL,
    CB_SPLIT_BACK,
    CB_SPLIT_CANCEL,
    CB_SPLIT_DONE,
    CB_SPLIT_TOGGLE_PREFIX,
    EXPENSE_AMOUNT,
    EXPENSE_DESCRIPTION,
    EXPENSE_MODE,
    EXPENSE_PAYER,
    EXPENSE_RECEIPT,
    EXPENSE_RECEIPT_REVIEW,
    EXPENSE_SPLIT,
    build_payer_inline_kb,
    build_split_inline_kb,
    get_cancel_keyboard,
    get_expense_mode_keyboard,
    get_main_keyboard,
)
from wgcop.members import calculate_balances
from wgcop.receipts import (
    ReceiptParsingError,
    _adjust_items_to_total,
    _receipt_shared_total,
    build_receipt_items_kb,
    build_receipt_items_text,
    extract_items_from_receipt,
)
from wgcop.store import get_store, member_by_mid

logger = logging.getLogger(__name__)

MAX_AMOUNT = 1_000_000


def _parse_amount(text: str):
    try:
        amount = round(float(text.strip().replace(",", ".")), 2)
    except ValueError:
        return None
    if not math.isfinite(amount) or amount <= 0 or amount > MAX_AMOUNT:
        return None
    return amount


async def _refuse_foreign_keyboard(ack, state):
    await ack("That keyboard belongs to someone else's draft.", show_alert=True)
    return state


# ---------------------------------------------------------------- entry
async def start_expense(update: Update, context: CallbackContext) -> int:
    home = await require_home(update, context)
    if home is None:
        return ConversationHandler.END
    if not home.get("members"):
        await update.message.reply_text("No members found. Please add members first.", reply_markup=get_main_keyboard())
        return ConversationHandler.END
    flow = start_flow(context, update.effective_chat.id, home)
    flow["mode"] = None
    await update.message.reply_text("How would you like to add the expense?", reply_markup=get_expense_mode_keyboard())
    return EXPENSE_MODE


async def expense_mode_selection(update: Update, context: CallbackContext) -> int:
    home, flow = await continue_flow(update, context)
    if home is None:
        return ConversationHandler.END
    text = update.message.text.strip()
    lowered = text.lower()

    if lowered == BTN_MANUAL_ENTRY.lower():
        flow["mode"] = "manual"
        await update.message.reply_text(
            "Enter a short description for the expense (e.g., 'Groceries Migros'):",
            reply_markup=ReplyKeyboardRemove(),
        )
        return EXPENSE_DESCRIPTION

    if lowered.startswith(BTN_SCAN_RECEIPT.lower()):
        flow["mode"] = "receipt"
        await update.message.reply_text("Send a photo of the receipt:", reply_markup=get_cancel_keyboard())
        return EXPENSE_RECEIPT

    if lowered == BTN_CANCEL.lower():
        return await cancel(update, context)

    flow["mode"] = "manual"
    flow["description"] = text
    await update.message.reply_text("Enter the amount (e.g. 42.50):", reply_markup=ReplyKeyboardRemove())
    return EXPENSE_AMOUNT


async def expense_description(update: Update, context: CallbackContext) -> int:
    home, flow = await continue_flow(update, context)
    if home is None:
        return ConversationHandler.END
    desc = update.message.text.strip()
    if not desc:
        await update.message.reply_text("Please provide a non-empty description.")
        return EXPENSE_DESCRIPTION
    flow["description"] = desc[:200]
    if flow.get("mode") == "receipt" and flow.get("amount") is not None:
        return await _prompt_for_payer(update.message, home, flow)
    await update.message.reply_text("Enter the amount (e.g. 42.50):")
    return EXPENSE_AMOUNT


async def expense_amount(update: Update, context: CallbackContext) -> int:
    home, flow = await continue_flow(update, context)
    if home is None:
        return ConversationHandler.END
    amount = _parse_amount(update.message.text)
    if amount is None:
        await update.message.reply_text("Invalid amount. Try again (e.g. 42.50).")
        return EXPENSE_AMOUNT
    flow["amount"] = amount
    flow.setdefault("mode", "manual")
    return await _prompt_for_payer(update.message, home, flow)


async def _prompt_for_payer(message, home, flow) -> int:
    await message.reply_text("Who paid?", reply_markup=ReplyKeyboardRemove())
    sent = await message.reply_html("<b>Select payer:</b>", reply_markup=build_payer_inline_kb(home["members"]))
    flow["kb_msg"] = getattr(sent, "message_id", None)
    return EXPENSE_PAYER


# --------------------------------------------------------------- receipts
async def expense_receipt_photo(update: Update, context: CallbackContext) -> int:
    home, flow = await continue_flow(update, context)
    if home is None:
        return ConversationHandler.END
    telegram_file = None
    mime_type = "image/jpeg"
    if update.message.photo:
        telegram_file = await update.message.photo[-1].get_file()
    elif update.message.document:
        telegram_file = await update.message.document.get_file()
        mime_type = update.message.document.mime_type or "image/jpeg"

    if not telegram_file:
        await update.message.reply_text("Please send a photo or image of the receipt, or type Cancel to abort.")
        return EXPENSE_RECEIPT

    analysing_msg = await update.message.reply_text("Received! Analysing receipt...", reply_markup=ReplyKeyboardRemove())

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp:
            tmp_path = tmp.name
        await telegram_file.download_to_drive(tmp_path)
        try:
            # The vision call is synchronous; keep the event loop (and every other
            # home's jobs) responsive while it runs.
            result = await asyncio.to_thread(extract_items_from_receipt, tmp_path, mime_type=mime_type)
        except ReceiptParsingError as exc:
            logger.info("Receipt OCR failed: %s", exc)
            await analysing_msg.delete()
            flow["mode"] = "manual"
            await update.message.reply_text(
                "Scan failed — switching to manual entry.\nEnter a short description (e.g. 'Groceries Migros'):",
                reply_markup=ReplyKeyboardRemove(),
            )
            return EXPENSE_DESCRIPTION
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)

    items = result.get("items", []) if isinstance(result, dict) else result
    if not items:
        await analysing_msg.delete()
        flow["mode"] = "manual"
        await update.message.reply_text(
            "No items found — switching to manual entry.\nEnter a short description (e.g. 'Groceries Migros'):",
            reply_markup=ReplyKeyboardRemove(),
        )
        return EXPENSE_DESCRIPTION

    currency = home_currency(home)
    receipt_total = result.get("receipt_total") if isinstance(result, dict) else None
    items_sum = result.get("items_sum") if isinstance(result, dict) else None
    if items_sum is None:
        items_sum = round(sum(item["amount"] for item in items), 2)
    gap = result.get("gap") if isinstance(result, dict) else None
    if gap is None and receipt_total is not None:
        gap = abs(items_sum - receipt_total)
    has_mismatch = receipt_total is not None and gap is not None and gap > 0.02

    flow["mode"] = "receipt"
    flow["receipt_items"] = items
    flow["receipt_total"] = receipt_total
    if isinstance(result, dict) and result.get("description"):
        flow["receipt_description"] = result["description"]

    item_lines = "\n".join(f"  {item['name']} — {currency} {item['amount']:.2f}" for item in items)
    if has_mismatch:
        summary = (
            f"Found {len(items)} items (sum: {currency} {items_sum:.2f} | receipt total: {currency} {receipt_total:.2f}):\n"
            f"{item_lines}\n\nGap of {currency} {gap:.2f} will be spread proportionally. Accept this parse?"
        )
    else:
        effective = receipt_total if receipt_total else items_sum
        summary = f"Found {len(items)} items (total: {currency} {effective:.2f}):\n{item_lines}\n\nAccept this parse?"

    confirm_kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Yes, continue", callback_data=CB_RECEIPT_ACCEPT),
                InlineKeyboardButton("No, manual entry", callback_data=CB_RECEIPT_MANUAL),
            ]
        ]
    )
    await analysing_msg.delete()
    sent = await update.message.reply_text(summary, reply_markup=confirm_kb)
    flow["kb_msg"] = getattr(sent, "message_id", None)
    return EXPENSE_RECEIPT_REVIEW


async def expense_receipt_invalid(update: Update, context: CallbackContext) -> int:
    await update.message.reply_text("Please send a photo or image of the receipt, or type Cancel to abort.")
    return EXPENSE_RECEIPT


@answers_callback
async def receipt_items_cb(update: Update, context: CallbackContext, ack) -> int:
    query = update.callback_query
    home, flow = await continue_flow(update, context)
    if home is None:
        return ConversationHandler.END
    if not owns_keyboard(query, flow):
        return await _refuse_foreign_keyboard(ack, EXPENSE_RECEIPT_REVIEW)

    items = flow.get("receipt_items", [])
    if not items:
        await query.edit_message_text("No items to review. Please send the receipt again.")
        return EXPENSE_RECEIPT
    stored = flow.get("receipt_selected")
    selected = set(range(len(items))) if stored is None else set(stored)  # [] = everything untoggled
    currency = home_currency(home)

    if query.data == CB_RECEIPT_CANCEL:
        end_flow(context, update.effective_chat.id)
        await query.edit_message_text("Receipt-based expense cancelled.")
        await query.message.reply_text("Cancelled. Back to main menu.", reply_markup=get_main_keyboard())
        return ConversationHandler.END

    if query.data == CB_RECEIPT_MANUAL:
        for key in ("receipt_items", "receipt_total", "receipt_description", "receipt_selected", "selected_items", "amount"):
            flow.pop(key, None)
        flow["mode"] = "manual"
        await query.edit_message_text("Switching to manual entry.")
        await query.message.reply_text(
            "Enter a short description for the expense (e.g., 'Groceries Migros'):", reply_markup=ReplyKeyboardRemove()
        )
        return EXPENSE_DESCRIPTION

    if query.data == CB_RECEIPT_ACCEPT:
        receipt_total = flow.get("receipt_total")
        if receipt_total is not None:
            items = _adjust_items_to_total(items, receipt_total)
            flow["receipt_items"] = items
        selected = set(range(len(items)))
        flow["receipt_selected"] = sorted(selected)
        flow["amount"] = _receipt_shared_total(items, selected)
        await query.edit_message_text(
            build_receipt_items_text(items, selected, currency), reply_markup=build_receipt_items_kb(items, selected)
        )
        return EXPENSE_RECEIPT_REVIEW

    if query.data == CB_RECEIPT_DONE:
        if not selected:
            await ack("Select at least one item.", show_alert=True)
            return EXPENSE_RECEIPT_REVIEW
        total = _receipt_shared_total(items, selected)
        if not math.isfinite(total) or not 0 < total <= MAX_AMOUNT:
            # Discount lines are kept on purpose, so a selection can add up to nothing.
            await ack(f"The shared total must be more than zero (now {total:.2f}).", show_alert=True)
            return EXPENSE_RECEIPT_REVIEW
        chosen = [items[i] for i in sorted(selected)]
        flow["selected_items"] = chosen
        flow["amount"] = total
        await query.edit_message_text(f"Shared total: {currency} {total:.2f}")
        auto_desc = flow.get("receipt_description", "")
        if auto_desc:
            desc_kb = ReplyKeyboardMarkup([[auto_desc]], one_time_keyboard=True, resize_keyboard=True)
            await query.message.reply_text(
                f"Suggested description: <b>{html.escape(auto_desc)}</b>\nTap to use it or type your own:",
                parse_mode="HTML",
                reply_markup=desc_kb,
            )
        else:
            await query.message.reply_text("Enter a short description:", reply_markup=ReplyKeyboardRemove())
        return EXPENSE_DESCRIPTION

    if query.data.startswith(CB_RECEIPT_TOGGLE_PREFIX):
        try:
            idx = int(query.data[len(CB_RECEIPT_TOGGLE_PREFIX):])
        except ValueError:
            logger.warning("Invalid receipt toggle index: %s", query.data)
            return EXPENSE_RECEIPT_REVIEW
        if 0 <= idx < len(items):
            selected.symmetric_difference_update({idx})
            flow["receipt_selected"] = sorted(selected)
        await query.edit_message_text(
            build_receipt_items_text(items, selected, currency), reply_markup=build_receipt_items_kb(items, selected)
        )
        return EXPENSE_RECEIPT_REVIEW

    return EXPENSE_RECEIPT_REVIEW


# ------------------------------------------------------------ payer/split
@answers_callback
async def expense_payer_cb(update: Update, context: CallbackContext, ack) -> int:
    query = update.callback_query
    home, flow = await continue_flow(update, context)
    if home is None:
        return ConversationHandler.END
    if not owns_keyboard(query, flow):
        return await _refuse_foreign_keyboard(ack, EXPENSE_PAYER)
    if not query.data.startswith(CB_PAYER_PREFIX):
        return EXPENSE_PAYER
    member = member_by_mid(home, query.data[len(CB_PAYER_PREFIX):])
    if member is None:
        await ack("That member no longer exists.", show_alert=True)
        return EXPENSE_PAYER
    flow["payer_mid"] = member["mid"]
    flow["payer"] = member["name"]
    flow["split"] = []
    await query.edit_message_text("Select who shares the expense (toggle). Then press Done.")
    sent = await query.message.reply_text("Split with:", reply_markup=build_split_inline_kb(home["members"], []))
    flow["kb_msg"] = getattr(sent, "message_id", None)
    return EXPENSE_SPLIT


@answers_callback
async def expense_split_cb(update: Update, context: CallbackContext, ack) -> int:
    query = update.callback_query
    home, flow = await continue_flow(update, context)
    if home is None:
        return ConversationHandler.END
    if not owns_keyboard(query, flow):
        return await _refuse_foreign_keyboard(ack, EXPENSE_SPLIT)
    chat_id = update.effective_chat.id
    members = home["members"]
    selected = list(flow.get("split") or [])

    if query.data == CB_SPLIT_BACK:
        await query.edit_message_text("Who paid?")
        sent = await query.message.reply_text("Select payer:", reply_markup=build_payer_inline_kb(members))
        flow["kb_msg"] = getattr(sent, "message_id", None)
        return EXPENSE_PAYER

    if query.data == CB_SPLIT_CANCEL:
        end_flow(context, chat_id)
        await query.edit_message_text("Expense entry cancelled.")
        await query.message.reply_text("Cancelled. Back to main menu.", reply_markup=get_main_keyboard())
        return ConversationHandler.END

    if query.data == CB_SPLIT_ALL:
        all_mids = [m["mid"] for m in members]
        selected = [] if set(all_mids) <= set(selected) else all_mids
        flow["split"] = selected
        await query.edit_message_reply_markup(reply_markup=build_split_inline_kb(members, selected))
        return EXPENSE_SPLIT

    if query.data.startswith(CB_SPLIT_TOGGLE_PREFIX):
        mid = query.data[len(CB_SPLIT_TOGGLE_PREFIX):]
        if member_by_mid(home, mid) is not None:
            if mid in selected:
                selected.remove(mid)
            else:
                selected.append(mid)
            flow["split"] = selected
        await query.edit_message_reply_markup(reply_markup=build_split_inline_kb(members, selected))
        return EXPENSE_SPLIT

    if query.data == CB_SPLIT_DONE:
        names = [m["name"] for mid in selected for m in [member_by_mid(home, mid)] if m is not None]
        if not names:
            await ack("Select at least one person.", show_alert=True)
            return EXPENSE_SPLIT
        amount = flow.get("amount")
        payer = flow.get("payer")
        desc = flow.get("description") or "(no description)"
        if amount is None or payer is None or not math.isfinite(amount) or not 0 < amount <= MAX_AMOUNT:
            end_flow(context, chat_id)
            await query.edit_message_text("The draft is incomplete. Please start again.")
            await query.message.reply_text("Back to main menu.", reply_markup=get_main_keyboard())
            return ConversationHandler.END
        today = datetime.now(home_tz(home)).strftime("%Y-%m-%d")
        entry = {"date": today, "description": desc, "amount": amount, "payer": payer, "split_with": names}
        if flow.get("selected_items"):
            entry["items"] = flow["selected_items"]

        def apply(h):
            h["expenses"].append(dict(entry))
            return calculate_balances(h).get(payer)

        _, payer_balance = get_store().update(home["id"], apply)
        end_flow(context, chat_id)  # saved: clear the draft before any further await
        currency = home_currency(home)
        share = amount / len(names)
        owed_total = share * sum(1 for n in names if n != payer)
        lines = [
            "<b>Expense saved</b>",
            f"Date: {html.escape(today)}",
            f"Description: {html.escape(desc)}",
            f"Total: {format_currency(amount, currency)}",
            f"Payer: {html.escape(payer)}",
            f"Split with: {', '.join(html.escape(n) for n in names)}",
        ]
        if owed_total > 0:
            lines.append(f"{html.escape(payer)} receives {format_currency(owed_total, currency)} back.")
        if payer_balance is not None:
            lines.append(f"Balance for {html.escape(payer)}: {format_signed_currency(payer_balance, currency)}")
        await query.edit_message_text("\n".join(lines), parse_mode="HTML")
        await query.message.reply_text("Entry stored.", reply_markup=get_main_keyboard())
        return ConversationHandler.END

    return EXPENSE_SPLIT
