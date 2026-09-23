"""Penalty overview and redemption conversation."""
from telegram import KeyboardButton, ReplyKeyboardMarkup, Update
from telegram.ext import CallbackContext, ConversationHandler

from wgcop.handlers.common import cancel, continue_flow
from wgcop.homes import end_flow, require_home, start_flow
from wgcop.keyboards import REDEEM_COUNT, REDEEM_MEMBER, get_cancel_keyboard, get_penalties_keyboard
from wgcop.members import _match_member_name, chore_points_by_member
from wgcop.reports import penalty_threshold, penalty_unit
from wgcop.store import get_store


def penalty_lines(home) -> list[str]:
    chores = chore_points_by_member(home)
    if not chores:
        return []
    leaderboard = sorted(chores.items(), key=lambda item: -item[1])
    leader_name, leader_points = leaderboard[0]
    penalties = home.get("penalties", {}) or {}
    threshold = penalty_threshold(home)
    unit = penalty_unit(home)

    currently_lagging = set()
    lines = []
    for member, points in leaderboard[1:]:
        gap = leader_points - points
        if gap > threshold:
            currently_lagging.add(member)
            owed = penalties.get(member, 0)
            if owed:
                lines.append(f"{member} owes {owed} {unit}(s) ({gap} pts behind {leader_name}).")
            else:
                lines.append(f"{member} is {gap} pts behind {leader_name} (warning).")
    for member, owed in penalties.items():
        if owed > 0 and member not in currently_lagging:
            lines.append(f"{member} still owes {owed} {unit}(s) (caught up, debt remains).")
    return lines


async def check_penalties(update: Update, context: CallbackContext) -> None:
    home = await require_home(update, context)
    if home is None:
        return
    if not chore_points_by_member(home):
        await update.message.reply_text("No chores recorded yet.", reply_markup=get_penalties_keyboard())
        return
    lines = penalty_lines(home)
    unit = penalty_unit(home).capitalize()
    if lines:
        await update.message.reply_text(f"{unit} Penalties:\n" + "\n".join(lines), reply_markup=get_penalties_keyboard())
    else:
        await update.message.reply_text("No penalties this week!", reply_markup=get_penalties_keyboard())


async def redeem_start(update: Update, context: CallbackContext) -> int:
    home = await require_home(update, context)
    if home is None:
        return ConversationHandler.END
    penalties = home.get("penalties", {}) or {}
    owing = {k: v for k, v in penalties.items() if v > 0}
    if not owing:
        await update.message.reply_text("Nobody owes any penalties right now.", reply_markup=get_penalties_keyboard())
        return ConversationHandler.END

    start_flow(context, update.effective_chat.id, home)
    buttons = [[KeyboardButton(name)] for name in sorted(owing)]
    buttons.append([KeyboardButton("Cancel")])
    await update.message.reply_text(
        f"Who brought the {penalty_unit(home)}? Select a member:",
        reply_markup=ReplyKeyboardMarkup(buttons, resize_keyboard=True),
    )
    return REDEEM_MEMBER


async def redeem_member(update: Update, context: CallbackContext) -> int:
    home, flow = await continue_flow(update, context)
    if home is None:
        return ConversationHandler.END
    chosen = update.message.text.strip()
    if chosen.lower() == "cancel":
        return await cancel(update, context)
    matched = _match_member_name(home["members"], chosen)
    if not matched:
        await update.message.reply_text("Member not found. Please pick a name from the keyboard.")
        return REDEEM_MEMBER

    name = matched["name"]
    owed = (home.get("penalties", {}) or {}).get(name, 0)
    unit = penalty_unit(home)
    if owed <= 0:
        await update.message.reply_text(f"{name} has no {unit}s to redeem.", reply_markup=get_penalties_keyboard())
        end_flow(context, update.effective_chat.id)
        return ConversationHandler.END

    flow["redeem_member"] = name
    flow["redeem_max"] = owed
    await update.message.reply_text(
        f"{name} currently owes {owed} {unit}(s).\nHow many are being redeemed? (1–{owed})",
        reply_markup=get_cancel_keyboard(),
    )
    return REDEEM_COUNT


async def redeem_count(update: Update, context: CallbackContext) -> int:
    home, flow = await continue_flow(update, context)
    if home is None:
        return ConversationHandler.END
    text = update.message.text.strip()
    if text.lower() == "cancel":
        return await cancel(update, context)
    name = flow.get("redeem_member")
    max_count = int(flow.get("redeem_max") or 0)
    unit = penalty_unit(home)

    try:
        count = int(text)
    except ValueError:
        await update.message.reply_text(f"Please enter a whole number between 1 and {max_count}.")
        return REDEEM_COUNT
    if count < 1 or count > max_count:
        await update.message.reply_text(f"Please enter a number between 1 and {max_count}.")
        return REDEEM_COUNT

    def apply(h):
        penalties = h.setdefault("penalties", {})
        current = int(penalties.get(name, 0) or 0)
        redeemed = min(count, current)
        remaining = current - redeemed
        if remaining <= 0:
            penalties.pop(name, None)
        else:
            penalties[name] = remaining
        return redeemed, remaining

    _, (redeemed, remaining) = get_store().update(home["id"], apply)
    end_flow(context, update.effective_chat.id)

    if redeemed == 0:
        msg = f"{name} no longer owes any {unit}s (already redeemed)."
    else:
        msg = f"Redeemed {redeemed} {unit}(s) for {name}. " + (
            f"{remaining} still owed." if remaining else f"All {unit}s redeemed!"
        )
    await update.message.reply_text(msg, reply_markup=get_penalties_keyboard())
    return ConversationHandler.END
