"""Edit-last-expense conversation (relocated verbatim)."""
import html

from telegram import KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.ext import CallbackContext, ConversationHandler

from wgcop.formatting import _format_expense_entry
from wgcop.identity import _resolve_member_for_user
from wgcop.keyboards import (
    EDIT_AMOUNT,
    EDIT_MENU,
    EDIT_PICK_MEMBER,
    EDIT_SPLIT,
    get_edit_choice_keyboard,
    get_main_keyboard,
)
from wgcop.members import _get_member_name, _match_member_name, _normalise_member_name
from wgcop.store import load_data, save_data

def _find_last_expense_for_payer(expenses, payer_name):
    target = _normalise_member_name(payer_name)
    for idx in range(len(expenses) - 1, -1, -1):
        entry = expenses[idx]
        if _normalise_member_name(entry.get("payer")) == target:
            return idx, entry
    return None, None


async def _initiate_edit_for_member(message, context, member_name, data=None):
    data = data or load_data()
    
    # member_name could be a dict object, extract the name if needed
    display_name = _get_member_name(member_name)
    
    expenses = data.get("expenses", []) or []
    idx, entry = _find_last_expense_for_payer(expenses, display_name)

    if entry is None:
        await message.reply_text(
            f"No expenses found for {display_name}.", reply_markup=get_main_keyboard()
        )
        return ConversationHandler.END

    context.user_data["edit_member"] = display_name
    context.user_data["edit_index"] = idx

    summary = _format_expense_entry(entry, display_name)
    prompt = (
        f"Last expense for {html.escape(display_name)}:\n\n"
        f"{summary}\n\nWhat do you want to edit?"
    )

    await message.reply_html(prompt, reply_markup=get_edit_choice_keyboard())
    return EDIT_MENU


async def start_edit_entries(update: Update, context: CallbackContext) -> int:
    data = load_data()
    members = data.get("members", []) or []

    if not members:
        await update.message.reply_text(
            "No members recorded yet.", reply_markup=get_main_keyboard()
        )
        return ConversationHandler.END

    detected = _resolve_member_for_user(update.effective_user)
    if detected:
        return await _initiate_edit_for_member(
            update.message, context, detected, data
        )

    member_names = [_get_member_name(m) for m in members]
    buttons = [[KeyboardButton(name)] for name in member_names]
    buttons.append([KeyboardButton("Cancel")])
    context.user_data["edit_member_selection"] = members

    await update.message.reply_text(
        "Whose expense would you like to adjust?",
        reply_markup=ReplyKeyboardMarkup(buttons, resize_keyboard=True),
    )
    return EDIT_PICK_MEMBER


async def edit_entries_pick_member(update: Update, context: CallbackContext) -> int:
    choice = update.message.text.strip()
    if choice.lower() in {"cancel", "back to main menu"}:
        context.user_data.pop("edit_member_selection", None)
        await update.message.reply_text(
            "Edit cancelled.", reply_markup=get_main_keyboard()
        )
        return ConversationHandler.END

    members = context.user_data.get("edit_member_selection")
    if not members:
        members = load_data().get("members", []) or []

    match = _match_member_name(members, choice)
    if not match:
        await update.message.reply_text(
            "Please choose a member from the list."
        )
        return EDIT_PICK_MEMBER

    context.user_data.pop("edit_member_selection", None)
    return await _initiate_edit_for_member(update.message, context, match)


async def edit_entries_menu(update: Update, context: CallbackContext) -> int:
    choice = update.message.text.strip().lower()

    if choice in {"cancel", "back to main menu"}:
        context.user_data.pop("edit_member", None)
        context.user_data.pop("edit_index", None)
        await update.message.reply_text(
            "Edit cancelled.", reply_markup=get_main_keyboard()
        )
        return ConversationHandler.END

    if choice == "amount":
        await update.message.reply_text(
            "Enter the corrected total (use a number).",
            reply_markup=ReplyKeyboardRemove(),
        )
        return EDIT_AMOUNT

    if choice.startswith("slitt"):
        data = load_data()
        members = data.get("members", []) or []
        member_list = ", ".join(members)
        await update.message.reply_text(
            "Send the updated split as comma-separated names."
            + (f"\nMembers: {member_list}" if member_list else ""),
            reply_markup=ReplyKeyboardRemove(),
        )
        return EDIT_SPLIT

    await update.message.reply_text("Please choose one of the options above.")
    return EDIT_MENU


async def edit_entries_amount(update: Update, context: CallbackContext) -> int:
    text = update.message.text.strip()
    if text.lower() in {"cancel", "back to main menu"}:
        await update.message.reply_text(
            "Edit cancelled.", reply_markup=get_main_keyboard()
        )
        context.user_data.pop("edit_member", None)
        context.user_data.pop("edit_index", None)
        return ConversationHandler.END

    try:
        amount = float(text.replace(",", "."))
    except ValueError:
        await update.message.reply_text(
            "Please provide a number (e.g. 12.50)."
        )
        return EDIT_AMOUNT

    if amount <= 0:
        await update.message.reply_text("Amount must be greater than zero.")
        return EDIT_AMOUNT

    idx = context.user_data.get("edit_index")
    member = context.user_data.get("edit_member")
    if idx is None or member is None:
        await update.message.reply_text(
            "No expense selected.", reply_markup=get_main_keyboard()
        )
        context.user_data.clear()
        return ConversationHandler.END

    data = load_data()
    expenses = data.get("expenses", []) or []
    if not (0 <= idx < len(expenses)):
        await update.message.reply_text(
            "Could not locate the expense entry.", reply_markup=get_main_keyboard()
        )
        context.user_data.clear()
        return ConversationHandler.END

    expenses[idx]["amount"] = round(amount, 2)
    save_data(data)

    updated_entry = expenses[idx]
    context.user_data.clear()
    await update.message.reply_html(
        "Amount updated.\n\n" + _format_expense_entry(updated_entry, member),
        reply_markup=get_main_keyboard(),
    )
    return ConversationHandler.END


async def edit_entries_split(update: Update, context: CallbackContext) -> int:
    text = update.message.text.strip()
    if text.lower() in {"cancel", "back to main menu"}:
        await update.message.reply_text(
            "Edit cancelled.", reply_markup=get_main_keyboard()
        )
        context.user_data.pop("edit_member", None)
        context.user_data.pop("edit_index", None)
        return ConversationHandler.END

    raw_names = [name.strip() for name in text.split(",") if name.strip()]
    if not raw_names:
        await update.message.reply_text(
            "Please provide at least one member name."
        )
        return EDIT_SPLIT

    data = load_data()
    members = data.get("members", []) or []
    resolved = []
    unknown = []

    for name in raw_names:
        match = _match_member_name(members, name)
        if not match:
            unknown.append(name)
            continue
        if match not in resolved:
            resolved.append(match)

    if unknown:
        await update.message.reply_text(
            "Unknown member(s): " + ", ".join(unknown)
        )
        return EDIT_SPLIT

    if not resolved:
        await update.message.reply_text("Split cannot be empty.")
        return EDIT_SPLIT

    idx = context.user_data.get("edit_index")
    member = context.user_data.get("edit_member")
    if idx is None or member is None:
        await update.message.reply_text(
            "No expense selected.", reply_markup=get_main_keyboard()
        )
        context.user_data.clear()
        return ConversationHandler.END

    expenses = data.get("expenses", []) or []
    if not (0 <= idx < len(expenses)):
        await update.message.reply_text(
            "Could not locate the expense entry.", reply_markup=get_main_keyboard()
        )
        context.user_data.clear()
        return ConversationHandler.END

    # Convert resolved member objects to names for storage
    resolved_names = [_get_member_name(m) for m in resolved]
    expenses[idx]["split_with"] = resolved_names
    save_data(data)

    updated_entry = expenses[idx]
    context.user_data.clear()
    await update.message.reply_html(
        "Split updated.\n\n" + _format_expense_entry(updated_entry, member),
        reply_markup=get_main_keyboard(),
    )
    return ConversationHandler.END
