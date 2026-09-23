"""Expense conversation: manual entry, receipt scan, payer and split selection."""
import html
import logging
import os
import tempfile
from datetime import datetime

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.ext import CallbackContext, ConversationHandler

from wgcop.formatting import _format_currency, _format_signed_currency
from wgcop.handlers.common import cancel
from wgcop.keyboards import (
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
    get_main_keyboard,
)
from wgcop.members import _get_member_name, _normalise_member_name, calculate_balances
from wgcop.receipts import (
    ReceiptParsingError,
    _adjust_items_to_total,
    _receipt_shared_total,
    build_receipt_items_kb,
    build_receipt_items_text,
    extract_items_from_receipt,
)
from wgcop.store import load_data, save_data

logger = logging.getLogger(__name__)

# Expense flow
async def _prompt_for_payer(message, context: CallbackContext) -> int:
    data = load_data()
    if not data.get("members"):
        context.user_data.clear()
        await message.reply_text(
            "No members found. Please add members first.",
            reply_markup=get_main_keyboard(),
        )
        return ConversationHandler.END

    await message.reply_text("Who paid?", reply_markup=ReplyKeyboardRemove())
    await message.reply_html(
        "<b>Select payer:</b>",
        reply_markup=build_payer_inline_kb(data["members"]),
    )
    return EXPENSE_PAYER


async def start_expense(update: Update, context: CallbackContext) -> int:
    context.user_data.clear()
    context.user_data["mode"] = None
    keyboard = ReplyKeyboardMarkup(
        [
            [KeyboardButton("Manual Entry")],
            [KeyboardButton("Scan Receipt")],
            [KeyboardButton("Cancel")],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    await update.message.reply_text(
        "How would you like to add the expense?",
        reply_markup=keyboard,
    )
    return EXPENSE_MODE


async def expense_mode_selection(update: Update, context: CallbackContext) -> int:
    text = update.message.text.strip()
    lowered = text.lower()

    if lowered == "manual entry":
        context.user_data["mode"] = "manual"
        await update.message.reply_text(
            "Enter a short description for the expense (e.g., 'Groceries Migros'):",
            reply_markup=ReplyKeyboardRemove(),
        )
        return EXPENSE_DESCRIPTION

    if lowered.startswith("scan receipt"):
        context.user_data["mode"] = "receipt"
        await update.message.reply_text(
            "Send a photo of the receipt:",
            reply_markup=ReplyKeyboardMarkup([[KeyboardButton("Cancel")]], resize_keyboard=True),
        )
        return EXPENSE_RECEIPT

    if lowered == "cancel":
        return await cancel(update, context)

    context.user_data["mode"] = "manual"
    context.user_data["description"] = text
    await update.message.reply_text(
        "Enter the amount (e.g. 42.50):", reply_markup=ReplyKeyboardRemove()
    )
    return EXPENSE_AMOUNT


async def expense_description(update: Update, context: CallbackContext) -> int:
    desc = update.message.text.strip()
    if not desc:
        await update.message.reply_text("Please provide a non-empty description.")
        return EXPENSE_DESCRIPTION
    context.user_data["description"] = desc
    mode = context.user_data.get("mode", "manual")
    if mode == "receipt" and context.user_data.get("amount") is not None:
        return await _prompt_for_payer(update.message, context)

    await update.message.reply_text("Enter the amount (e.g. 42.50):")
    return EXPENSE_AMOUNT


async def expense_amount(update: Update, context: CallbackContext) -> int:
    try:
        context.user_data["amount"] = round(
            float(update.message.text.replace(",", ".")), 2
        )
    except ValueError:
        await update.message.reply_text("Invalid amount. Try again (e.g. 42.50).")
        return EXPENSE_AMOUNT

    context.user_data.setdefault("mode", "manual")
    return await _prompt_for_payer(update.message, context)


async def expense_receipt_photo(update: Update, context: CallbackContext) -> int:
    telegram_file = None
    mime_type = "image/jpeg"
    if update.message.photo:
        telegram_file = await update.message.photo[-1].get_file()
    elif update.message.document:
        telegram_file = await update.message.document.get_file()
        mime_type = update.message.document.mime_type or "image/jpeg"

    if not telegram_file:
        await update.message.reply_text(
            "Please send a photo or image of the receipt, or type Cancel to abort."
        )
        return EXPENSE_RECEIPT

    analysing_msg = await update.message.reply_text(
        "Received! Analysing receipt...", reply_markup=ReplyKeyboardRemove()
    )

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp:
            tmp_path = tmp.name
        await telegram_file.download_to_drive(tmp_path)

        try:
            result = extract_items_from_receipt(tmp_path, mime_type=mime_type)
        except ReceiptParsingError as exc:
            logger.info("Receipt OCR failed: %s", exc)
            await analysing_msg.delete()
            context.user_data["mode"] = "manual"
            await update.message.reply_text(
                "Scan failed — switching to manual entry.\n"
                "Enter a short description (e.g. 'Groceries Migros'):",
                reply_markup=ReplyKeyboardRemove(),
            )
            return EXPENSE_DESCRIPTION

    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)

    items = result.get("items", []) if isinstance(result, dict) else result
    if not items:
        await analysing_msg.delete()
        context.user_data["mode"] = "manual"
        await update.message.reply_text(
            "No items found — switching to manual entry.\n"
            "Enter a short description (e.g. 'Groceries Migros'):",
            reply_markup=ReplyKeyboardRemove(),
        )
        return EXPENSE_DESCRIPTION

    receipt_total = result.get("receipt_total") if isinstance(result, dict) else None
    items_sum = result.get("items_sum") if isinstance(result, dict) else None
    if items_sum is None:
        items_sum = round(sum(item["amount"] for item in items), 2)
    gap = result.get("gap") if isinstance(result, dict) else None
    if gap is None and receipt_total is not None:
        gap = abs(items_sum - receipt_total)
    has_mismatch = receipt_total is not None and gap is not None and gap > 0.02

    context.user_data["mode"] = "receipt"
    context.user_data["receipt_items"] = items
    context.user_data["receipt_total"] = receipt_total
    if isinstance(result, dict) and result.get("description"):
        context.user_data["receipt_description"] = result["description"]

    item_lines = "\n".join(
        f"  {item['name']} — CHF {item['amount']:.2f}" for item in items
    )
    if has_mismatch:
        effective = receipt_total
        summary = (
            f"Found {len(items)} items (sum: CHF {items_sum:.2f} | receipt total: CHF {receipt_total:.2f}):\n"
            f"{item_lines}\n\n"
            f"Gap of CHF {gap:.2f} will be spread proportionally. Accept this parse?"
        )
    else:
        effective = receipt_total if receipt_total else items_sum
        summary = (
            f"Found {len(items)} items (total: CHF {effective:.2f}):\n"
            f"{item_lines}\n\n"
            "Accept this parse?"
        )

    confirm_kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Yes, continue", callback_data=CB_RECEIPT_ACCEPT),
            InlineKeyboardButton("No, manual entry", callback_data=CB_RECEIPT_MANUAL),
        ]
    ])
    await analysing_msg.delete()
    await update.message.reply_text(
        summary,
        reply_markup=confirm_kb,
    )
    return EXPENSE_RECEIPT_REVIEW


async def expense_receipt_invalid(update: Update, context: CallbackContext) -> int:
    await update.message.reply_text(
        "Please send a photo or image of the receipt, or type Cancel to abort."
    )
    return EXPENSE_RECEIPT


async def receipt_items_cb(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    await query.answer()

    items = context.user_data.get("receipt_items", [])
    if not items:
        await query.edit_message_text("No items to review. Please send the receipt again.")
        return EXPENSE_RECEIPT

    selected = context.user_data.get(
        "receipt_selected", set(range(len(items)))
    )
    if not isinstance(selected, set):
        selected = set(selected)

    if query.data == CB_RECEIPT_CANCEL:
        context.user_data.clear()
        await query.edit_message_text("Receipt-based expense cancelled.")
        await query.message.reply_text(
            "Cancelled. Back to main menu.", reply_markup=get_main_keyboard()
        )
        return ConversationHandler.END

    if query.data == CB_RECEIPT_MANUAL:
        context.user_data.clear()
        context.user_data["mode"] = "manual"
        await query.edit_message_text("Switching to manual entry.")
        await query.message.reply_text(
            "Enter a short description for the expense (e.g., 'Groceries Migros'):",
            reply_markup=ReplyKeyboardRemove(),
        )
        return EXPENSE_DESCRIPTION

    if query.data == CB_RECEIPT_ACCEPT:
        receipt_total = context.user_data.get("receipt_total")
        if receipt_total is not None:
            items = _adjust_items_to_total(items, receipt_total)
            context.user_data["receipt_items"] = items
        selected = set(range(len(items)))
        context.user_data["receipt_selected"] = selected
        context.user_data["amount"] = _receipt_shared_total(items, selected)
        await query.edit_message_text(
            build_receipt_items_text(items, selected),
            reply_markup=build_receipt_items_kb(items, selected),
        )
        return EXPENSE_RECEIPT_REVIEW

    if query.data == CB_RECEIPT_DONE:
        if not selected:
            await query.answer("Select at least one item.", show_alert=True)
            return EXPENSE_RECEIPT_REVIEW

        chosen = [items[i] for i in sorted(selected)]
        total = _receipt_shared_total(items, selected)
        context.user_data["selected_items"] = chosen
        context.user_data["amount"] = total

        await query.edit_message_text(f"Shared total: CHF {total:.2f}")

        auto_desc = context.user_data.get("receipt_description", "")
        if auto_desc:
            desc_kb = ReplyKeyboardMarkup(
                [[auto_desc]], one_time_keyboard=True, resize_keyboard=True
            )
            await query.message.reply_text(
                f"Suggested description: <b>{html.escape(auto_desc)}</b>\nTap to use it or type your own:",
                parse_mode="HTML",
                reply_markup=desc_kb,
            )
        else:
            await query.message.reply_text(
                "Enter a short description:", reply_markup=ReplyKeyboardRemove()
            )
        return EXPENSE_DESCRIPTION

    if query.data.startswith(CB_RECEIPT_TOGGLE_PREFIX):
        try:
            idx = int(query.data[len(CB_RECEIPT_TOGGLE_PREFIX) :])
        except ValueError:
            logger.warning("Invalid receipt toggle index: %s", query.data)
            return EXPENSE_RECEIPT_REVIEW

        if 0 <= idx < len(items):
            if idx in selected:
                selected.remove(idx)
            else:
                selected.add(idx)
            context.user_data["receipt_selected"] = selected

        await query.edit_message_text(
            build_receipt_items_text(items, selected),
            reply_markup=build_receipt_items_kb(items, selected),
        )
        return EXPENSE_RECEIPT_REVIEW

    return EXPENSE_RECEIPT_REVIEW


async def expense_payer_cb(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    await query.answer()
    data = load_data()
    if query.data.startswith(CB_PAYER_PREFIX):
        payer = query.data[len(CB_PAYER_PREFIX) :]
        context.user_data["payer"] = payer
        context.user_data["split_with"] = set()
        await query.edit_message_text(
            "Select who shares the expense (toggle). Then press Done."
        )
        await query.message.reply_text(
            "Split with:",
            reply_markup=build_split_inline_kb(
                data["members"], context.user_data["split_with"]
            ),
        )
        return EXPENSE_SPLIT
    return EXPENSE_PAYER


async def expense_split_cb(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    await query.answer()
    data = load_data()

    if query.data == CB_SPLIT_BACK:
        await query.edit_message_text("Who paid?")
        await query.message.reply_text(
            "Select payer:", reply_markup=build_payer_inline_kb(data["members"])
        )
        return EXPENSE_PAYER

    if query.data == CB_SPLIT_CANCEL:
        await query.edit_message_text("Expense entry cancelled.")
        await query.message.reply_text(
            "Cancelled. Back to main menu.", reply_markup=get_main_keyboard()
        )
        return ConversationHandler.END

    if query.data == CB_SPLIT_DONE:
        selected = sorted(context.user_data.get("split_with", []))
        if not selected:
            await query.answer("Select at least one person.", show_alert=True)
            return EXPENSE_SPLIT

        amount = context.user_data["amount"]
        payer = context.user_data["payer"]
        desc = context.user_data["description"]
        today = datetime.now().strftime("%Y-%m-%d")

        db = load_data()
        entry = {
            "date": today,
            "description": desc,
            "amount": amount,
            "payer": payer,
            "split_with": selected,
        }
        selected_items = context.user_data.get("selected_items")
        if selected_items:
            entry["items"] = selected_items
        db["expenses"].append(entry)
        save_data(db)

        payer_norm = _normalise_member_name(payer)
        share_count = len(selected)
        share = amount / share_count if share_count else 0.0
        owed_total = share * sum(
            1 for name in selected if _normalise_member_name(name) != payer_norm
        )

        members = db.get("members", []) or []
        balances = calculate_balances(db)
        payer_balance = balances.get(payer)

        confirmation_lines = [
            "<b>Expense saved</b>",
            f"Date: {html.escape(today)}",
            f"Description: {html.escape(desc)}",
            f"Total: {_format_currency(amount)}",
            f"Payer: {html.escape(payer)}",
            f"Split with: {', '.join(html.escape(name) for name in selected)}",
        ]

        if owed_total > 0:
            confirmation_lines.append(
                f"{html.escape(payer)} receives {_format_currency(owed_total)} back."
            )

        if payer_balance is not None:
            confirmation_lines.append(
                f"Balance for {html.escape(payer)}: {_format_signed_currency(payer_balance)}"
            )

        await query.edit_message_text("\n".join(confirmation_lines), parse_mode="HTML")
        await query.message.reply_text(
            "Entry stored.", reply_markup=get_main_keyboard()
        )
        context.user_data.clear()
        return ConversationHandler.END

    if query.data == CB_SPLIT_ALL:
        all_names = {_get_member_name(m) for m in data["members"]}
        sel = context.user_data.get("split_with", set())
        if all_names and all(
            _normalise_member_name(n) in {_normalise_member_name(s) for s in sel}
            for n in all_names
        ):
            sel = set()
        else:
            sel = set(all_names)
        context.user_data["split_with"] = sel
        await query.edit_message_reply_markup(
            reply_markup=build_split_inline_kb(data["members"], sel)
        )
        return EXPENSE_SPLIT

    if query.data.startswith(CB_SPLIT_TOGGLE_PREFIX):
        member = query.data[len(CB_SPLIT_TOGGLE_PREFIX) :]
        sel = context.user_data.get("split_with", set())
        if member in sel:
            sel.remove(member)
        else:
            sel.add(member)
        context.user_data["split_with"] = sel
        await query.edit_message_reply_markup(
            reply_markup=build_split_inline_kb(data["members"], sel)
        )
        return EXPENSE_SPLIT

    return EXPENSE_SPLIT
