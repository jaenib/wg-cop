"""Edit-last-expense conversation."""
import html
import math

from telegram import KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.ext import CallbackContext, ConversationHandler

from wgcop.formatting import format_expense_entry
from wgcop.handlers.common import cancel, continue_flow
from wgcop.homes import end_flow, home_currency, require_home, start_flow
from wgcop.keyboards import (
    BTN_EDIT_AMOUNT,
    BTN_EDIT_SPLITTERS,
    EDIT_AMOUNT,
    EDIT_MENU,
    EDIT_PICK_MEMBER,
    EDIT_SPLIT,
    get_edit_choice_keyboard,
    get_main_keyboard,
)
from wgcop.members import _match_member_name, _normalise_member_name, active_members
from wgcop.store import get_store, member_for_uid

CANCEL_WORDS = {"cancel", "back to main menu"}
MAX_AMOUNT = 1_000_000


def _find_last_expense_for_payer(expenses, payer_name):
    target = _normalise_member_name(payer_name)
    for idx in range(len(expenses) - 1, -1, -1):
        if _normalise_member_name(expenses[idx].get("payer")) == target:
            return idx, expenses[idx]
    return None, None


async def _initiate_edit_for_member(message, context, home, flow, display_name):
    idx, entry = _find_last_expense_for_payer(home.get("expenses", []) or [], display_name)
    if entry is None:
        end_flow(context, message.chat.id)
        await message.reply_text(f"No expenses found for {display_name}.", reply_markup=get_main_keyboard())
        return ConversationHandler.END
    flow["edit_member"] = display_name
    flow["edit_index"] = idx
    summary = format_expense_entry(entry, display_name, home_currency(home))
    await message.reply_html(
        f"Last expense for {html.escape(display_name)}:\n\n{summary}\n\nWhat do you want to edit?",
        reply_markup=get_edit_choice_keyboard(),
    )
    return EDIT_MENU


async def start_edit_entries(update: Update, context: CallbackContext) -> int:
    home = await require_home(update, context)
    if home is None:
        return ConversationHandler.END
    members = active_members(home)
    if not members:
        await update.message.reply_text("No members recorded yet.", reply_markup=get_main_keyboard())
        return ConversationHandler.END
    flow = start_flow(context, update.effective_chat.id, home)
    detected = member_for_uid(home, update.effective_user.id)
    if detected:
        return await _initiate_edit_for_member(update.message, context, home, flow, detected["name"])

    buttons = [[KeyboardButton(m["name"])] for m in members]
    buttons.append([KeyboardButton("Cancel")])
    await update.message.reply_text(
        "Whose expense would you like to adjust?", reply_markup=ReplyKeyboardMarkup(buttons, resize_keyboard=True)
    )
    return EDIT_PICK_MEMBER


async def edit_entries_pick_member(update: Update, context: CallbackContext) -> int:
    home, flow = await continue_flow(update, context)
    if home is None:
        return ConversationHandler.END
    choice = update.message.text.strip()
    if choice.lower() in CANCEL_WORDS:
        return await cancel(update, context)
    match = _match_member_name(active_members(home), choice)
    if not match:
        await update.message.reply_text("Please choose a member from the list.")
        return EDIT_PICK_MEMBER
    return await _initiate_edit_for_member(update.message, context, home, flow, match["name"])


async def edit_entries_menu(update: Update, context: CallbackContext) -> int:
    home, flow = await continue_flow(update, context)
    if home is None:
        return ConversationHandler.END
    choice = update.message.text.strip().lower()
    if choice in CANCEL_WORDS:
        return await cancel(update, context)
    if choice == BTN_EDIT_AMOUNT.lower():
        await update.message.reply_text("Enter the corrected total (use a number).", reply_markup=ReplyKeyboardRemove())
        return EDIT_AMOUNT
    if choice == BTN_EDIT_SPLITTERS.lower():
        member_list = ", ".join(m["name"] for m in active_members(home))
        await update.message.reply_text(
            "Send the updated split as comma-separated names." + (f"\nMembers: {member_list}" if member_list else ""),
            reply_markup=ReplyKeyboardRemove(),
        )
        return EDIT_SPLIT
    await update.message.reply_text("Please choose one of the options above.")
    return EDIT_MENU


def _locate(h, flow):
    idx = flow.get("edit_index")
    member = flow.get("edit_member")
    expenses = h.get("expenses", []) or []
    if idx is None or member is None or not (0 <= idx < len(expenses)):
        return None
    entry = expenses[idx]
    if _normalise_member_name(entry.get("payer")) != _normalise_member_name(member):
        return None  # list changed underneath us
    return entry


async def edit_entries_amount(update: Update, context: CallbackContext) -> int:
    home, flow = await continue_flow(update, context)
    if home is None:
        return ConversationHandler.END
    text = update.message.text.strip()
    if text.lower() in CANCEL_WORDS:
        return await cancel(update, context)
    try:
        amount = float(text.replace(",", "."))
    except ValueError:
        await update.message.reply_text("Please provide a number (e.g. 12.50).")
        return EDIT_AMOUNT
    amount = round(amount, 2) if math.isfinite(amount) else amount
    if not math.isfinite(amount) or amount <= 0 or amount > MAX_AMOUNT:
        await update.message.reply_text("Amount must be greater than zero (two decimals).")
        return EDIT_AMOUNT

    def apply(h):
        entry = _locate(h, flow)
        if entry is None:
            return None
        entry["amount"] = amount
        return dict(entry)

    _, updated = get_store().update(home["id"], apply)
    end_flow(context, update.effective_chat.id)
    if updated is None:
        await update.message.reply_text("Could not locate the expense entry.", reply_markup=get_main_keyboard())
        return ConversationHandler.END
    await update.message.reply_html(
        "Amount updated.\n\n" + format_expense_entry(updated, flow.get("edit_member"), home_currency(home)),
        reply_markup=get_main_keyboard(),
    )
    return ConversationHandler.END


async def edit_entries_split(update: Update, context: CallbackContext) -> int:
    home, flow = await continue_flow(update, context)
    if home is None:
        return ConversationHandler.END
    text = update.message.text.strip()
    if text.lower() in CANCEL_WORDS:
        return await cancel(update, context)
    raw_names = [name.strip() for name in text.split(",") if name.strip()]
    if not raw_names:
        await update.message.reply_text("Please provide at least one member name.")
        return EDIT_SPLIT

    resolved, unknown = [], []
    for name in raw_names:
        match = _match_member_name(active_members(home), name)
        if not match:
            unknown.append(name)
        elif match["name"] not in resolved:
            resolved.append(match["name"])
    if unknown:
        await update.message.reply_text("Unknown member(s): " + ", ".join(unknown))
        return EDIT_SPLIT

    def apply(h):
        entry = _locate(h, flow)
        if entry is None:
            return None
        entry["split_with"] = resolved
        return dict(entry)

    _, updated = get_store().update(home["id"], apply)
    end_flow(context, update.effective_chat.id)
    if updated is None:
        await update.message.reply_text("Could not locate the expense entry.", reply_markup=get_main_keyboard())
        return ConversationHandler.END
    await update.message.reply_html(
        "Split updated.\n\n" + format_expense_entry(updated, flow.get("edit_member"), home_currency(home)),
        reply_markup=get_main_keyboard(),
    )
    return ConversationHandler.END
