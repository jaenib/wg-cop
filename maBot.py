import json
import logging
import os
import re
import tempfile
import pytz
from datetime import datetime, timedelta

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
    from PIL import Image
except ImportError:  # pragma: no cover - optional dependency
    Image = None

try:
    import pytesseract
except ImportError:  # pragma: no cover - optional dependency
    pytesseract = None

# Set up logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)

# Bot Token
from config import TOKEN
from config import GROUP_CHAT_ID
from config import BOT_HANDLER_ID
from config import CHRONICLER_ID

# Data storage
DATA_FILE = "wg_data_beta.json"


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
        save_data(data)

    return data


def save_data(data):
    with open(DATA_FILE, "w") as file:
        json.dump(data, file, indent=4)


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
) = range(8)
CHORE_USER, CHORE_MINUTES = range(2)
MANAGE_MEMBER = range(1)

RECEIPT_IMAGE_FILTER = filters.PHOTO | filters.Document.IMAGE


# Dynamic Keyboards
def get_main_keyboard():
    return ReplyKeyboardMarkup(
        [
            [
                KeyboardButton("Add Expense"),
                KeyboardButton("Add Chore"),
                KeyboardButton("List Expenses"),
            ],
            [
                KeyboardButton("Standings"),
                KeyboardButton("Check Beer Owed"),
                KeyboardButton("Manage Members"),
            ],
            [KeyboardButton("Set Weekly Report"), KeyboardButton("Cancel")],
        ],
        resize_keyboard=True,
    )


def get_member_keyboard(data):
    members = data.get("members", [])
    if not members:
        return None
    buttons = [[KeyboardButton(member)] for member in members]
    buttons.append([KeyboardButton("Done")])
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)


def _truncate_button_label(label: str, limit: int = 60) -> str:
    if len(label) <= limit:
        return label
    return label[: limit - 1] + "…"


def build_payer_inline_kb(members):
    rows = [[InlineKeyboardButton(m, callback_data=f"{CB_PAYER_PREFIX}{m}")] for m in members]
    return InlineKeyboardMarkup(rows)


def build_split_inline_kb(members, selected):
    rows = []
    for m in members:
        picked = m in selected
        label = f"{'✅ ' if picked else ''}{m}"
        rows.append(
            [InlineKeyboardButton(label, callback_data=f"{CB_SPLIT_TOGGLE_PREFIX}{m}")]
        )
    rows.append(
        [
            InlineKeyboardButton("⬅️ Back", callback_data=CB_SPLIT_BACK),
            InlineKeyboardButton("✅ Done", callback_data=CB_SPLIT_DONE),
            InlineKeyboardButton("✖️ Cancel", callback_data=CB_SPLIT_CANCEL),
        ]
    )
    return InlineKeyboardMarkup(rows)


def build_receipt_items_text(items, selected):
    lines = ["Toggle items to exclude them from the shared expense:"]
    total = 0.0
    for idx, item in enumerate(items):
        picked = idx in selected
        marker = "✅" if picked else "🚫"
        lines.append(
            f"{marker} {item['name']} — {item['amount']:.2f}"
        )
        if picked:
            total += item["amount"]
    lines.append("")
    lines.append(f"Current shared total: {total:.2f}")
    return "\n".join(lines)


def build_receipt_items_kb(items, selected):
    rows = []
    for idx, item in enumerate(items):
        picked = idx in selected
        marker = "✅" if picked else "🚫"
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
            InlineKeyboardButton("✅ Done", callback_data=CB_RECEIPT_DONE),
            InlineKeyboardButton("✖️ Cancel", callback_data=CB_RECEIPT_CANCEL),
        ]
    )
    return InlineKeyboardMarkup(rows)


def _parse_line_item(line: str):
    clean = line.strip()
    if not clean:
        return None
    match = re.search(r"(-?\d+[.,]\d{1,2})", clean)
    if not match:
        return None
    amount_raw = match.group(1)
    try:
        amount = round(float(amount_raw.replace(",", ".")), 2)
    except ValueError:
        return None
    name = clean[: match.start()].strip(" :-–—\t")
    if not name:
        name = "Item"
    return {"name": name, "amount": amount}


def parse_receipt_text(text: str):
    items = []
    for line in text.splitlines():
        parsed = _parse_line_item(line)
        if parsed:
            items.append(parsed)
    return items


def extract_items_from_receipt(image_path: str):
    if not pytesseract or not Image:
        raise ReceiptParsingError("OCR dependencies are not installed.")
    try:
        with Image.open(image_path) as img:
            text = pytesseract.image_to_string(img)
    except Exception as exc:  # pragma: no cover - depends on runtime env
        raise ReceiptParsingError("Failed to read the receipt image.") from exc

    items = parse_receipt_text(text)
    if not items:
        raise ReceiptParsingError("No line items recognised in the receipt.")
    return items


async def start(update: Update, context: CallbackContext) -> None:
    await update.message.reply_text(
        "WG Bot is active! Use the buttons below:", reply_markup=get_main_keyboard()
    )


# Manage Members
async def manage_members(update: Update, context: CallbackContext) -> int:
    data = load_data()
    if data["members"]:
        members_list = ", ".join(data["members"])
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
        (i for i, m in enumerate(data["members"]) if m.lower() == name_ci), None
    )
    if existing_index is not None:
        removed = data["members"].pop(existing_index)
        response = f"Removed {removed} from the household."
    else:
        data["members"].append(text)
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

    if lowered == "scan receipt":
        context.user_data["mode"] = "receipt"
        await update.message.reply_text(
            "Please send a photo or image of the receipt. I'll try to read the line items.",
            reply_markup=ReplyKeyboardRemove(),
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
    if update.message.photo:
        telegram_file = await update.message.photo[-1].get_file()
    elif update.message.document:
        telegram_file = await update.message.document.get_file()

    if not telegram_file:
        await update.message.reply_text(
            "Please send a photo or image of the receipt, or type Cancel to abort."
        )
        return EXPENSE_RECEIPT

    tmp_path = None
    items = []
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp:
            tmp_path = tmp.name
        await telegram_file.download_to_drive(tmp_path)

        try:
            items = extract_items_from_receipt(tmp_path)
        except ReceiptParsingError as exc:
            logger.info("Receipt OCR failed: %s", exc)
            await update.message.reply_text(
                "I couldn't read the receipt automatically."
                "\nPlease send the items as text in the format 'Item - price',"
                " one per line."
            )
            return EXPENSE_RECEIPT_MANUAL

    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)

    if not items:
        await update.message.reply_text(
            "I couldn't find any purchasable items. Please send them as text, one per line."
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

    if query.data == CB_RECEIPT_DONE:
        if not selected:
            await query.answer("Select at least one item.", show_alert=True)
            return EXPENSE_RECEIPT_REVIEW

        chosen = [items[i] for i in sorted(selected)]
        total = round(sum(item["amount"] for item in chosen), 2)
        context.user_data["selected_items"] = chosen
        context.user_data["amount"] = total

        lines = ["Selected items:"]
        for item in chosen:
            lines.append(f"• {item['name']} — {item['amount']:.2f}")
        lines.append("")
        lines.append(f"Shared subtotal: {total:.2f}")

        await query.edit_message_text("\n".join(lines))
        await query.message.reply_text(
            "Enter a short description for these items:",
            reply_markup=ReplyKeyboardRemove(),
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
            "Select who shares the expense (toggle). Then press ✅ Done."
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
        selected = list(context.user_data.get("split_with", []))
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

        await query.edit_message_text(
            f"Added expense: {today} — {desc} — {amount:.2f}€\n"
            f"Payer: {payer}\nSplit with: {', '.join(selected)}"
        )
        await query.message.reply_text("Done ✅", reply_markup=get_main_keyboard())
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
    context.user_data["user"] = update.message.text.strip()
    await update.message.reply_text(
        "How many minutes did it take?", reply_markup=ReplyKeyboardRemove()
    )
    return CHORE_MINUTES


async def chore_minutes(update: Update, context: CallbackContext) -> int:
    data = load_data()
    try:
        minutes = int(update.message.text)
        points = minutes // 15
        user = context.user_data["user"]
        data["chores"][user] = data["chores"].get(user, 0) + points
        save_data(data)
        await update.message.reply_text(
            f"{user} earned {points} points!", reply_markup=get_main_keyboard()
        )
        return ConversationHandler.END
    except ValueError:
        await update.message.reply_text(
            "Invalid input. Enter the minutes again."
        )
        return CHORE_MINUTES


# Show latest logged expenses
async def list_expenses(update: Update, context: CallbackContext) -> None:
    data = load_data()
    if not data.get("expenses"):
        await update.message.reply_text(
            "No expenses recorded yet.", reply_markup=get_main_keyboard()
        )
        return
    items = data["expenses"][-EXPENSE_LIST_LIMIT:][::-1]
    lines = []
    for e in items:
        date = e.get("date", "?")
        desc = e.get("description", "(no description)")
        amt = e.get("amount", 0.0)
        payer = e.get("payer", "?")
        split = ", ".join(e.get("split_with", [])) or "-"
        base = f"{date} — {desc} — {amt:.2f}€ | Payer: {payer} | Split: {split}"
        if e.get("items"):
            item_summary = ", ".join(
                f"{item.get('name', 'Item')} ({float(item.get('amount', 0.0)):.2f})"
                for item in e.get("items", [])
            )
            base += f" | Items: {item_summary}"
        lines.append(base)
    text = "Recent Expenses:\n" + "\n".join(lines)
    await update.message.reply_text(text, reply_markup=get_main_keyboard())


# Calculate + show standings
async def standings(update: Update, context: CallbackContext) -> None:
    data = load_data()
    members = data.get("members", [])
    if not members:
        await update.message.reply_text(
            "No members recorded yet.", reply_markup=get_main_keyboard()
        )
        return

    balances = {m: 0.0 for m in members}

    for expense in data.get("expenses", []):
        payer = expense.get("payer", "")
        amount = float(expense.get("amount", 0.0))
        split_with = expense.get("split_with", []) or []
        if not split_with:
            continue
        share = amount / len(split_with)

        payer_key = next((m for m in members if m.lower() == payer.lower()), None)
        if payer_key:
            balances[payer_key] = balances.get(payer_key, 0.0) + amount

        for u in split_with:
            u_key = next((m for m in members if m.lower() == u.lower()), None)
            if u_key:
                balances[u_key] = balances.get(u_key, 0.0) - share

    chores = {}
    for name, pts in (data.get("chores", {}) or {}).items():
        mkey = next((m for m in members if m.lower() == name.lower()), None)
        if mkey:
            chores[mkey] = pts

    ordered = sorted(members, key=lambda m: (chores.get(m, 0)), reverse=True)

    lines = []
    for m in ordered:
        points = chores.get(m, 0)
        bal = balances.get(m, 0.0)
        lines.append(f"{m}: {points} points, {bal:+.2f}€")

    await update.message.reply_text("\n".join(lines), reply_markup=get_main_keyboard())


# Beer owed
async def beer_owed(update: Update, context: CallbackContext) -> None:
    data = load_data()
    leaderboard = sorted(data["chores"].items(), key=lambda x: -x[1])
    if not leaderboard:
        await update.message.reply_text("No chores recorded yet.")
        return

    leader_points = leaderboard[0][1]
    violators = []

    for user, points in leaderboard[1:]:
        if leader_points - points > 4:
            weeks_lagging = data["penalties"].get(user, 0) + 1
            data["penalties"][user] = weeks_lagging
            violators.append(f"{user} owes {weeks_lagging} beers!")

    save_data(data)
    if violators:
        await update.message.reply_text(
            "Beer Penalties:\n" + "\n".join(violators)
        )
    else:
        await update.message.reply_text("No penalties this week!")


# Weekly report handling
async def set_weekly_report(update: Update, context: CallbackContext) -> None:
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


async def check_weekly_penalties(context: CallbackContext) -> None:
    data = load_data()

    if "group_chat_id" not in data:
        logger.warning("No group chat ID set for weekly reports")
        return

    group_id = data["group_chat_id"]

    if not data["members"] or not data["chores"]:
        try:
            await context.bot.send_message(
                chat_id=group_id,
                text="Weekly Report: Not enough data to calculate penalties. Make sure members are added and chores are recorded."
            )
        except TelegramError as e:
            logger.error(f"Failed to send weekly report: {e}")
        return

    chores_normalized = {}
    for chore_user, points in data["chores"].items():
        for member in data["members"]:
            if member.lower() == chore_user.lower():
                chores_normalized[member] = points
                break

    leaderboard = sorted(
        [(member, chores_normalized.get(member, 0)) for member in data["members"]],
        key=lambda x: -x[1],
    )

    if not leaderboard:
        return

    leader, leader_points = leaderboard[0]
    violators = []

    for member, points in leaderboard[1:]:
        if leader_points - points > 4:
            last_week_violator = data.get("last_week_violators", {}).get(
                member.lower(), False
            )
            if last_week_violator:
                weeks_lagging = data["penalties"].get(member, 0) + 1
                data["penalties"][member] = weeks_lagging
                violators.append(f"{member} owes {weeks_lagging} beers! 🍺")
            else:
                if "last_week_violators" not in data:
                    data["last_week_violators"] = {}
                data["last_week_violators"][member.lower()] = True
                violators.append(
                    f"{member} is lagging by {leader_points - points} points behind {leader}. If not improved by next week, beer penalty will apply! ⚠️"
                )
        elif member.lower() in data.get("last_week_violators", {}):
            data["last_week_violators"].pop(member.lower(), None)
            violators.append(
                f"{member} has improved their standing! No beer penalty this week. 👍"
            )

    save_data(data)

    current_date = datetime.now().strftime("%Y-%m-%d")
    if violators:
        report = f"Weekly Chore Report ({current_date}):\n\n"
        report += f"Leader: {leader} with {leader_points} points\n\n"
        report += "Penalties:\n" + "\n".join(violators)
    else:
        report = f"Weekly Chore Report ({current_date}):\n\n"
        report += f"Leader: {leader} with {leader_points} points\n\n"
        report += "Everyone is keeping up with their chores! No penalties this week. 🎉"

    try:
        await context.bot.send_message(chat_id=group_id, text=report)
    except TelegramError as e:
        logger.error(f"Failed to send weekly report: {e}")


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

    try:
        with open(DATA_FILE, "rb") as doc:
            await context.bot.send_document(
                chat_id=chronicler_chat_id,
                document=doc,
                filename=DATA_FILE,
                caption="Weekly archive backup",
            )
    except (FileNotFoundError, TelegramError) as e:
        logger.error(f"Failed to send chronicler backup: {e}")
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
    try:
        await context.bot.send_message(chat_id=BOT_HANDLER_ID, text="I'm alive")
    except TelegramError as e:
        logger.error(f"Failed to send heartbeat: {e}")


async def cancel(update: Update, context: CallbackContext) -> int:
    context.user_data.clear()
    await update.message.reply_text(
        "Cancelled. Back to main menu.", reply_markup=get_main_keyboard()
    )
    return ConversationHandler.END


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

    app.add_handler(MessageHandler(filters.Regex("^Standings$"), standings))
    app.add_handler(MessageHandler(filters.Regex("^List Expenses$"), list_expenses))
    app.add_handler(MessageHandler(filters.Regex("^Check Beer Owed$"), beer_owed))
    app.add_handler(MessageHandler(filters.Regex("^Set Weekly Report$"), set_weekly_report))
    app.add_handler(MessageHandler(filters.Regex("^Cancel$"), cancel))

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
                MessageHandler(filters.TEXT & ~filters.COMMAND, expense_receipt_invalid),
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
        entry_points=[MessageHandler(filters.Regex("^Manage Members$"), manage_members)],
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

    chore_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^Add Chore$"), start_chore)],
        states={
            CHORE_USER: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, chore_user)
            ],
            CHORE_MINUTES: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, chore_minutes)
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

    setup_weekly_job(app)
    setup_chronicler_backup_job(app)
    app.job_queue.run_repeating(
        send_alive,
        interval=timedelta(hours=4).total_seconds(),
        first=0,
        name="heartbeat",
    )
    logger.info("Bot running...")
    app.run_polling()


if __name__ == "__main__":
    main()
