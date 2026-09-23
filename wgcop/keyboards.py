"""Reply/inline keyboards, callback constants and conversation states (relocated verbatim)."""
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from wgcop.members import _get_member_name, _get_member_status, _normalise_member_name

# Callback data prefixes
CB_PAYER_PREFIX = "payer:"
CB_SPLIT_TOGGLE_PREFIX = "split_toggle:"
CB_SPLIT_ALL = "split_all"
CB_SPLIT_DONE = "split_done"
CB_SPLIT_BACK = "split_back"
CB_SPLIT_CANCEL = "split_cancel"
CB_RECEIPT_TOGGLE_PREFIX = "receipt_toggle:"
CB_RECEIPT_DONE = "receipt_done"
CB_RECEIPT_CANCEL = "receipt_cancel"
CB_RECEIPT_ACCEPT = "receipt_accept"
CB_RECEIPT_MANUAL = "receipt_manual"

# States for conversation handler
(
    EXPENSE_MODE,
    EXPENSE_DESCRIPTION,
    EXPENSE_AMOUNT,
    EXPENSE_PAYER,
    EXPENSE_SPLIT,
    EXPENSE_RECEIPT,
    EXPENSE_RECEIPT_REVIEW,
) = range(7)
CHORE_USER, CHORE_MINUTES, CHORE_DESCRIPTION = range(3)
MANAGE_MEMBER = range(1)
EDIT_PICK_MEMBER, EDIT_MENU, EDIT_AMOUNT, EDIT_SPLIT = range(4)
REDEEM_MEMBER, REDEEM_COUNT = range(2)
ADMIN_BEER_MEMBER, ADMIN_BEER_COUNT = range(2)
ADMIN_HOECK_DATE = 0  # single-state conv
ADMIN_REPORT_TIME = 0  # single-state conv
CHANGE_USERNAME_NEW = 0  # single-state conv

# Dynamic Keyboards
def get_main_keyboard():
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("Add Expense"), KeyboardButton("Add Chore")],
            [KeyboardButton("List Expenses"), KeyboardButton("List Chores")],
            [KeyboardButton("Standings"), KeyboardButton("Penalties")],
            [KeyboardButton("Settings")],
        ],
        resize_keyboard=True,
    )


def get_member_keyboard(data):
    members = data.get("members", [])
    if not members:
        return None
    buttons = [[KeyboardButton(_get_member_name(member))] for member in members]
    buttons.append([KeyboardButton("Done")])
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)


def get_settings_keyboard(is_admin=False, username=None):
    label = f"Manage {username}" if username else "Manage Profile"
    buttons = [[KeyboardButton(label)]]
    if is_admin:
        buttons.append([KeyboardButton("Admin Panel")])
    buttons.append([KeyboardButton("Back to Main Menu")])
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)


def get_admin_keyboard():
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("Member Management")],
            [KeyboardButton("Manage Weekly Report")],
            [KeyboardButton("Adjust Beer Count")],
            [KeyboardButton("Set WG-Höck")],
            [KeyboardButton("System Overview")],
            [KeyboardButton("Back to Settings")],
        ],
        resize_keyboard=True,
    )


def get_weekly_report_keyboard():
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("Set Weekly Report")],
            [KeyboardButton("Set Report Time")],
            [KeyboardButton("Trigger Weekly Report")],
            [KeyboardButton("Back to Admin")],
        ],
        resize_keyboard=True,
    )


def get_manage_self_keyboard():
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("Set Vacation Status")],
            [KeyboardButton("Edit Entries")],
            [KeyboardButton("Change Username")],
            [KeyboardButton("Back to Settings")],
        ],
        resize_keyboard=True,
    )


def get_penalties_keyboard():
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("Check Beer Owed")],
            [KeyboardButton("Redeem Beer")],
            [KeyboardButton("Back to Main Menu")],
        ],
        resize_keyboard=True,
    )


def get_edit_choice_keyboard():
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("Amount")],
            [KeyboardButton("Splitters")],
            [KeyboardButton("Cancel")],
        ],
        resize_keyboard=True,
    )


def _truncate_button_label(label: str, limit: int = 60) -> str:
    if len(label) <= limit:
        return label
    return label[: limit - 1] + "…"


def build_payer_inline_kb(members):
    rows = []
    for m in members:
        name = _get_member_name(m)
        rows.append([InlineKeyboardButton(name, callback_data=f"{CB_PAYER_PREFIX}{name}")])
    return InlineKeyboardMarkup(rows)


def build_split_inline_kb(members, selected):
    # Sort members: active first, then vacating (italic, at the end)
    active_members = []
    vacating_members = []
    
    for m in members:
        name = _get_member_name(m)
        status = _get_member_status(m)
        if status == "vacating":
            vacating_members.append((name, m))
        else:
            active_members.append((name, m))
    
    rows = []

    # Add active members first
    for name, m in active_members:
        picked = _normalise_member_name(m) in {_normalise_member_name(s) for s in selected}
        label = f"{name}{' ✅' if picked else ''}"
        rows.append(
            [InlineKeyboardButton(label, callback_data=f"{CB_SPLIT_TOGGLE_PREFIX}{name}")]
        )

    # Add vacating members (reminder not to forget them for long-term only)
    for name, m in vacating_members:
        picked = _normalise_member_name(m) in {_normalise_member_name(s) for s in selected}
        label = f"{name} (vacating){' ✅' if picked else ''}"
        rows.append(
            [InlineKeyboardButton(label, callback_data=f"{CB_SPLIT_TOGGLE_PREFIX}{name}")]
        )

    all_names = [name for name, _ in active_members + vacating_members]
    all_selected = all_names and all(
        _normalise_member_name(n) in {_normalise_member_name(s) for s in selected}
        for n in all_names
    )
    rows.append(
        [InlineKeyboardButton(f"All{' ✅' if all_selected else ''}", callback_data=CB_SPLIT_ALL)]
    )

    rows.append(
        [
            InlineKeyboardButton("Back", callback_data=CB_SPLIT_BACK),
            InlineKeyboardButton("Done", callback_data=CB_SPLIT_DONE),
            InlineKeyboardButton("Cancel", callback_data=CB_SPLIT_CANCEL),
        ]
    )
    return InlineKeyboardMarkup(rows)
