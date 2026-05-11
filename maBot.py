import json
import base64
import logging
import copy
from random import random
from random import randint
import sys, os
import re
import tempfile
import html
import pytz
import shutil, time
from pathlib import Path
from datetime import datetime, timedelta
import importlib.util

from telegram import (
    Update, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove,
    InlineKeyboardMarkup, InlineKeyboardButton,
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters, CallbackContext,
    ConversationHandler, CallbackQueryHandler,
)
from telegram.error import TelegramError

try:
    import openai as _openai_lib
except ImportError:  # pragma: no cover - optional dependency
    _openai_lib = None

# Set up logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)

# Secrets loader

ROOT = Path(__file__).resolve().parent
CFG_PATH = ROOT / ".secrets" / "config.py"

if not CFG_PATH.exists():
    raise FileNotFoundError(f"Missing secrets file: {CFG_PATH}")

_spec = importlib.util.spec_from_file_location("wgcop_config", CFG_PATH)
_config = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_config)

# Bot token & UUID
TOKEN = getattr(_config, "TOKEN")
GROUP_CHAT_ID = getattr(_config, "GROUP_CHAT_ID")
try:
    BOT_HANDLER_ID = int(getattr(_config, "BOT_HANDLER_ID"))
except (TypeError, ValueError):
    BOT_HANDLER_ID = None  # admin features disabled when unconfigured
CHRONICLER_ID = getattr(_config, "CHRONICLER_ID")
NI_ID = getattr(_config, "NI_ID")
GI_ID = getattr(_config, "GI_ID")
GY_ID = getattr(_config, "GY_ID")
TO_ID = getattr(_config, "TO_ID")
JA_ID = getattr(_config, "JA_ID")
UIDS = [NI_ID, GI_ID, GY_ID, TO_ID, JA_ID]
OPENAI_API_KEY = getattr(_config, "OPENAI_API_KEY", None)

# Data storage
DATA_FILE = "wg_data_alpha.json"


def _get_chronicler_chat_id():
    if not CHRONICLER_ID:
        return None

    chronicler_chat_id = str(CHRONICLER_ID).strip()
    if not chronicler_chat_id or chronicler_chat_id == "your_chronicler_chatid":
        return None

    return chronicler_chat_id


def load_data():
    try:
        with open(DATA_FILE, "r") as file:
            data = json.load(file)
    except (FileNotFoundError, json.JSONDecodeError):
        default_data = {
            "expenses": [],
            "chores": {},
            "chore_log": [],
            "penalties": {},
            "members": [],
            "chronicler_backup": {"greeting_sent": False, "last_sent": None},
        }
        with open(DATA_FILE, "w") as file:
            json.dump(default_data, file, indent=4)
        return default_data

    if "chronicler_backup" not in data:
        data["chronicler_backup"] = {
            "greeting_sent": False,
            "last_sent": None,
        }
    if "chore_log" not in data:
        data["chore_log"] = []
    
    # Migrate members from strings to objects with status field
    if data.get("members"):
        needs_migration = False
        for member in data["members"]:
            if isinstance(member, str):
                needs_migration = True
                break
        
        if needs_migration:
            data["members"] = [
                _member_to_dict(m) if isinstance(m, str) else m
                for m in data["members"]
            ]
            save_data(data)
            logger.info("Migrated members to new object format with status field")
    
    save_data(data)
    return data


def save_data(data):
    """Save bot data safely, keeping timestamped backups."""
    if os.path.exists(DATA_FILE):
        # Make timestamped backup before overwriting
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        backup_file = f"{DATA_FILE}.{timestamp}.bak"
        try:
            shutil.copy2(DATA_FILE, backup_file)
            print(f"[wg-cop] Backup created: {backup_file}")
        except Exception as e:
            print(f"[wg-cop] Warning: failed to backup data file: {e}")

    # Now overwrite safely
    tmp_file = DATA_FILE + ".tmp"
    with open(tmp_file, "w") as file:
        json.dump(data, file, indent=4)
    os.replace(tmp_file, DATA_FILE)


def _normalise_member_name(name: str) -> str:
    """Normalize a member name (string or object) to lowercase."""
    if isinstance(name, dict):
        name = name.get("name", "")
    return name.strip().casefold() if name else ""


def _get_member_name(member):
    """Get the display name from a member (string or object)."""
    if isinstance(member, dict):
        return member.get("name", "")
    return member


def _get_member_status(member):
    """Get the status from a member object, default to 'active'."""
    if isinstance(member, dict):
        return member.get("status", "active")
    return "active"


def _member_to_dict(member_name: str):
    """Convert a member name to a member object."""
    return {"name": member_name, "status": "active"}


def _match_member_UID(candidate):
    candidate_norm = str(_normalise_member_name(candidate))
    print(f"Matching candidate: {candidate_norm} and {JA_ID}")

    if candidate_norm == str(NI_ID):
        return _member_to_dict("Nicci Lopez")
    if candidate_norm == str(GI_ID):
        return _member_to_dict("Gjango Gmüseshole")
    if candidate_norm == str(GY_ID):
        return _member_to_dict("General Guysan")
    if candidate_norm == str(TO_ID):
        return _member_to_dict("Thomath Sucker")
    if candidate_norm == str(JA_ID):
        return _member_to_dict("Janidputzä")
    return None


def _match_member_name(members, candidate):
    """Match a candidate name against a list of members (strings or objects)."""
    candidate_norm = _normalise_member_name(candidate)
    for member in members:
        if _normalise_member_name(member) == candidate_norm:
            return member
    return None


def calculate_balances(data):
    members = data.get("members", []) or []
    # Create balance dict using member names (strings)
    member_names = [_get_member_name(m) for m in members]
    balances = {name: 0.0 for name in member_names}

    for expense in data.get("expenses", []) or []:
        amount = float(expense.get("amount", 0.0) or 0.0)
        payer = _match_member_name(members, expense.get("payer"))
        payer_name = _get_member_name(payer) if payer else None
        
        split_with = []
        for participant in expense.get("split_with", []) or []:
            matched = _match_member_name(members, participant)
            if matched:
                split_with.append(matched)

        if not split_with:
            continue

        share = amount / len(split_with)

        if payer_name:
            balances[payer_name] = balances.get(payer_name, 0.0) + amount

        for participant in split_with:
            participant_name = _get_member_name(participant)
            balances[participant_name] = balances.get(participant_name, 0.0) - share

    return balances


def _format_currency(amount: float) -> str:
    return f"CHF {amount:.2f}"


def _format_signed_currency(amount: float) -> str:
    sign = "+" if amount >= 0 else ""
    return f"CHF {sign}{amount:.2f}"


def _resolve_member_for_user(user):
    if not user or not user.id:
        return None

    uid = user.id
    data = load_data()
    members = data.get("members") or []

    # Primary lookup: find member by stored uid field
    for member in members:
        if isinstance(member, dict) and member.get("uid") == uid:
            return member

    # Fallback: hardcoded UID map (bootstrap for members without uid stored yet)
    match = _match_member_UID(str(uid))
    if match:
        matched_name = _get_member_name(match)
        # Find the actual member in data and persist the uid
        for member in members:
            if isinstance(member, dict) and _normalise_member_name(member.get("name", "")) == _normalise_member_name(matched_name):
                member["uid"] = uid
                save_data(data)
                return member
        # Name from hardcoded map not in members list (shouldn't happen normally)
        return match

    return None


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


class ReceiptParsingError(Exception):
    """Raised when a receipt image cannot be parsed for line items."""


# Callback data prefixes
CB_PAYER_PREFIX = "payer:"
CB_SPLIT_TOGGLE_PREFIX = "split_toggle:"
CB_SPLIT_DONE = "split_done"
CB_SPLIT_BACK = "split_back"
CB_SPLIT_CANCEL = "split_cancel"
CB_RECEIPT_TOGGLE_PREFIX = "receipt_toggle:"
CB_RECEIPT_DONE = "receipt_done"
CB_RECEIPT_CANCEL = "receipt_cancel"

# Settings
EXPENSE_LIST_LIMIT = 20

# States for conversation handler
(
    EXPENSE_MODE,
    EXPENSE_DESCRIPTION,
    EXPENSE_AMOUNT,
    EXPENSE_PAYER,
    EXPENSE_SPLIT,
    EXPENSE_RECEIPT,
    EXPENSE_RECEIPT_REVIEW,
    EXPENSE_RECEIPT_MANUAL,
    EXPENSE_RECEIPT_CONFIRM_TOTAL,
) = range(9)
CHORE_USER, CHORE_MINUTES, CHORE_DESCRIPTION = range(3)
MANAGE_MEMBER = range(1)
EDIT_PICK_MEMBER, EDIT_MENU, EDIT_AMOUNT, EDIT_SPLIT = range(4)
REDEEM_MEMBER, REDEEM_COUNT = range(2)
ADMIN_BEER_MEMBER, ADMIN_BEER_COUNT = range(2)
ADMIN_HOECK_DATE = 0  # single-state conv
CHANGE_USERNAME_NEW = 0  # single-state conv

RECEIPT_IMAGE_FILTER = filters.PHOTO | filters.Document.IMAGE


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
            [KeyboardButton("Set Weekly Report")],
            [KeyboardButton("Trigger Weekly Report")],
            [KeyboardButton("Adjust Beer Count")],
            [KeyboardButton("Set WG-Höck")],
            [KeyboardButton("Back to Settings")],
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


async def open_penalties(update: Update, context: CallbackContext) -> None:
    await update.message.reply_text(
        "Penalties menu:", reply_markup=get_penalties_keyboard()
    )


def _settings_keyboard_for_user(user):
    is_admin = user.id == BOT_HANDLER_ID
    member = _resolve_member_for_user(user)
    username = _get_member_name(member) if member else None
    return get_settings_keyboard(is_admin=is_admin, username=username)


async def open_settings(update: Update, context: CallbackContext) -> None:
    await update.message.reply_text(
        "Settings menu:",
        reply_markup=_settings_keyboard_for_user(update.effective_user),
    )


async def settings_back(update: Update, context: CallbackContext) -> None:
    await update.message.reply_text(
        "Main menu ready.", reply_markup=get_main_keyboard()
    )


async def open_admin_menu(update: Update, context: CallbackContext) -> None:
    if update.effective_user.id != BOT_HANDLER_ID:
        await update.message.reply_text("Unauthorized.")
        return
    await update.message.reply_text("Admin panel:", reply_markup=get_admin_keyboard())


async def back_to_settings(update: Update, context: CallbackContext) -> None:
    await update.message.reply_text(
        "Settings menu:",
        reply_markup=_settings_keyboard_for_user(update.effective_user),
    )


async def open_manage_self(update: Update, context: CallbackContext) -> None:
    member = _resolve_member_for_user(update.effective_user)
    if not member:
        await update.message.reply_text(
            "I can't match you to a household member. Ask an admin to add you."
        )
        return
    username = _get_member_name(member)
    await update.message.reply_text(
        f"Managing your profile: {username}",
        reply_markup=get_manage_self_keyboard(),
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
        prefix = "[x] " if picked else "[ ] "
        label = f"{prefix}{name}"
        rows.append(
            [InlineKeyboardButton(label, callback_data=f"{CB_SPLIT_TOGGLE_PREFIX}{name}")]
        )
    
    # Add vacating members in italic (reminder not to forget them for long-term only)
    for name, m in vacating_members:
        picked = _normalise_member_name(m) in {_normalise_member_name(s) for s in selected}
        prefix = "[x] " if picked else "[ ] "
        label = f"{prefix}<i>{name} (vacating)</i>"
        rows.append(
            [InlineKeyboardButton(f"{prefix}{name} (vacating)", callback_data=f"{CB_SPLIT_TOGGLE_PREFIX}{name}")]
        )
    
    rows.append(
        [
            InlineKeyboardButton("Back", callback_data=CB_SPLIT_BACK),
            InlineKeyboardButton("Done", callback_data=CB_SPLIT_DONE),
            InlineKeyboardButton("Cancel", callback_data=CB_SPLIT_CANCEL),
        ]
    )
    return InlineKeyboardMarkup(rows)


def _receipt_shared_total(items, selected, confirmed_total=None):
    all_sum = sum(item["amount"] for item in items)
    selected_sum = sum(items[i]["amount"] for i in selected if i < len(items))
    if confirmed_total and all_sum:
        return round(confirmed_total * selected_sum / all_sum, 2)
    return round(selected_sum, 2)


def build_receipt_items_text(items, selected, confirmed_total=None):
    total = _receipt_shared_total(items, selected, confirmed_total)
    return f"Tap items to exclude personal ones.\n\nShared total: CHF {total:.2f}"


def build_receipt_items_kb(items, selected):
    rows = []
    for idx, item in enumerate(items):
        picked = idx in selected
        marker = "[x]" if picked else "[ ]"
        label = _truncate_button_label(
            f"{marker} {item['name']} ({item['amount']:.2f})"
        )
        rows.append(
            [
                InlineKeyboardButton(
                    label, callback_data=f"{CB_RECEIPT_TOGGLE_PREFIX}{idx}"
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton("Done", callback_data=CB_RECEIPT_DONE),
            InlineKeyboardButton("Cancel", callback_data=CB_RECEIPT_CANCEL),
        ]
    )
    return InlineKeyboardMarkup(rows)


_LINE_ITEM_AMOUNT_RE = re.compile(r"(-?\d+[.,]\d{1,2})")
_COLUMN_TOKEN_RE = re.compile(r"^-?\d+(?:[.,]\d+)?$")
_HEADER_MARKERS = re.compile(r"\bartikel\b", re.IGNORECASE)
_STOP_MARKERS = re.compile(r"\b(total|summe|gesamt)\b", re.IGNORECASE)
_COLUMN_WORDS = {"aktion", "ak", "chf"}


def _looks_like_column_token(token: str) -> bool:
    stripped = token.strip(" :-–—")
    if not stripped:
        return True
    lower = stripped.lower()
    if _COLUMN_TOKEN_RE.fullmatch(stripped):
        return True
    if lower in _COLUMN_WORDS:
        return True
    if len(stripped) == 1 and stripped.isalpha() and stripped.isupper():
        return True
    return False


def _normalise_receipt_line(line: str) -> str:
    return re.sub(r"\s+", " ", line).strip()


def _parse_line_item(line: str):
    clean = _normalise_receipt_line(line)
    if not clean:
        return None

    matches = list(_LINE_ITEM_AMOUNT_RE.finditer(clean))
    if not matches:
        return None

    amount_match = matches[-1]
    amount_raw = amount_match.group(1)
    try:
        amount = round(float(amount_raw.replace(",", ".")), 2)
    except ValueError:
        return None

    name_part = clean[: amount_match.start()].strip()
    if not name_part:
        return None

    tokens = name_part.split()
    while tokens and _looks_like_column_token(tokens[-1]):
        tokens.pop()

    name = " ".join(tokens).strip(" :-–—")
    if len(name) < 2 or sum(ch.isalpha() for ch in name) < 2:
        return None

    return {"name": name, "amount": amount}


def parse_receipt_text(text: str):
    raw_lines = [ln.rstrip() for ln in text.splitlines()]
    items = []

    start_idx = 0
    for idx, line in enumerate(raw_lines):
        if _HEADER_MARKERS.search(line):
            start_idx = idx + 1
            break

    relevant_lines = []
    for line in raw_lines[start_idx:]:
        if _STOP_MARKERS.search(line):
            break
        if not line.strip():
            continue
        lower_line = line.lower()
        if (
            not any(ch.isdigit() for ch in lower_line)
            and any(word in lower_line for word in ("menge", "preis", "aktion", "mwst", "vat"))
        ):
            continue
        relevant_lines.append(line)

    buffer = ""
    for line in relevant_lines:
        candidate = f"{buffer} {line}".strip() if buffer else line
        parsed = _parse_line_item(candidate)
        if parsed:
            items.append(parsed)
            buffer = ""
            continue

        buffer = candidate

    if buffer:
        parsed = _parse_line_item(buffer)
        if parsed:
            items.append(parsed)

    return items


def extract_items_from_receipt(image_path: str, mime_type: str = "image/jpeg"):
    if not _openai_lib or not OPENAI_API_KEY:
        raise ReceiptParsingError("Receipt scanning is not configured (missing OPENAI_API_KEY).")

    try:
        with open(image_path, "rb") as f:
            image_data = base64.standard_b64encode(f.read()).decode("utf-8")
    except Exception as exc:
        raise ReceiptParsingError("Failed to read the receipt image.") from exc

    media_type = mime_type if mime_type and mime_type.startswith("image/") else "image/jpeg"

    client = _openai_lib.OpenAI(api_key=OPENAI_API_KEY)
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            max_tokens=1024,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{media_type};base64,{image_data}"},
                    },
                    {
                        "type": "text",
                        "text": (
                            "Extract every purchased line item from this receipt and suggest a short expense description.\n"
                            "Return ONLY a JSON object — no markdown, no explanation — in this format:\n"
                            '{"description": "Migros groceries", "items": [{"name": "Product name", "amount": 9.95}, ...]}\n\n'
                            "Rules:\n"
                            "- description: 2-4 words, store name + category (e.g. 'Migros groceries', 'Lidl snacks')\n"
                            "- For each item use the TOTAL column (the rightmost price on the line) as the amount — never multiply Menge × Preis yourself\n"
                            "- For weighted items (e.g. 0.275 kg) the Total column already shows the CHF amount charged; use it as-is\n"
                            "- For discounted items use the discounted/action price shown in the Total column, not the original price\n"
                            "- For multi-quantity items (e.g. Menge=2) the Total column already reflects the full line total; use it\n"
                            "- If the same product name appears on multiple lines (e.g. Bauernspeck twice with different weights), list EACH line as a separate item — do not merge them\n"
                            "- Exclude header rows, subtotals, receipt totals, tax lines, and loyalty/points lines\n"
                            "- Keep item names short but recognisable; append weight (e.g. '0.128kg') to disambiguate duplicate names"
                        ),
                    },
                ],
            }],
        )
    except Exception as exc:
        raise ReceiptParsingError(f"Vision API error: {exc}") from exc

    raw = response.choices[0].message.content.strip()
    try:
        start = raw.index("{")
        end = raw.rindex("}") + 1
        parsed = json.loads(raw[start:end])
        items = parsed["items"]
        description = str(parsed.get("description", "")).strip()
    except (ValueError, json.JSONDecodeError, KeyError) as exc:
        raise ReceiptParsingError(f"Could not parse API response as JSON: {exc}") from exc

    if not items:
        raise ReceiptParsingError("No line items found in receipt.")

    try:
        normalised = []
        for i in items:
            if not isinstance(i, dict) or "name" not in i or i.get("amount") is None:
                continue
            normalised.append({"name": str(i["name"]), "amount": round(float(i["amount"]), 2)})
    except (TypeError, ValueError, KeyError) as exc:
        raise ReceiptParsingError(f"Failed to parse item data: {exc}") from exc

    if not normalised:
        raise ReceiptParsingError("No valid line items found in receipt.")

    return {
        "description": description,
        "items": normalised,
    }


# Start
async def start(update: Update, context: CallbackContext) -> None:
    await update.message.reply_text(
        "WG Bot is active! Use the buttons below:", reply_markup=get_main_keyboard()
    )


# Manage Members
async def manage_members(update: Update, context: CallbackContext) -> int:
    if update.effective_user.id != BOT_HANDLER_ID:
        await update.message.reply_text("Unauthorized.")
        return ConversationHandler.END
    data = load_data()
    if data["members"]:
        members_list = ", ".join(_get_member_name(m) for m in data["members"])
        txt = (
            f"Current members: {members_list}\n\n"
            "Send a name to add/remove.\n"
            "Or type 'Back' to return without changes."
        )
    else:
        txt = "No members yet. Send a name to add. Or type 'Back' to return."

    await update.message.reply_text(
        txt,
        reply_markup=ReplyKeyboardMarkup([[KeyboardButton("Back")]], resize_keyboard=True),
    )
    return MANAGE_MEMBER


async def modify_members(update: Update, context: CallbackContext) -> int:
    data = load_data()
    text = update.message.text.strip()
    if text.lower() == "back":
        await update.message.reply_text(
            "Member management closed.", reply_markup=get_main_keyboard()
        )
        return ConversationHandler.END

    name_ci = text.lower()
    existing_index = next(
        (i for i, m in enumerate(data["members"]) if _normalise_member_name(m) == name_ci), None
    )
    if existing_index is not None:
        removed_member = data["members"].pop(existing_index)
        removed_name = _get_member_name(removed_member)
        response = f"Removed {removed_name} from the household."
    else:
        data["members"].append(_member_to_dict(text))
        response = f"Added {text} to the household."

    save_data(data)
    await update.message.reply_text(response, reply_markup=get_main_keyboard())
    return ConversationHandler.END


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
            await update.message.reply_text(
                "I couldn't read the receipt automatically."
                "\nPlease send the items as text in the format 'Item - price',"
                " one per line."
            )
            return EXPENSE_RECEIPT_MANUAL

    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)

    items = result.get("items", []) if isinstance(result, dict) else result
    if not items:
        await analysing_msg.delete()
        await update.message.reply_text(
            "I couldn't find any purchasable items. Please send them as text, one per line."
        )
        return EXPENSE_RECEIPT_MANUAL

    parsed_total = round(sum(item["amount"] for item in items), 2)

    context.user_data["mode"] = "receipt"
    context.user_data["receipt_items"] = items
    context.user_data["receipt_parsed_total"] = parsed_total
    if isinstance(result, dict) and result.get("description"):
        context.user_data["receipt_description"] = result["description"]

    await analysing_msg.delete()
    total_kb = ReplyKeyboardMarkup(
        [[f"{parsed_total:.2f}"]],
        one_time_keyboard=True,
        resize_keyboard=True,
    )
    await update.message.reply_text(
        f"Found {len(items)} items. Parsed total: <b>CHF {parsed_total:.2f}</b>\n"
        "Does this match your receipt? Tap to confirm or type the correct total:",
        parse_mode="HTML",
        reply_markup=total_kb,
    )
    return EXPENSE_RECEIPT_CONFIRM_TOTAL


async def expense_receipt_confirm_total(update: Update, context: CallbackContext) -> int:
    text = update.message.text.strip()
    if text.lower() == "cancel":
        return await cancel(update, context)

    try:
        confirmed_total = round(float(text.replace(",", ".")), 2)
    except ValueError:
        await update.message.reply_text("Please enter a valid amount (e.g. 129.50).")
        return EXPENSE_RECEIPT_CONFIRM_TOTAL

    items = context.user_data.get("receipt_items", [])
    context.user_data["confirmed_total"] = confirmed_total
    context.user_data["amount"] = confirmed_total
    context.user_data["receipt_selected"] = set(range(len(items)))

    await update.message.reply_text(
        build_receipt_items_text(items, context.user_data["receipt_selected"], confirmed_total),
        reply_markup=build_receipt_items_kb(items, context.user_data["receipt_selected"]),
    )
    return EXPENSE_RECEIPT_REVIEW


async def expense_receipt_invalid(update: Update, context: CallbackContext) -> int:
    await update.message.reply_text(
        "Please send a photo or image of the receipt, or type Cancel to abort."
    )
    return EXPENSE_RECEIPT


async def expense_receipt_manual_items(update: Update, context: CallbackContext) -> int:
    raw = update.message.text or ""
    items = parse_receipt_text(raw)
    if not items:
        await update.message.reply_text(
            "I couldn't understand any items. Use lines like 'Bread - 3.50'."
        )
        return EXPENSE_RECEIPT_MANUAL

    context.user_data["mode"] = "receipt"
    context.user_data["receipt_items"] = items
    context.user_data["receipt_selected"] = set(range(len(items)))

    await update.message.reply_text(
        build_receipt_items_text(items, context.user_data["receipt_selected"]),
        reply_markup=build_receipt_items_kb(
            items, context.user_data["receipt_selected"]
        ),
    )
    return EXPENSE_RECEIPT_REVIEW


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

    confirmed_total = context.user_data.get("confirmed_total")

    if query.data == CB_RECEIPT_DONE:
        if not selected:
            await query.answer("Select at least one item.", show_alert=True)
            return EXPENSE_RECEIPT_REVIEW

        chosen = [items[i] for i in sorted(selected)]
        total = _receipt_shared_total(items, selected, confirmed_total)
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
            build_receipt_items_text(items, selected, confirmed_total),
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


# Chore flow
async def start_chore(update: Update, context: CallbackContext) -> int:
    data = load_data()
    keyboard = get_member_keyboard(data)
    if keyboard:
        await update.message.reply_text(
            "Who completed the chore?", reply_markup=keyboard
        )
    else:
        await update.message.reply_text(
            "No members found. Please add members first.",
            reply_markup=get_main_keyboard(),
        )
        return ConversationHandler.END
    return CHORE_USER


async def chore_user(update: Update, context: CallbackContext) -> int:
    choice = update.message.text.strip()
    if choice.lower() in {"done", "cancel", "back to main menu"}:
        await update.message.reply_text(
            "Chore entry cancelled.", reply_markup=get_main_keyboard()
        )
        context.user_data.clear()
        return ConversationHandler.END

    context.user_data["user"] = choice
    await update.message.reply_text(
        "How many minutes did it take?", reply_markup=ReplyKeyboardRemove()
    )
    return CHORE_MINUTES


def _rename_member_in_data(data: dict, old_name: str, new_name: str) -> None:
    """Atomically rename a member across all data structures."""
    old_norm = _normalise_member_name(old_name)

    for m in data.get("members") or []:
        if isinstance(m, dict) and _normalise_member_name(m.get("name", "")) == old_norm:
            m["name"] = new_name

    chores = data.setdefault("chores", {})
    if old_name in chores:
        chores[new_name] = chores.pop(old_name)

    penalties = data.setdefault("penalties", {})
    if old_name in penalties:
        penalties[new_name] = penalties.pop(old_name)

    violators = data.setdefault("last_week_violators", {})
    old_v_key = _normalise_member_name(old_name)
    new_v_key = _normalise_member_name(new_name)
    if old_v_key in violators:
        violators[new_v_key] = violators.pop(old_v_key)

    for entry in data.get("chore_log") or []:
        if _normalise_member_name(entry.get("member", "")) == old_norm:
            entry["member"] = new_name

    for expense in data.get("expenses") or []:
        if _normalise_member_name(expense.get("payer", "")) == old_norm:
            expense["payer"] = new_name
        expense["split_with"] = [
            new_name if _normalise_member_name(p) == old_norm else p
            for p in expense.get("split_with") or []
        ]


def add_chore_entry(data, member: str, points: int, description: str | None = None):
    entry = {
        "timestamp": datetime.now(pytz.timezone("Europe/Berlin")).isoformat(),
        "member": member,
        "points": points,
        "description": description or "",
    }

    data.setdefault("chore_log", []).append(entry)

    # keep your existing totals working
    data.setdefault("chores", {})
    data["chores"].setdefault(member, 0)
    data["chores"][member] += points

    save_data(data)


async def chore_minutes(update: Update, context: CallbackContext) -> int:
    try:
        minutes = int(update.message.text)
    except ValueError:
        await update.message.reply_text(
            "Invalid input. Enter the minutes again."
        )
        return CHORE_MINUTES

    points = minutes // 15
    user = context.user_data["user"]

    # Remember values for the next step
    context.user_data["minutes"] = minutes
    context.user_data["points"] = points

    # Ask for an optional description
    await update.message.reply_text(
        (
            f"{user} earned {points} points.\n"
            "Optional: send a short description of the chore "
            "(what was done). Send '-' to skip."
        ),
        reply_markup=ReplyKeyboardRemove(),
    )
    return CHORE_DESCRIPTION


async def chore_description(update: Update, context: CallbackContext) -> int:
    data = load_data()

    user = context.user_data.get("user")
    points = context.user_data.get("points")

    if user is None or points is None:
        # Something went wrong in the flow, bail out cleanly
        await update.message.reply_text(
            "Something went wrong with the chore entry. Please try again.",
            reply_markup=get_main_keyboard(),
        )
        context.user_data.clear()
        return ConversationHandler.END

    raw_desc = update.message.text.strip()
    # Allow skipping via '-' or 'skip'
    if raw_desc in {"-", "skip", "Skip"}:
        description = ""
    else:
        description = raw_desc

    # Single source of truth: logs + totals
    add_chore_entry(data, user, points, description)

    text = f"{user} earned {points} points!"
    if description:
        text += f"\n📝 {description}"

    await update.message.reply_text(text, reply_markup=get_main_keyboard())
    context.user_data.clear()
    return ConversationHandler.END


async def handle_chore(update: Update, context: CallbackContext) -> None:
    # Usage: /chore <points> <optional description...>
    if not context.args:
        await update.message.reply_text(
            "Usage: /chore <points> [description...]\n"
            "Example: /chore 2 cleaned bathroom"
        )
        return

    points_str, *desc_parts = context.args
    try:
        points = int(points_str)
    except ValueError:
        await update.message.reply_text("First argument has to be the number of points.")
        return

    description = " ".join(desc_parts) if desc_parts else ""

    user = update.effective_user
    data = load_data()

    member_obj = _match_member_name(
        data["members"],
        str(user.id),
    ) or _match_member_name(data["members"], user.full_name)
    
    member_name = _get_member_name(member_obj) if member_obj else user.full_name

    add_chore_entry(data, member_name, points, description)

    text = f"Recorded {points} points for {member_name}."
    if description:
        text += f"\n📝 {description}"

    await update.message.reply_text(text)


def _format_chore_entry(entry):
    ts = entry.get("timestamp", "")[:16].replace("T", " ")
    member = entry.get("member", "?")
    points = entry.get("points", 0)
    desc = entry.get("description", "")
    desc_part = f" – {desc}" if desc else ""
    return f"<b>{ts}</b> · {member} (+{points} pts){desc_part}"


async def list_chores(update: Update, context: CallbackContext) -> None:
    data = load_data()
    entries = data.get("chore_log", []) or []
    if not entries:
        await update.message.reply_text(
            "No chores recorded yet.", reply_markup=get_main_keyboard()
        )
        return

    # Show last 10
    entries = entries[-10:]
    formatted = "\n\n".join(_format_chore_entry(e) for e in entries)
    await update.message.reply_html(formatted, reply_markup=get_main_keyboard())


# Display new standings after entry
def _format_expense_entry(entry, viewer_name):
    date = str(entry.get("date", "?"))
    description = html.escape(str(entry.get("description", "(no description)")))
    amount = float(entry.get("amount", 0.0) or 0.0)
    payer = str(entry.get("payer", "?"))
    split_raw = [str(name) for name in (entry.get("split_with", []) or [])]
    split_names = [html.escape(name) for name in split_raw]

    viewer_norm = _normalise_member_name(viewer_name)
    payer_norm = _normalise_member_name(payer)
    viewer_in_split = any(
        _normalise_member_name(name) == viewer_norm for name in split_raw
    )

    lines = [
        f"<b>{html.escape(date)}</b> · {description}",
        f"Total: {_format_currency(amount)}",
        f"Payer: {html.escape(payer)}",
    ]

    if split_names:
        lines.append(
            f"Split ({len(split_names)}): {', '.join(split_names)}"
        )
    else:
        lines.append("Split: —")

    if viewer_norm:
        if payer_norm == viewer_norm:
            share_count = len(split_raw)
            if share_count:
                share = amount / share_count
                owed = share * sum(
                    1 for name in split_raw if _normalise_member_name(name) != payer_norm
                )
                if owed > 0:
                    lines.append(f"To receive: {_format_currency(owed)}")
        elif viewer_in_split:
            share_count = len(split_raw)
            if share_count:
                share = amount / share_count
                lines.append(f"Your share: {_format_currency(share)}")

    return "\n".join(lines)


# Show latest logged expenses
async def list_expenses(update: Update, context: CallbackContext) -> None:
    data = load_data()
    expenses = data.get("expenses", []) or []
    if not expenses:
        await update.message.reply_text(
            "No expenses recorded yet.", reply_markup=get_main_keyboard()
        )
        return

    members = data.get("members", []) or []
    viewer = _resolve_member_for_user(update.effective_user)
    context.user_data["viewer_member"] = viewer

    if not viewer:
        await update.message.reply_text(
            "I cannot match you to a household member. Please ask to be added to the member list.",
            reply_markup=get_main_keyboard(),
        )
        return

    viewer_name = _get_member_name(viewer)
    viewer_norm = _normalise_member_name(viewer_name)

    relevant = []
    for entry in reversed(expenses):
        payer = entry.get("payer")
        split_with = entry.get("split_with", []) or []
        is_payer = _normalise_member_name(payer) == viewer_norm
        in_split = any(
            _normalise_member_name(name) == viewer_norm for name in split_with
        )
        if is_payer or in_split:
            relevant.append(entry)
        if len(relevant) >= EXPENSE_LIST_LIMIT:
            break

    if not relevant:
        await update.message.reply_text(
            "No recent expenses linked to you.", reply_markup=get_main_keyboard()
        )
        return

    formatted = [
        _format_expense_entry(entry, viewer_name) for entry in reversed(relevant)
    ]
    header = f"<b>Recent expenses involving {html.escape(viewer_name)}</b>"
    text = header + "\n\n" + "\n\n".join(formatted)
    text = text + "\n\n" + header
    await update.message.reply_html(text, reply_markup=get_main_keyboard())


# Calculate + show standings
async def standings(update: Update, context: CallbackContext) -> None:
    data = load_data()
    members = data.get("members", [])
    if not members:
        await update.message.reply_text(
            "No members recorded yet.", reply_markup=get_main_keyboard()
        )
        return

    # Create member name dict for lookups
    member_names = [_get_member_name(m) for m in members]
    balances = {name: 0.0 for name in member_names}

    for expense in data.get("expenses", []):
        payer = expense.get("payer", "")
        amount = float(expense.get("amount", 0.0))
        split_with = expense.get("split_with", []) or []
        if not split_with:
            continue
        share = amount / len(split_with)

        payer_matched = _match_member_name(members, payer)
        payer_name = _get_member_name(payer_matched) if payer_matched else None
        if payer_name:
            balances[payer_name] = balances.get(payer_name, 0.0) + amount

        for u in split_with:
            u_matched = _match_member_name(members, u)
            u_name = _get_member_name(u_matched) if u_matched else None
            if u_name:
                balances[u_name] = balances.get(u_name, 0.0) - share

    chores = {}
    for name, pts in (data.get("chores", {}) or {}).items():
        mkey = _match_member_name(members, name)
        if mkey:
            chores[_get_member_name(mkey)] = pts

    ordered = sorted(member_names, key=lambda m: (chores.get(m, 0)), reverse=True)

    lines = []
    for m in ordered:
        points = chores.get(m, 0)
        bal = balances.get(m, 0.0)
        lines.append(f"{m}: {points} points, {_format_signed_currency(bal)}")

    await update.message.reply_text("\n".join(lines), reply_markup=get_main_keyboard())


# Beer owed
async def beer_owed(update: Update, context: CallbackContext) -> None:
    data = load_data()
    members = data.get("members", []) or []
    chores = data.get("chores", {}) or {}

    # Build name map from members (supporting both string and object formats)
    name_map = {}
    for member in members:
        member_name = _get_member_name(member)
        name_map[_normalise_member_name(member_name)] = member_name

    leaderboard = []
    for raw_name, points in chores.items():
        matched = name_map.get(_normalise_member_name(raw_name))
        if matched:
            leaderboard.append((matched, points))

    if not leaderboard:
        await update.message.reply_text("No chores recorded yet.")
        return

    leaderboard.sort(key=lambda item: -item[1])
    leader_name, leader_points = leaderboard[0]
    penalties = data.get("penalties", {}) or {}

    # Collect members currently lagging beyond threshold
    currently_lagging = set()
    lines = []
    for member, points in leaderboard[1:]:
        gap = leader_points - points
        if gap > 4:
            currently_lagging.add(member)
            owed = penalties.get(member, 0)
            if owed:
                lines.append(
                    f"{member} owes {owed} beer(s) ({gap} pts behind {leader_name})."
                )
            else:
                lines.append(
                    f"{member} is {gap} pts behind {leader_name} (warning)."
                )

    # Also list members who still owe beers but have since caught up
    for member, owed in penalties.items():
        if owed > 0 and member not in currently_lagging:
            lines.append(
                f"{member} still owes {owed} beer(s) (caught up, debt remains)."
            )

    if lines:
        await update.message.reply_text(
            "Beer Penalties:\n" + "\n".join(lines),
            reply_markup=get_penalties_keyboard(),
        )
    else:
        await update.message.reply_text(
            "No penalties this week!", reply_markup=get_penalties_keyboard()
        )


async def redeem_start(update: Update, context: CallbackContext) -> int:
    data = load_data()
    penalties = data.get("penalties", {}) or {}
    members_with_penalties = {k: v for k, v in penalties.items() if v > 0}

    if not members_with_penalties:
        await update.message.reply_text(
            "Nobody owes any beers right now.", reply_markup=get_penalties_keyboard()
        )
        return ConversationHandler.END

    buttons = [[KeyboardButton(name)] for name in sorted(members_with_penalties)]
    buttons.append([KeyboardButton("Cancel")])
    await update.message.reply_text(
        "Who brought the beer? Select a member:",
        reply_markup=ReplyKeyboardMarkup(buttons, resize_keyboard=True),
    )
    return REDEEM_MEMBER


async def redeem_member(update: Update, context: CallbackContext) -> int:
    data = load_data()
    penalties = data.get("penalties", {}) or {}
    members = data.get("members", []) or []
    chosen = update.message.text.strip()

    matched = _match_member_name(members, chosen)
    if not matched:
        await update.message.reply_text(
            "Member not found. Please pick a name from the keyboard."
        )
        return REDEEM_MEMBER

    member_name = _get_member_name(matched)
    owed = penalties.get(member_name, 0)
    if owed <= 0:
        await update.message.reply_text(
            f"{member_name} has no beers to redeem.", reply_markup=get_penalties_keyboard()
        )
        return ConversationHandler.END

    context.user_data["redeem_member"] = member_name
    context.user_data["redeem_max"] = owed
    await update.message.reply_text(
        f"{member_name} currently owes {owed} beer(s).\n"
        f"How many beers are being redeemed? (1–{owed})",
        reply_markup=ReplyKeyboardMarkup([[KeyboardButton("Cancel")]], resize_keyboard=True),
    )
    return REDEEM_COUNT


async def redeem_count(update: Update, context: CallbackContext) -> int:
    text = update.message.text.strip()
    member_name = context.user_data.get("redeem_member")
    max_count = context.user_data.get("redeem_max", 0)

    try:
        count = int(text)
    except ValueError:
        await update.message.reply_text(
            f"Please enter a whole number between 1 and {max_count}."
        )
        return REDEEM_COUNT

    if count < 1 or count > max_count:
        await update.message.reply_text(
            f"Please enter a number between 1 and {max_count}."
        )
        return REDEEM_COUNT

    data = load_data()
    penalties = data.get("penalties", {}) or {}
    new_count = penalties.get(member_name, 0) - count
    if new_count <= 0:
        penalties.pop(member_name, None)
    else:
        penalties[member_name] = new_count
    data["penalties"] = penalties
    save_data(data)

    remaining = max(0, max_count - count)
    msg = (
        f"Redeemed {count} beer(s) for {member_name}. "
        + (f"{remaining} still owed." if remaining else "All beers redeemed!")
    )
    context.user_data.clear()
    await update.message.reply_text(msg, reply_markup=get_penalties_keyboard())
    return ConversationHandler.END


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
        if "group_chat_id" in data:
            await update.message.reply_text(
                "Weekly reports are set to be sent to a group chat. To change the group, use this command in the new group chat."
            )
        else:
            await update.message.reply_text(
                "Please use this command in the group chat where you want the weekly reports to be sent."
            )


def _chore_entries_this_week(chore_log):
    tz = pytz.timezone("Europe/Berlin")
    cutoff = datetime.now(tz) - timedelta(days=7)
    result = []
    for entry in chore_log or []:
        try:
            ts = datetime.fromisoformat(entry["timestamp"])
            if ts.tzinfo is None:
                ts = tz.localize(ts)
        except (KeyError, ValueError):
            continue
        if ts >= cutoff:
            result.append(entry)
    return result


def _expenses_in_period(expenses, days=28):
    cutoff = datetime.now() - timedelta(days=days)
    result = []
    for e in expenses or []:
        try:
            d = datetime.strptime(e["date"], "%Y-%m-%d")
        except (KeyError, ValueError):
            continue
        if d >= cutoff:
            result.append(e)
    return result


def _build_expense_fun_facts(expenses):
    recent = _expenses_in_period(expenses, days=28)
    if not recent:
        return None

    payer_count = {}
    payer_total = {}
    for e in recent:
        payer = e.get("payer", "?")
        amount = float(e.get("amount", 0))
        payer_count[payer] = payer_count.get(payer, 0) + 1
        payer_total[payer] = payer_total.get(payer, 0) + amount

    total_spend = sum(payer_total.values())
    total_entries = sum(payer_count.values())

    lines = [f"Expense snapshot (last 28 days): €{total_spend:.2f} across {total_entries} entries"]

    if len(payer_count) >= 2:
        most_frequent = max(payer_count, key=payer_count.get)
        lines.append(f"  • {most_frequent} paid most often ({payer_count[most_frequent]}x)")

        avg_per = {p: payer_total[p] / payer_count[p] for p in payer_count}
        highest = max(avg_per, key=avg_per.get)
        lowest = min(avg_per, key=avg_per.get)
        if highest != lowest:
            lines.append(
                f"  • Avg per entry: {highest} €{avg_per[highest]:.2f} vs {lowest} €{avg_per[lowest]:.2f}"
            )

        top_payer = max(payer_total, key=payer_total.get)
        share_pct = payer_total[top_payer] / total_spend * 100 if total_spend else 0
        if share_pct >= 40:
            lines.append(
                f"  • {top_payer} covered {share_pct:.0f}% of total spending"
            )
    elif len(payer_count) == 1:
        sole = next(iter(payer_count))
        lines.append(f"  • {sole} paid for everything this month – someone owes them!")

    return "\n".join(lines)


def _build_weekly_report(data):
    """Build the weekly report text, mutating data to apply penalty changes."""
    chores_normalized = {}
    for chore_user, points in (data.get("chores") or {}).items():
        for member in data.get("members") or []:
            member_name = _get_member_name(member)
            if _normalise_member_name(member_name) == _normalise_member_name(chore_user):
                chores_normalized[member_name] = points
                break

    member_names = [_get_member_name(m) for m in (data.get("members") or [])]
    leaderboard = sorted(
        [(name, chores_normalized.get(name, 0)) for name in member_names],
        key=lambda x: -x[1],
    )

    if not leaderboard:
        return "Mario's Monday Mauling: No data yet."

    leader, leader_points = leaderboard[0]
    current_date = datetime.now().strftime("%Y-%m-%d")
    sections = [f"Mario's Monday Mauling, {current_date}", f"Leader: {leader} with {leader_points} points"]

    # --- Standings & penalties ---
    penalty_lines = []
    comeback_kids = []
    currently_penalised = set()
    for member, points in leaderboard[1:]:
        if leader_points - points > 4:
            currently_penalised.add(member)
            last_week_violator = data.get("last_week_violators", {}).get(
                _normalise_member_name(member), False
            )
            if last_week_violator:
                weeks_lagging = data.setdefault("penalties", {}).get(member, 0) + 1
                data["penalties"][member] = weeks_lagging
                penalty_lines.append(f"  • {member} owes {weeks_lagging} beer(s)!")
            else:
                data.setdefault("last_week_violators", {})[_normalise_member_name(member)] = True
                penalty_lines.append(
                    f"  • {member} is {leader_points - points} pts behind {leader} — shape up or beer incoming!"
                )
        elif _normalise_member_name(member) in data.get("last_week_violators", {}):
            data["last_week_violators"].pop(_normalise_member_name(member), None)
            comeback_kids.append(member)

    # Include members who caught up but still owe unredeemed beers
    penalties = data.get("penalties", {}) or {}
    for member, owed in penalties.items():
        if owed > 0 and member not in currently_penalised:
            penalty_lines.append(f"  • {member} still owes {owed} beer(s) (caught up, debt remains).")

    if penalty_lines:
        sections.append("Standings & Penalties:\n" + "\n".join(penalty_lines))
    else:
        sections.append("Everyone is keeping up — no penalties this week!")

    # --- Comeback kids ---
    if comeback_kids:
        names = ", ".join(comeback_kids)
        sections.append(f"Comeback of the week: {names} turned it around after lagging last week!")

    # --- Big moves this week: members with >1h total across all sessions ---
    week_entries = _chore_entries_this_week(data.get("chore_log") or [])
    member_week_pts = {}
    member_week_entries = {}
    for e in week_entries:
        member = e.get("member", "?")
        member_week_pts[member] = member_week_pts.get(member, 0) + e.get("points", 0)
        member_week_entries.setdefault(member, []).append(e)
    big_movers = {m: pts for m, pts in member_week_pts.items() if pts > 4}
    if big_movers:
        leap_lines = []
        for member, pts in sorted(big_movers.items(), key=lambda x: -x[1]):
            mins = pts * 15
            leap_lines.append(f"  • {member}: {mins} min total")
            descs = [
                e.get("description", "").strip()
                for e in member_week_entries[member]
                if e.get("description", "").strip()
            ]
            if descs:
                leap_lines.append(f"  ({', '.join(descs)})")
        sections.append("Big moves this week:\n" + "\n".join(leap_lines))

    # --- Expense fun facts ---
    fun_facts = _build_expense_fun_facts(data.get("expenses") or [])
    if fun_facts:
        sections.append(fun_facts)

    # --- WG-Höck reminder ---
    hoeck_date_str = data.get("wg_hoeck_date")
    if hoeck_date_str:
        try:
            hoeck_date = datetime.strptime(hoeck_date_str, "%Y-%m-%d").date()
            today = datetime.now(pytz.timezone("Europe/Berlin")).date()
            days_until = (hoeck_date - today).days
            if 0 <= days_until <= 7:
                day_name = hoeck_date.strftime("%A, %d.%m.")
                if days_until == 0:
                    sections.append(f"📅 WG-Höck is TODAY!")
                else:
                    sections.append(f"📅 WG-Höck this week: {day_name} ({days_until} day{'s' if days_until != 1 else ''} away)")
        except ValueError:
            pass

    return "\n\n".join(sections)


async def check_weekly_penalties(context: CallbackContext) -> None:
    data = load_data()

    if "group_chat_id" not in data:
        logger.warning("No group chat ID set for weekly reports")
        return

    group_id = data["group_chat_id"]

    if not data.get("members") or not data.get("chores"):
        try:
            await context.bot.send_message(
                chat_id=group_id,
                text="Weekly Report: Not enough data yet. Add members and log some chores first.",
            )
        except TelegramError as e:
            logger.error(f"Failed to send weekly report: {e}")
        return

    report = _build_weekly_report(data)
    save_data(data)

    try:
        await context.bot.send_message(chat_id=group_id, text=report)
    except TelegramError as e:
        logger.error(f"Failed to send weekly report: {e}")


async def admin_trigger_report(update: Update, context: CallbackContext) -> None:
    if update.effective_user.id != BOT_HANDLER_ID:
        await update.message.reply_text("Unauthorized.")
        return

    data = load_data()
    if not data.get("members") or not data.get("chores"):
        await update.message.reply_text(
            "Not enough data yet. Add members and log some chores first.",
            reply_markup=get_admin_keyboard(),
        )
        return

    dry_data = copy.deepcopy(data)
    report = _build_weekly_report(dry_data)

    await update.message.reply_text(
        f"[DRY RUN — not sent to group, no data changed]\n\n{report}",
        reply_markup=get_admin_keyboard(),
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


def _get_chronicler_meta(data):
    return data.setdefault(
        "chronicler_backup",
        {"greeting_sent": False, "last_sent": None},
    )


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


def setup_weekly_job(application):
    target_time = datetime.now(pytz.timezone("Europe/Berlin"))
    target_time = target_time.replace(hour=9, minute=0, second=0, microsecond=0)

    if target_time.weekday() != 0 or datetime.now(pytz.timezone("Europe/Berlin")) > target_time:
        days_until_monday = (7 - target_time.weekday()) % 7
        if days_until_monday == 0:
            days_until_monday = 7
        target_time = target_time + timedelta(days=days_until_monday)

    current_time = datetime.now(pytz.timezone("Europe/Berlin"))
    seconds_until_target = (target_time - current_time).total_seconds()

    application.job_queue.run_repeating(
        check_weekly_penalties,
        interval=timedelta(days=7).total_seconds(),
        first=seconds_until_target,
        name="weekly_penalty_check",
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


async def cancel(update: Update, context: CallbackContext) -> int:
    context.user_data.clear()
    await update.message.reply_text(
        "Cancelled. Back to main menu.", reply_markup=get_main_keyboard()
    )
    return ConversationHandler.END


async def start_change_username(update: Update, context: CallbackContext) -> int:
    member = _resolve_member_for_user(update.effective_user)
    if not member:
        await update.message.reply_text(
            "I can't match you to a household member.",
            reply_markup=get_manage_self_keyboard(),
        )
        return ConversationHandler.END
    current_name = _get_member_name(member)
    context.user_data["change_username_old"] = current_name
    await update.message.reply_text(
        f"Current username: {current_name}\n\nSend your new username:",
        reply_markup=ReplyKeyboardMarkup([[KeyboardButton("Cancel")]], resize_keyboard=True),
    )
    return CHANGE_USERNAME_NEW


async def do_change_username(update: Update, context: CallbackContext) -> int:
    new_name = update.message.text.strip()
    old_name = context.user_data.get("change_username_old", "")
    data = load_data()

    if _match_member_name(data.get("members") or [], new_name):
        await update.message.reply_text(
            f"'{new_name}' is already taken. Try a different name:"
        )
        return CHANGE_USERNAME_NEW

    _rename_member_in_data(data, old_name, new_name)
    save_data(data)
    context.user_data.clear()

    await update.message.reply_text(
        f"Username changed to {new_name}!",
        reply_markup=get_manage_self_keyboard(),
    )
    return ConversationHandler.END


async def set_vacation_status(update: Update, context: CallbackContext) -> None:
    """Toggle vacation status. Usage: /setstatus or /setstatus <name>"""
    data = load_data()
    members = data.get("members", []) or []
    
    # Check if a name was provided as argument
    if context.args:
        # Admin mode: set status for specified member
        member_name_arg = " ".join(context.args)
        member_match = _match_member_name(members, member_name_arg)
        if not member_match:
            await update.message.reply_text(
                f"Member '{member_name_arg}' not found."
            )
            return
    else:
        # Auto-identify caller by Chat ID
        member_uid = _resolve_member_for_user(update.effective_user)
        if not member_uid:
            await update.message.reply_text(
                "I cannot match you to a household member. Please ask to be added to the member list."
            )
            return
        
        # Get the member name from the resolved UID
        member_name_arg = _get_member_name(member_uid)
        member_match = _match_member_name(members, member_name_arg)
        if not member_match:
            await update.message.reply_text(
                "I cannot match you to a household member. Please ask to be added to the member list."
            )
            return
    
    member_name = _get_member_name(member_match)
    current_status = _get_member_status(member_match)
    new_status = "vacating" if current_status == "active" else "active"
    
    # Update the member in the list
    for member in members:
        if _normalise_member_name(member) == _normalise_member_name(member_match):
            if isinstance(member, dict):
                member["status"] = new_status
            else:
                # Convert old string format to dict
                idx = members.index(member)
                members[idx] = {"name": member, "status": new_status}
            break
    
    save_data(data)

    # Return to manage-self submenu when button-triggered; main menu for /setstatus commands
    reply_kb = get_main_keyboard() if context.args else get_manage_self_keyboard()

    if new_status == "vacating":
        await update.message.reply_text(
            f"{member_name}, you are now on vacation. "
            f"You will appear at the bottom when selecting splitters, "
            f"to remind others to only include you for long-term expenses.",
            reply_markup=reply_kb,
        )
    else:
        await update.message.reply_text(
            f"{member_name}, you are now active.",
            reply_markup=reply_kb,
        )


async def on_timeout(update: Update, context: CallbackContext) -> int:
    context.user_data.clear()
    chat = update.effective_chat
    if chat:
        await context.bot.send_message(
            chat_id=chat.id,
            text="Session timed out. Back to main menu.",
            reply_markup=get_main_keyboard(),
        )
    return ConversationHandler.END


def main():
    data = load_data()
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("expenses", list_expenses))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CommandHandler("chore", handle_chore))
    app.add_handler(CommandHandler("chores", list_chores))
    app.add_handler(CommandHandler("setstatus", set_vacation_status))

    app.add_handler(MessageHandler(filters.Regex("^Standings$"), standings))
    app.add_handler(MessageHandler(filters.Regex("^List Expenses$"), list_expenses))
    app.add_handler(MessageHandler(filters.Regex("^List Chores$"), list_chores))
    app.add_handler(MessageHandler(filters.Regex("^Penalties$"), open_penalties))
    app.add_handler(MessageHandler(filters.Regex("^Check Beer Owed$"), beer_owed))
    app.add_handler(MessageHandler(filters.Regex("^Set Weekly Report$"), set_weekly_report))
    app.add_handler(MessageHandler(filters.Regex("^Set Vacation Status$"), set_vacation_status))
    app.add_handler(MessageHandler(filters.Regex("^Settings$"), open_settings))
    app.add_handler(MessageHandler(filters.Regex("^Back to Main Menu$"), settings_back))
    app.add_handler(MessageHandler(filters.Regex("^Admin Panel$"), open_admin_menu))
    app.add_handler(MessageHandler(filters.Regex("^Back to Settings$"), back_to_settings))
    app.add_handler(MessageHandler(filters.Regex("^Trigger Weekly Report$"), admin_trigger_report))
    app.add_handler(MessageHandler(filters.Regex("^Manage "), open_manage_self))
    app.add_handler(MessageHandler(filters.Regex("^Cancel$"), cancel))

    admin_beer_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^Adjust Beer Count$"), admin_beer_start)],
        states={
            ADMIN_BEER_MEMBER: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_beer_member)
            ],
            ADMIN_BEER_COUNT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_beer_count)
            ],
            ConversationHandler.TIMEOUT: [
                MessageHandler(filters.ALL, on_timeout)
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            MessageHandler(filters.Regex("^Cancel$"), cancel),
        ],
        allow_reentry=True,
        conversation_timeout=300,
    )
    app.add_handler(admin_beer_conv)

    admin_hoeck_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(r"^Set WG-Höck$"), admin_hoeck_start)],
        states={
            0: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_hoeck_date)],
            ConversationHandler.TIMEOUT: [
                MessageHandler(filters.ALL, on_timeout)
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            MessageHandler(filters.Regex("^Cancel$"), cancel),
        ],
        allow_reentry=True,
        conversation_timeout=300,
    )
    app.add_handler(admin_hoeck_conv)

    expense_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^Add Expense$"), start_expense)],
        states={
            EXPENSE_MODE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, expense_mode_selection)
            ],
            EXPENSE_DESCRIPTION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, expense_description)
            ],
            EXPENSE_AMOUNT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, expense_amount)
            ],
            EXPENSE_RECEIPT: [
                MessageHandler(RECEIPT_IMAGE_FILTER, expense_receipt_photo),
                MessageHandler(filters.Regex("^Cancel$"), cancel),
                MessageHandler(filters.TEXT & ~filters.COMMAND, expense_receipt_invalid),
            ],
            EXPENSE_RECEIPT_CONFIRM_TOTAL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, expense_receipt_confirm_total),
            ],
            EXPENSE_RECEIPT_MANUAL: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND, expense_receipt_manual_items
                )
            ],
            EXPENSE_RECEIPT_REVIEW: [
                CallbackQueryHandler(
                    receipt_items_cb,
                    pattern=r"^(?:receipt_toggle:.*|receipt_done|receipt_cancel)$",
                )
            ],
            EXPENSE_PAYER: [
                CallbackQueryHandler(expense_payer_cb, pattern=f"^{CB_PAYER_PREFIX}")
            ],
            EXPENSE_SPLIT: [
                CallbackQueryHandler(
                    expense_split_cb,
                    pattern=r"^(?:split_toggle:.*|split_done|split_back|split_cancel)$",
                )
            ],
            ConversationHandler.TIMEOUT: [
                MessageHandler(filters.ALL, on_timeout)
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            MessageHandler(filters.Regex("^Cancel$"), cancel),
        ],
        conversation_timeout=300,
    )
    app.add_handler(expense_conv)

    manage_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^Member Management$"), manage_members)],
        states={
            MANAGE_MEMBER: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, modify_members)
            ],
            ConversationHandler.TIMEOUT: [
                MessageHandler(filters.ALL, on_timeout)
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            MessageHandler(filters.Regex("^Cancel$"), cancel),
        ],
        conversation_timeout=300,
    )
    app.add_handler(manage_conv)

    change_username_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^Change Username$"), start_change_username)],
        states={
            CHANGE_USERNAME_NEW: [
                MessageHandler(filters.Regex("^Cancel$"), cancel),
                MessageHandler(filters.TEXT & ~filters.COMMAND, do_change_username),
            ],
            ConversationHandler.TIMEOUT: [
                MessageHandler(filters.ALL, on_timeout)
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            MessageHandler(filters.Regex("^Cancel$"), cancel),
        ],
        allow_reentry=True,
        conversation_timeout=300,
    )
    app.add_handler(change_username_conv)

    edit_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^Edit Entries$"), start_edit_entries)],
        states={
            EDIT_PICK_MEMBER: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, edit_entries_pick_member)
            ],
            EDIT_MENU: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, edit_entries_menu)
            ],
            EDIT_AMOUNT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, edit_entries_amount)
            ],
            EDIT_SPLIT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, edit_entries_split)
            ],
            ConversationHandler.TIMEOUT: [
                MessageHandler(filters.ALL, on_timeout)
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            MessageHandler(filters.Regex("^Cancel$"), cancel),
        ],
        conversation_timeout=300,
    )
    app.add_handler(edit_conv)

    _nav_pattern = filters.Regex(
        "^(Penalties|Check Beer Owed|Back to Main Menu|Settings|"
        "Add Expense|Add Chore|Standings|List Expenses|List Chores|"
        "Manage Members)$"
    )
    redeem_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^Redeem Beer$"), redeem_start)],
        states={
            REDEEM_MEMBER: [
                MessageHandler(_nav_pattern, cancel),
                MessageHandler(filters.TEXT & ~filters.COMMAND, redeem_member),
            ],
            REDEEM_COUNT: [
                MessageHandler(_nav_pattern, cancel),
                MessageHandler(filters.TEXT & ~filters.COMMAND, redeem_count),
            ],
            ConversationHandler.TIMEOUT: [
                MessageHandler(filters.ALL, on_timeout)
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            MessageHandler(filters.Regex("^Cancel$"), cancel),
        ],
        allow_reentry=True,
        conversation_timeout=300,
    )
    app.add_handler(redeem_conv)

    chore_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^Add Chore$"), start_chore)],
        states={
            CHORE_USER: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, chore_user)
            ],
            CHORE_MINUTES: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, chore_minutes)
            ],
            CHORE_DESCRIPTION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, chore_description)
            ],
            ConversationHandler.TIMEOUT: [
                MessageHandler(filters.ALL, on_timeout)
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            MessageHandler(filters.Regex("^Cancel$"), cancel),
        ],
        conversation_timeout=300,
    )

    app.add_handler(chore_conv)

    # Catch stale receipt inline-keyboard callbacks (e.g. after a bot restart)
    async def _stale_receipt_cb(update: Update, context: CallbackContext) -> None:
        query = update.callback_query
        await query.answer()
        await query.message.reply_text(
            "This receipt session has expired (the bot may have restarted).\n"
            "Please start a new expense via Add Expense → Scan Receipt.",
            reply_markup=ReplyKeyboardRemove(),
        )

    app.add_handler(
        CallbackQueryHandler(
            _stale_receipt_cb,
            pattern=r"^(?:receipt_toggle:.*|receipt_done|receipt_cancel)$",
        )
    )

    setup_weekly_job(app)
    setup_chronicler_backup_job(app)

    # Daily noon check for WG-Höck reminder
    tz = pytz.timezone("Europe/Berlin")
    now = datetime.now(tz)
    noon_today = now.replace(hour=12, minute=0, second=0, microsecond=0)
    if now >= noon_today:
        noon_today += timedelta(days=1)
    seconds_until_noon = (noon_today - now).total_seconds()
    app.job_queue.run_repeating(
        send_hoeck_reminder,
        interval=timedelta(days=1).total_seconds(),
        first=seconds_until_noon,
        name="hoeck_reminder",
    )
    logger.info(f"Höck reminder scheduled, next check at {noon_today.strftime('%Y-%m-%d %H:%M')}")

    app.job_queue.run_repeating(
        send_alive,
        interval=timedelta(hours=50).total_seconds(),
        first=0,
        name="heartbeat",
    )
    logger.info("Bot running...")
    app.run_polling()


if __name__ == "__main__":
    main()
