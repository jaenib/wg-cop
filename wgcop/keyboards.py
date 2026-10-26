"""Reply/inline keyboards, button labels, callback constants and conversation states.

Callback data never carries member names (Telegram caps it at 64 bytes and
names are user input); members are addressed by their stable ``mid``.
"""
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from wgcop.members import _get_member_name, _get_member_status, active_members

# ----------------------------------------------------------------- labels
BTN_ADD_EXPENSE = "Add Expense"
BTN_ADD_CHORE = "Add Chore"
BTN_LIST_EXPENSES = "List Expenses"
BTN_LIST_CHORES = "List Chores"
BTN_STANDINGS = "Standings"
BTN_PENALTIES = "Penalties"
BTN_SETTINGS = "Settings"
BTN_BACK_MAIN = "Back to Main Menu"
BTN_BACK_SETTINGS = "Back to Settings"
BTN_BACK_ADMIN = "Back to Admin"
BTN_CANCEL = "Cancel"
BTN_DONE = "Done"
BTN_BACK = "Back"

BTN_CHECK_PENALTIES = "Check Penalties"
BTN_REDEEM_PENALTY = "Redeem Penalty"

BTN_SWITCH_HOME = "Switch Home"
BTN_ADMIN_PANEL = "Admin Panel"
BTN_MANAGE_PREFIX = "Manage "          # "Manage <profile name>"
BTN_SET_VACATION = "Set Vacation Status"
BTN_EDIT_EXPENSES = "Edit Expenses"
BTN_EDIT_CHORES = "Edit Chores"
BTN_CHANGE_USERNAME = "Change Username"

BTN_MEMBER_MANAGEMENT = "Member Management"
BTN_MANAGE_ROLES = "Manage Roles"
BTN_HOME_SETTINGS = "Home Settings"
BTN_MANAGE_WEEKLY = "Manage Weekly Report"
BTN_ADJUST_PENALTIES = "Adjust Penalties"
BTN_SET_HOECK = "Set WG-Höck"
BTN_SYSTEM_OVERVIEW = "System Overview"

BTN_SET_REPORT_TIME = "Set Report Time"
BTN_TOGGLE_WEEKLY = "Toggle Weekly Report"
BTN_TRIGGER_WEEKLY = "Trigger Weekly Report"

BTN_EDIT_AMOUNT = "Amount"
BTN_EDIT_SPLITTERS = "Splitters"

# Edit Chores submenu (conversation-internal choices)
BTN_CHORE_POINTS = "Points"
BTN_CHORE_DESCRIPTION = "Description"
BTN_CHORE_DELETE = "Delete"
BTN_CHORE_DELETE_CONFIRM = "Yes, delete"

# Manage Roles submenu (conversation-internal choices, not navigation labels)
BTN_ROLE_ADD_ADMIN = "Add Admin"
BTN_ROLE_REMOVE_ADMIN = "Remove Admin"
BTN_ROLE_SET_CHRONICLER = "Set Chronicler"
BTN_ROLE_SET_HANDLER = "Set Handler"
BTN_ROLE_UNLINK = "Unlink Member"
BTN_ROLE_REQUESTS = "Join Requests"
BTN_NONE = "None"

# Home Settings submenu
BTN_SETTING_NAME = "Home Name"
BTN_SETTING_CURRENCY = "Currency"
BTN_SETTING_TIMEZONE = "Timezone"
BTN_SETTING_MINUTES = "Minutes per Point"
BTN_SETTING_THRESHOLD = "Penalty Threshold"
BTN_SETTING_UNIT = "Penalty Unit"

BTN_MANUAL_ENTRY = "Manual Entry"
BTN_SCAN_RECEIPT = "Scan Receipt"

# ---------------------------------------------------------- callback data
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
CB_JOIN_NEW = "join:new"
CB_JOIN_CLAIM_PREFIX = "join:claim:"
CB_JOIN_CANCEL = "join:cancel"
CB_JOIN_APPROVE_PREFIX = "join:approve:"
CB_JOIN_DENY_PREFIX = "join:deny:"

SPLIT_CALLBACK_PATTERN = r"^(?:split_toggle:.*|split_all|split_done|split_back|split_cancel)$"
RECEIPT_CALLBACK_PATTERN = r"^(?:receipt_toggle:.*|receipt_done|receipt_cancel|receipt_accept|receipt_manual)$"
JOIN_CALLBACK_PATTERN = r"^join:(?:new|cancel|claim:.*|approve:.*|deny:.*)$"
CB_HOME_DELETE_CONFIRM = "home:delete:confirm"
CB_HOME_DELETE_CANCEL = "home:delete:cancel"
HOME_DELETE_CALLBACK_PATTERN = r"^home:delete:(?:confirm|cancel)$"

# Settings
EXPENSE_LIST_LIMIT = 20

# ------------------------------------------------------- conversation states
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
MANAGE_MEMBER, MANAGE_MEMBER_ACTION, MANAGE_MEMBER_ADD = range(3)
EDIT_PICK_MEMBER, EDIT_MENU, EDIT_AMOUNT, EDIT_SPLIT = range(4)
EDIT_CHORE_PICK, EDIT_CHORE_MENU, EDIT_CHORE_POINTS, EDIT_CHORE_DESCRIPTION, EDIT_CHORE_DELETE = range(5)
REDEEM_MEMBER, REDEEM_COUNT = range(2)
ADMIN_PENALTY_MEMBER, ADMIN_PENALTY_COUNT = range(2)
ADMIN_HOECK_DATE = 0  # single-state conv
ADMIN_REPORT_TIME = 0  # single-state conv
CHANGE_USERNAME_NEW = 0  # single-state conv
ROLES_MENU, ROLES_PICK_USER = range(2)
HOME_SETTINGS_MENU, HOME_SETTINGS_VALUE = range(2)


def _rows(*labels):
    return [[KeyboardButton(label)] for label in labels]


# ------------------------------------------------------------ reply keyboards
def get_main_keyboard():
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton(BTN_ADD_EXPENSE), KeyboardButton(BTN_ADD_CHORE)],
            [KeyboardButton(BTN_LIST_EXPENSES), KeyboardButton(BTN_LIST_CHORES)],
            [KeyboardButton(BTN_STANDINGS), KeyboardButton(BTN_PENALTIES)],
            [KeyboardButton(BTN_SETTINGS)],
        ],
        resize_keyboard=True,
    )


def get_cancel_keyboard():
    return ReplyKeyboardMarkup([[KeyboardButton(BTN_CANCEL)]], resize_keyboard=True)


def get_back_cancel_keyboard():
    return ReplyKeyboardMarkup([[KeyboardButton(BTN_BACK), KeyboardButton(BTN_CANCEL)]], resize_keyboard=True)


def get_member_keyboard(home):
    members = active_members(home)
    if not members:
        return None
    buttons = [[KeyboardButton(_get_member_name(member))] for member in members]
    buttons.append([KeyboardButton(BTN_CANCEL)])
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)


def get_settings_keyboard(is_admin=False, username=None, multi_home=False):
    label = f"{BTN_MANAGE_PREFIX}{username}" if username else f"{BTN_MANAGE_PREFIX}Profile"
    buttons = [[KeyboardButton(label)]]
    if multi_home:
        buttons.append([KeyboardButton(BTN_SWITCH_HOME)])
    if is_admin:
        buttons.append([KeyboardButton(BTN_ADMIN_PANEL)])
    buttons.append([KeyboardButton(BTN_BACK_MAIN)])
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)


def get_admin_keyboard():
    return ReplyKeyboardMarkup(
        _rows(
            BTN_MEMBER_MANAGEMENT,
            BTN_MANAGE_ROLES,
            BTN_HOME_SETTINGS,
            BTN_MANAGE_WEEKLY,
            BTN_ADJUST_PENALTIES,
            BTN_SET_HOECK,
            BTN_SYSTEM_OVERVIEW,
            BTN_BACK_SETTINGS,
        ),
        resize_keyboard=True,
    )


def get_weekly_report_keyboard():
    return ReplyKeyboardMarkup(
        _rows(BTN_SET_REPORT_TIME, BTN_TOGGLE_WEEKLY, BTN_TRIGGER_WEEKLY, BTN_BACK_ADMIN),
        resize_keyboard=True,
    )


def get_manage_self_keyboard():
    return ReplyKeyboardMarkup(
        _rows(BTN_SET_VACATION, BTN_EDIT_EXPENSES, BTN_EDIT_CHORES, BTN_CHANGE_USERNAME, BTN_BACK_SETTINGS),
        resize_keyboard=True,
    )


def get_penalties_keyboard():
    return ReplyKeyboardMarkup(
        _rows(BTN_CHECK_PENALTIES, BTN_REDEEM_PENALTY, BTN_BACK_MAIN),
        resize_keyboard=True,
    )


def get_edit_choice_keyboard():
    return ReplyKeyboardMarkup(
        _rows(BTN_EDIT_AMOUNT, BTN_EDIT_SPLITTERS, BTN_CANCEL),
        resize_keyboard=True,
    )


def get_edit_chore_keyboard():
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton(BTN_CHORE_POINTS), KeyboardButton(BTN_CHORE_DESCRIPTION)],
            [KeyboardButton(BTN_CHORE_DELETE)],
            [KeyboardButton(BTN_BACK), KeyboardButton(BTN_CANCEL)],
        ],
        resize_keyboard=True,
    )


def get_confirm_delete_keyboard():
    return ReplyKeyboardMarkup(_rows(BTN_CHORE_DELETE_CONFIRM, BTN_BACK), resize_keyboard=True)


def get_roles_keyboard():
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton(BTN_ROLE_ADD_ADMIN), KeyboardButton(BTN_ROLE_REMOVE_ADMIN)],
            [KeyboardButton(BTN_ROLE_SET_CHRONICLER), KeyboardButton(BTN_ROLE_SET_HANDLER)],
            [KeyboardButton(BTN_ROLE_UNLINK), KeyboardButton(BTN_ROLE_REQUESTS)],
            [KeyboardButton(BTN_BACK_ADMIN)],
        ],
        resize_keyboard=True,
    )


def get_home_settings_keyboard():
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton(BTN_SETTING_NAME), KeyboardButton(BTN_SETTING_CURRENCY)],
            [KeyboardButton(BTN_SETTING_TIMEZONE), KeyboardButton(BTN_SETTING_MINUTES)],
            [KeyboardButton(BTN_SETTING_THRESHOLD), KeyboardButton(BTN_SETTING_UNIT)],
            [KeyboardButton(BTN_BACK_ADMIN)],
        ],
        resize_keyboard=True,
    )


def build_pick_keyboard(labels, extra=None):
    """One button per label (plus optional extras), then Cancel."""
    rows = [[KeyboardButton(_truncate_button_label(label))] for label in labels]
    for label in extra or ():
        rows.append([KeyboardButton(label)])
    rows.append([KeyboardButton(BTN_CANCEL)])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def get_expense_mode_keyboard():
    return ReplyKeyboardMarkup(
        _rows(BTN_MANUAL_ENTRY, BTN_SCAN_RECEIPT, BTN_CANCEL),
        resize_keyboard=True,
        one_time_keyboard=True,
    )


# ----------------------------------------------------------- inline keyboards
def _truncate_button_label(label: str, limit: int = 60) -> str:
    if len(label) <= limit:
        return label
    return label[: limit - 1] + "…"


def build_payer_inline_kb(members):
    rows = []
    for m in members:
        if _get_member_status(m) == "retired":
            continue
        rows.append(
            [InlineKeyboardButton(_truncate_button_label(_get_member_name(m)), callback_data=f"{CB_PAYER_PREFIX}{m['mid']}")]
        )
    return InlineKeyboardMarkup(rows)


def build_split_inline_kb(members, selected_mids):
    """Active members first, vacating members last, then All / Back / Done / Cancel."""
    selected = set(selected_mids or ())
    members = [m for m in members if _get_member_status(m) != "retired"]
    active = [m for m in members if _get_member_status(m) != "vacating"]
    vacating = [m for m in members if _get_member_status(m) == "vacating"]

    rows = []
    for m in active:
        label = f"{_get_member_name(m)}{' ✅' if m['mid'] in selected else ''}"
        rows.append([InlineKeyboardButton(_truncate_button_label(label), callback_data=f"{CB_SPLIT_TOGGLE_PREFIX}{m['mid']}")])
    for m in vacating:
        label = f"{_get_member_name(m)} (vacating){' ✅' if m['mid'] in selected else ''}"
        rows.append([InlineKeyboardButton(_truncate_button_label(label), callback_data=f"{CB_SPLIT_TOGGLE_PREFIX}{m['mid']}")])

    all_mids = {m["mid"] for m in members}
    all_selected = bool(all_mids) and all_mids <= selected
    rows.append([InlineKeyboardButton(f"All{' ✅' if all_selected else ''}", callback_data=CB_SPLIT_ALL)])
    rows.append(
        [
            InlineKeyboardButton(BTN_BACK, callback_data=CB_SPLIT_BACK),
            InlineKeyboardButton(BTN_DONE, callback_data=CB_SPLIT_DONE),
            InlineKeyboardButton(BTN_CANCEL, callback_data=CB_SPLIT_CANCEL),
        ]
    )
    return InlineKeyboardMarkup(rows)


def build_join_choice_kb(unclaimed_members, telegram_name: str):
    rows = [[InlineKeyboardButton(f"I'm new: {_truncate_button_label(telegram_name, 40)}", callback_data=CB_JOIN_NEW)]]
    for m in unclaimed_members:
        rows.append(
            [InlineKeyboardButton(f"I am {_truncate_button_label(m['name'], 40)}", callback_data=f"{CB_JOIN_CLAIM_PREFIX}{m['mid']}")]
        )
    rows.append([InlineKeyboardButton(BTN_CANCEL, callback_data=CB_JOIN_CANCEL)])
    return InlineKeyboardMarkup(rows)


def build_delete_home_kb(home_name: str):
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(_truncate_button_label(f"Yes, delete '{home_name}' permanently"), callback_data=CB_HOME_DELETE_CONFIRM)],
            [InlineKeyboardButton(BTN_CANCEL, callback_data=CB_HOME_DELETE_CANCEL)],
        ]
    )


def build_join_review_kb(uid: int):
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Approve", callback_data=f"{CB_JOIN_APPROVE_PREFIX}{uid}"),
                InlineKeyboardButton("Deny", callback_data=f"{CB_JOIN_DENY_PREFIX}{uid}"),
            ]
        ]
    )
