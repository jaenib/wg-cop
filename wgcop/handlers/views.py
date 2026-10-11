"""Read-only views: recent expenses and standings for the resolved home."""
import html

from telegram import Update
from telegram.ext import CallbackContext

from wgcop.formatting import format_expense_entry, format_signed_currency
from wgcop.homes import home_currency, require_home
from wgcop.keyboards import EXPENSE_LIST_LIMIT, get_main_keyboard
from wgcop.members import _is_retired, _normalise_member_name, active_members, calculate_balances, chore_points_by_member, member_stats
from wgcop.store import member_for_uid


async def list_expenses(update: Update, context: CallbackContext) -> None:
    home = await require_home(update, context)
    if home is None:
        return
    expenses = home.get("expenses", []) or []
    if not expenses:
        await update.message.reply_text("No expenses recorded yet.", reply_markup=get_main_keyboard())
        return

    viewer = member_for_uid(home, update.effective_user.id)
    if not viewer:
        await update.message.reply_text(
            "I cannot match you to a member of this home. Send /join in the group to link your profile.",
            reply_markup=get_main_keyboard(),
        )
        return

    viewer_name = viewer["name"]
    viewer_norm = _normalise_member_name(viewer_name)
    currency = home_currency(home)

    relevant = []
    for entry in reversed(expenses):
        is_payer = _normalise_member_name(entry.get("payer")) == viewer_norm
        in_split = any(_normalise_member_name(n) == viewer_norm for n in entry.get("split_with", []) or [])
        if is_payer or in_split:
            relevant.append(entry)
        if len(relevant) >= EXPENSE_LIST_LIMIT:
            break

    if not relevant:
        await update.message.reply_text("No recent expenses linked to you.", reply_markup=get_main_keyboard())
        return

    formatted = [format_expense_entry(entry, viewer_name, currency) for entry in reversed(relevant)]
    header = f"<b>Recent expenses involving {html.escape(viewer_name)}</b>"
    text = header + "\n\n" + "\n\n".join(formatted) + "\n\n" + header
    await update.message.reply_html(text, reply_markup=get_main_keyboard())


async def standings(update: Update, context: CallbackContext) -> None:
    home = await require_home(update, context)
    if home is None:
        return
    members = active_members(home)
    retired = [m for m in home.get("members", []) or [] if _is_retired(m)]
    if not members and not retired:
        await update.message.reply_text("No members recorded yet.", reply_markup=get_main_keyboard())
        return

    balances = calculate_balances(home)
    chores = chore_points_by_member(home)
    currency = home_currency(home)
    ordered = sorted((m["name"] for m in members), key=lambda n: chores.get(n, 0), reverse=True)

    lines = [
        f"{name}: {chores.get(name, 0)} points, {format_signed_currency(balances.get(name, 0.0), currency)}"
        for name in ordered
    ]
    if retired:
        lines += ["", "Retired (records frozen):"]
        for member in retired:
            stats = member_stats(home, member)
            lines.append(
                f"  {member['name']}: {stats['chore_points']} points, {format_signed_currency(stats['balance'], currency)}"
            )
    await update.message.reply_text("\n".join(lines), reply_markup=get_main_keyboard())
