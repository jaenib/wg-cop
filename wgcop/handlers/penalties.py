"""Beer penalties view and redemption conversation (relocated verbatim)."""
from telegram import KeyboardButton, ReplyKeyboardMarkup, Update
from telegram.ext import CallbackContext, ConversationHandler

from wgcop.keyboards import REDEEM_COUNT, REDEEM_MEMBER, get_penalties_keyboard
from wgcop.members import _get_member_name, _match_member_name, _normalise_member_name
from wgcop.store import load_data, save_data

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
