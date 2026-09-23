"""Read-only views: recent expenses and standings (relocated verbatim)."""
import html

from telegram import Update
from telegram.ext import CallbackContext

from wgcop.formatting import _format_expense_entry, _format_signed_currency
from wgcop.identity import _resolve_member_for_user
from wgcop.keyboards import get_main_keyboard
from wgcop.members import _get_member_name, _match_member_name, _normalise_member_name
from wgcop.store import load_data

# Settings
EXPENSE_LIST_LIMIT = 20

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
