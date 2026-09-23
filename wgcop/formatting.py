"""Currency and entry formatters (relocated verbatim from maBot.py)."""
import html

from wgcop.members import _normalise_member_name

def _format_currency(amount: float) -> str:
    return f"CHF {amount:.2f}"


def _format_signed_currency(amount: float) -> str:
    sign = "+" if amount >= 0 else ""
    return f"CHF {sign}{amount:.2f}"


def _format_chore_entry(entry):
    ts = entry.get("timestamp", "")[:16].replace("T", " ")
    member = entry.get("member", "?")
    points = entry.get("points", 0)
    desc = entry.get("description", "")
    desc_part = f" – {desc}" if desc else ""
    return f"<b>{ts}</b> · {member} (+{points} pts){desc_part}"


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
