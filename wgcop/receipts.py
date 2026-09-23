"""GPT-4o receipt pipeline and receipt review keyboards (relocated verbatim)."""
import base64
import json
import logging
import math
import os
from datetime import datetime

import pytz
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from wgcop.botconfig import BACKUP_DIR, OPENAI_API_KEY, RECEIPT_TOLERANCE_CHF
from wgcop.keyboards import (
    CB_RECEIPT_CANCEL,
    CB_RECEIPT_DONE,
    CB_RECEIPT_TOGGLE_PREFIX,
    _truncate_button_label,
)

try:
    import openai as _openai_lib
except ImportError:  # pragma: no cover - optional dependency
    _openai_lib = None

logger = logging.getLogger(__name__)

class ReceiptParsingError(Exception):
    """Raised when a receipt image cannot be parsed for line items."""


def _receipt_shared_total(items, selected):
    return round(sum(items[i]["amount"] for i in selected if i < len(items)), 2)


def _adjust_items_to_total(items, receipt_total):
    """Scale item amounts proportionally so they sum to receipt_total."""
    items_sum = sum(item["amount"] for item in items)
    if not items_sum or abs(items_sum - receipt_total) < 0.01:
        return items
    scale = receipt_total / items_sum
    adjusted = [{"name": item["name"], "amount": round(item["amount"] * scale, 2)} for item in items]
    diff = round(receipt_total - sum(a["amount"] for a in adjusted), 2)
    if diff and adjusted:
        largest = max(range(len(adjusted)), key=lambda i: adjusted[i]["amount"])
        adjusted[largest]["amount"] = round(adjusted[largest]["amount"] + diff, 2)
    return adjusted


def build_receipt_items_text(items, selected):
    total = _receipt_shared_total(items, selected)
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


RECEIPT_PARSE_LOG = os.path.join(BACKUP_DIR, "receipt_parse_log.jsonl")


def _log_receipt_parse(entry: dict):
    try:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        with open(RECEIPT_PARSE_LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as exc:
        logger.warning("Receipt parse log write failed: %s", exc)


def _receipt_prompt_text(receipt_total=None, items_sum=None, prior_json=None):
    prompt = (
        "Extract every purchased line item from this receipt and suggest a short expense description.\n"
        "Return ONLY a JSON object — no markdown, no explanation — in this exact format:\n"
        '{"description": "Migros groceries", "total_observed": 129.50, "items": [{"name": "Product name", "quantity": 1, "unit_price": 9.95, "line_total_observed": 9.95}, ...]}\n\n'
        "Rules:\n"
        "- description: 2-4 words, store name + category (e.g. 'Migros groceries', 'Lidl snacks')\n"
        "- total_observed: the grand total printed at the bottom (e.g. 'TOTAL CHF 129.50') — copy exactly as a number\n"
        "- quantity: MUST be the exact Menge column value from the receipt (can be fractional for weighted items, e.g. 0.275)\n"
        "- For multi-buy items, never default quantity to 1: if the receipt shows 2 x CHF 2.00, return quantity: 2, unit_price: 2.00\n"
        "- unit_price: the Preis column value (unit price or per-kg price)\n"
        "- line_total_observed: the line total as printed on the receipt — copy exactly, do not compute\n"
        "- For discounted items: unit_price and line_total_observed should reflect the discounted price (Aktion column), not the original\n"
        "- If the same product name appears on multiple lines (different weights), list EACH as a separate item; append weight to disambiguate\n"
        "- Exclude header rows, subtotals, receipt totals, tax lines, and loyalty/points lines\n"
        "- Keep item names short but recognisable"
    )
    if receipt_total is not None and items_sum is not None and prior_json:
        prompt += (
            "\n\nRe-check carefully because the first extraction does not reconcile.\n"
            f"- Printed receipt total: CHF {receipt_total:.2f}\n"
            f"- Computed sum of line items: CHF {items_sum:.2f}\n"
            f"- First extracted JSON: {prior_json}\n"
            "- Focus on missed duplicate lines and wrong quantity values on multi-buy items.\n"
            "- Return a corrected full JSON object for the entire receipt, not a diff."
        )
    return prompt


def _parse_receipt_ocr_response(raw: str, log_prefix: str):
    start = raw.index("{")
    end = raw.rindex("}") + 1
    raw_json = raw[start:end]
    logger.info("%s raw JSON: %s", log_prefix, raw_json)
    parsed = json.loads(raw_json)
    items = parsed["items"]
    if not isinstance(items, list):
        raise ValueError("Receipt items must be a list")
    description = str(parsed.get("description", "")).strip()
    receipt_total = parsed.get("total_observed")
    if receipt_total is not None:
        try:
            receipt_total = round(float(receipt_total), 2)
            if not math.isfinite(receipt_total) or receipt_total <= 0:
                raise ValueError("Invalid receipt total")
        except (TypeError, ValueError):
            receipt_total = None
    return raw_json, items, description, receipt_total


def _normalise_receipt_items(items, log_prefix: str):
    normalised = []
    for idx, i in enumerate(items, start=1):
        if not isinstance(i, dict) or "name" not in i or i.get("unit_price") is None:
            continue
        name = str(i["name"]).strip()
        raw_quantity = i.get("quantity")
        raw_unit_price = i.get("unit_price")
        raw_line_total_observed = i.get("line_total_observed")
        try:
            quantity = float(raw_quantity) if raw_quantity is not None else 1.0
            unit_price = float(raw_unit_price)
            if not math.isfinite(quantity) or not math.isfinite(unit_price) or quantity <= 0:
                continue
        except (TypeError, ValueError):
            continue
        computed = round(quantity * unit_price, 2)
        if not math.isfinite(computed):
            continue
        observed = None
        try:
            if raw_line_total_observed is not None:
                observed = round(float(raw_line_total_observed), 2)
                if not math.isfinite(observed):
                    observed = None
        except (TypeError, ValueError):
            pass
        logger.info(
            "%s item %02d: name=%r quantity=%r unit_price=%r line_total_observed=%r computed=%.2f",
            log_prefix,
            idx,
            name,
            raw_quantity,
            raw_unit_price,
            raw_line_total_observed,
            computed,
        )
        normalised.append({"name": name, "amount": computed, "_observed": observed})
    return normalised


def _vision_call(client, image_data: str, media_type: str, prompt_text: str) -> str:
    response = client.chat.completions.create(
        model="gpt-4o",
        max_tokens=2048,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{media_type};base64,{image_data}"},
                },
                {"type": "text", "text": prompt_text},
            ],
        }]
    )
    return response.choices[0].message.content.strip()


def extract_items_from_receipt(image_path: str, mime_type: str = "image/jpeg"):
    media_type = mime_type if mime_type and mime_type.startswith("image/") else "image/jpeg"

    if not _openai_lib or not OPENAI_API_KEY:
        raise ReceiptParsingError("Receipt scanning is not configured (missing OPENAI_API_KEY).")

    try:
        with open(image_path, "rb") as f:
            image_data = base64.standard_b64encode(f.read()).decode("utf-8")
    except Exception as exc:
        raise ReceiptParsingError("Failed to read the receipt image.") from exc

    client = _openai_lib.OpenAI(api_key=OPENAI_API_KEY, timeout=45.0, max_retries=1)
    ts = datetime.now(pytz.UTC).isoformat()
    log_entry: dict = {"ts": ts, "image_path": image_path, "passes": []}

    def _call_and_parse(pass_label: str, prompt_text: str):
        try:
            raw = _vision_call(client, image_data, media_type, prompt_text)
        except Exception as exc:
            raise ReceiptParsingError(f"Vision API error ({pass_label}): {exc}") from exc
        try:
            raw_json, items, description, receipt_total = _parse_receipt_ocr_response(
                raw, f"Receipt {pass_label}"
            )
        except (ValueError, json.JSONDecodeError, KeyError) as exc:
            raise ReceiptParsingError(
                f"Could not parse API response as JSON ({pass_label}): {exc}"
            ) from exc
        if not items:
            raise ReceiptParsingError(f"No line items found in receipt ({pass_label}).")
        normalised = _normalise_receipt_items(items, f"Receipt {pass_label}")
        if not normalised:
            raise ReceiptParsingError(f"No valid line items after normalisation ({pass_label}).")
        items_sum = round(sum(item["amount"] for item in normalised), 2)
        gap = abs(items_sum - receipt_total) if receipt_total is not None else None
        log_entry["passes"].append({
            "pass": pass_label,
            "raw_json": raw_json,
            "item_count": len(normalised),
            "items_sum": items_sum,
            "receipt_total": receipt_total,
            "gap": gap,
        })
        logger.info(
            "Receipt %s: items=%d items_sum=%.2f receipt_total=%s gap=%s",
            pass_label,
            len(normalised),
            items_sum,
            f"{receipt_total:.2f}" if receipt_total is not None else "None",
            f"{gap:.2f}" if gap is not None else "None",
        )
        return description, receipt_total, normalised, items_sum, gap, raw_json

    description, receipt_total, normalised, items_sum, gap, raw_json = _call_and_parse(
        "pass1", _receipt_prompt_text()
    )

    # Retry once if sum deviates beyond tolerance and we have a total to compare
    if receipt_total is not None and gap is not None and gap > RECEIPT_TOLERANCE_CHF:
        logger.info(
            "Receipt pass1 gap %.2f > tolerance %.2f — retrying with mismatch context",
            gap,
            RECEIPT_TOLERANCE_CHF,
        )
        try:
            description2, receipt_total2, normalised2, items_sum2, gap2, _ = _call_and_parse(
                "pass2",
                _receipt_prompt_text(
                    receipt_total=receipt_total,
                    items_sum=items_sum,
                    prior_json=raw_json,
                ),
            )
            # Accept pass2 only if it's a genuine improvement
            if gap2 is None or (gap is not None and gap2 < gap):
                description, receipt_total, normalised, items_sum, gap = (
                    description2, receipt_total2, normalised2, items_sum2, gap2
                )
                logger.info("Receipt pass2 accepted (gap improved to %s)", f"{gap2:.2f}" if gap2 is not None else "None")
            else:
                logger.info("Receipt pass2 not better (gap %s vs %s) — keeping pass1", f"{gap2:.2f}" if gap2 is not None else "None", f"{gap:.2f}" if gap is not None else "None")
        except ReceiptParsingError as exc:
            logger.warning("Receipt pass2 failed: %s — keeping pass1", exc)

    log_entry["final_gap"] = gap
    log_entry["accepted_pass"] = log_entry["passes"][-1]["pass"] if log_entry["passes"] else None
    _log_receipt_parse(log_entry)

    # Strip internal _observed field before returning
    clean_items = [{"name": item["name"], "amount": item["amount"]} for item in normalised]
    return {
        "description": description,
        "receipt_total": receipt_total,
        "items_sum": items_sum,
        "gap": gap,
        "items": clean_items,
    }
