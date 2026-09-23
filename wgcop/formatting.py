"""Currency and entry formatters (home-aware: currency label comes from settings)."""
import html

from wgcop.members import _normalise_member_name


def format_currency(amount: float, currency: str = "CHF") -> str:
    return f"{currency} {amount:.2f}"


def format_signed_currency(amount: float, currency: str = "CHF") -> str:
    sign = "+" if amount >= 0 else ""
    return f"{currency} {sign}{amount:.2f}"


def format_chore_entry(entry):
    ts = entry.get("timestamp", "")[:16].replace("T", " ")
    member = entry.get("member", "?")
    points = entry.get("points", 0)
    desc = entry.get("description", "")
    desc_part = f" – {html.escape(desc)}" if desc else ""
    return f"<b>{html.escape(ts)}</b> · {html.escape(str(member))} (+{points} pts){desc_part}"


def format_expense_entry(entry, viewer_name, currency: str = "CHF"):
    date = str(entry.get("date", "?"))
    description = html.escape(str(entry.get("description", "(no description)")))
    amount = float(entry.get("amount", 0.0) or 0.0)
    payer = str(entry.get("payer", "?"))
    split_raw = [str(name) for name in (entry.get("split_with", []) or [])]
    split_names = [html.escape(name) for name in split_raw]

    viewer_norm = _normalise_member_name(viewer_name)
    payer_norm = _normalise_member_name(payer)
    viewer_in_split = any(_normalise_member_name(name) == viewer_norm for name in split_raw)

    lines = [
        f"<b>{html.escape(date)}</b> · {description}",
        f"Total: {format_currency(amount, currency)}",
        f"Payer: {html.escape(payer)}",
    ]

    if split_names:
        lines.append(f"Split ({len(split_names)}): {', '.join(split_names)}")
    else:
        lines.append("Split: —")

    if viewer_norm:
        share_count = len(split_raw)
        if payer_norm == viewer_norm:
            if share_count:
                share = amount / share_count
                owed = share * sum(1 for name in split_raw if _normalise_member_name(name) != payer_norm)
                if owed > 0:
                    lines.append(f"To receive: {format_currency(owed, currency)}")
        elif viewer_in_split and share_count:
            lines.append(f"Your share: {format_currency(amount / share_count, currency)}")

    return "\n".join(lines)


# Backwards-compatible aliases (older tests/modules)
_format_currency = format_currency
_format_signed_currency = format_signed_currency
_format_chore_entry = format_chore_entry
_format_expense_entry = format_expense_entry
