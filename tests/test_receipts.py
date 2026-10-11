"""Receipt maths and parsing that never call the vision API."""
import pytest

from wgcop.receipts import (
    ReceiptParsingError,
    _adjust_items_to_total,
    _normalise_receipt_items,
    _parse_receipt_ocr_response,
    _receipt_shared_total,
    extract_items_from_receipt,
)


def test_adjust_items_scales_to_receipt_total_exactly():
    items = [{"name": "a", "amount": 10.0}, {"name": "b", "amount": 20.0}]
    adjusted = _adjust_items_to_total(items, 33.0)
    assert [i["amount"] for i in adjusted] == [11.0, 22.0]
    assert round(sum(i["amount"] for i in adjusted), 2) == 33.0


def test_adjust_items_puts_rounding_remainder_on_largest():
    items = [{"name": "a", "amount": 10.0}, {"name": "b", "amount": 10.0}, {"name": "c", "amount": 10.0}]
    adjusted = _adjust_items_to_total(items, 31.0)
    assert round(sum(i["amount"] for i in adjusted), 2) == 31.0
    assert max(i["amount"] for i in adjusted) == 10.34


def test_adjust_items_noop_when_already_matching_or_empty():
    items = [{"name": "a", "amount": 5.0}]
    assert _adjust_items_to_total(items, 5.0) is items
    assert _adjust_items_to_total([], 5.0) == []


def test_shared_total_sums_selected_indices_only():
    items = [{"name": "a", "amount": 1.5}, {"name": "b", "amount": 2.25}, {"name": "c", "amount": 3.0}]
    assert _receipt_shared_total(items, {0, 2}) == 4.5
    assert _receipt_shared_total(items, {7}) == 0.0


def test_parse_response_extracts_json_from_prose():
    raw = 'Sure! ```json\n{"description": "Migros groceries", "total_observed": "12.5", "items": [{"name": "Milk"}]}\n```'
    raw_json, items, description, total = _parse_receipt_ocr_response(raw, "t")
    assert items == [{"name": "Milk"}]
    assert description == "Migros groceries"
    assert total == 12.5
    assert raw_json.startswith("{") and raw_json.endswith("}")


def test_normalise_items_computes_line_totals_and_skips_junk():
    items = [
        {"name": "Milk", "quantity": 2, "unit_price": 1.25, "line_total_observed": 2.5},
        {"name": "NoPrice"},
        {"name": "Weighted", "quantity": 0.5, "unit_price": "4.00"},
        "garbage",
    ]
    out = _normalise_receipt_items(items, "t")
    assert [(i["name"], i["amount"]) for i in out] == [("Milk", 2.5), ("Weighted", 2.0)]
    assert out[0]["_observed"] == 2.5
    assert out[1]["_observed"] is None


def test_extract_without_api_key_raises_clean_error(tmp_path):
    img = tmp_path / "r.jpg"
    img.write_bytes(b"not really an image")
    with pytest.raises(ReceiptParsingError):
        extract_items_from_receipt(str(img))
