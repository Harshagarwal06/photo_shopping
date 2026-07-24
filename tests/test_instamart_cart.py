import json
from pathlib import Path

from app.providers.instamart import (
    _fees_from_bill,
    cart_summary_from_instamart,
    zeroed_cart_update,
)

FIXTURE = Path(__file__).parent / "fixtures" / "instamart_cart.json"


def test_cart_summary_reads_lines_and_fees():
    """Payload shape matches the real spike recording (tests/fixtures/instamart_cart.json):
    flat top-level "items" (no "cart" wrapper) and "billBreakdown" with a "lineItems"
    list plus a "toPay" {label, value} object. Item field names (spinId, quantity,
    price) and populated fee-line-item field names were not observed live (the
    connected account's cart was empty) and are the parser's best-effort guess.
    """
    payload = {
        "items": [
            {
                "spinId": "abc",
                "name": "Amul Taaza 1 L",
                "quantity": 2,
                "price": 35.0,
                "total": 70.0,
            },
        ],
        "billBreakdown": {
            "lineItems": [
                {"label": "Delivery fee", "value": 25.0},
                {"label": "Handling fee", "value": 5.0},
                {"label": "Discount", "value": -10.0},
            ],
            "toPay": {"label": "To Pay", "value": 90.0},
        },
        "etaMinutes": 12,
    }

    summary = cart_summary_from_instamart(payload)

    assert summary.provider == "instamart"
    assert [line.name for line in summary.lines] == ["Amul Taaza 1 L"]
    assert summary.lines[0].quantity == 2
    assert summary.subtotal == 70.0
    assert summary.total == 90.0
    assert summary.delivery_eta_minutes == 12
    assert summary.reconciles is True


def test_cart_summary_of_empty_cart_is_zero():
    """Uses the real recorded spike fixture verbatim (empty cart, cartAbsent=True)."""
    payload = json.loads(FIXTURE.read_text())
    summary = cart_summary_from_instamart(payload)
    assert summary.lines == []
    assert summary.total == 0
    assert summary.reconciles is True


def test_zeroed_cart_update_sets_every_quantity_to_zero():
    """Zeroing must speak update_cart's real wire format: spinId, not product_id.
    Uses the real top-level "items" shape observed in the spike (no "cart" wrapper).
    """
    payload = {
        "items": [
            {"spinId": "abc", "quantity": 2},
            {"spinId": "def", "quantity": 1},
        ]
    }
    assert zeroed_cart_update(payload) == [
        {"spinId": "abc", "quantity": 0},
        {"spinId": "def", "quantity": 0},
    ]


def test_zeroed_cart_update_of_empty_cart_is_empty():
    assert zeroed_cart_update({"items": []}) == []


def test_fees_from_bill_does_not_double_count_when_both_shapes_present():
    """A payload carrying both an itemized lineItems array and the flat
    FEE_LABELS convenience keys for the same charges must count each fee once,
    not twice. lineItems is authoritative when present; the flat scan is only
    a fallback for payloads that never include lineItems at all.
    """
    bill = {
        "lineItems": [
            {"label": "Delivery fee", "value": 25.0},
            {"label": "Handling fee", "value": 5.0},
        ],
        "deliveryFee": 25.0,
        "handlingFee": 5.0,
        "surgeFee": 10.0,
    }

    fees = _fees_from_bill(bill)

    assert len(fees) == 2
    assert {fee.label: fee.amount for fee in fees} == {
        "Delivery fee": 25.0,
        "Handling fee": 5.0,
    }


def test_missing_total_is_derived_and_marks_the_summary_estimated():
    """When Instamart reports no grandTotal/total/payableAmount/toPay key at all,
    the total must be derived locally as subtotal + fees. Because a derived
    total always reconciles against itself, this case must force
    estimated=True (never present as a fully verified cart).
    """
    payload = {
        "items": [
            {
                "spinId": "abc",
                "name": "Amul Taaza 1 L",
                "quantity": 2,
                "price": 35.0,
                "total": 70.0,
            },
        ],
        "billBreakdown": {
            "lineItems": [
                {"label": "Delivery fee", "value": 25.0},
            ],
        },
    }

    summary = cart_summary_from_instamart(payload)

    assert summary.subtotal == 70.0
    assert summary.total == 95.0
    assert summary.estimated is True
    assert "total" in summary.raw_note.casefold()
    assert summary.reconciles is True
