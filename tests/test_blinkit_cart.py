from app.blinkit import cart_summary_from_raw


def test_parses_lines_and_bill_from_cart_text():
    raw = {
        "lines": [
            {"text": "Amul Taaza Toned Milk\n1 L\n2 x ₹35\n₹70", "handle": "/prn/amul/1"},
            {"text": "Aashirvaad Atta\n1 kg\n₹60", "handle": "/prn/atta/2"},
        ],
        "billText": (
            "Item total\n₹130\nDelivery charge\n₹25\nHandling charge\n₹5\n"
            "Grand total\n₹160"
        ),
        "etaText": "Delivery in 11 minutes",
    }

    summary = cart_summary_from_raw(raw, provider="blinkit")

    assert [line.name for line in summary.lines] == [
        "Amul Taaza Toned Milk", "Aashirvaad Atta",
    ]
    assert summary.lines[0].quantity == 2
    assert summary.lines[0].line_total == 70.0
    assert summary.lines[1].quantity == 1
    assert summary.subtotal == 130.0
    assert {fee.label for fee in summary.fees} == {"Delivery charge", "Handling charge"}
    assert summary.total == 160.0
    assert summary.delivery_eta_minutes == 11
    assert summary.reconciles is True


def test_discount_line_is_negative():
    raw = {
        "lines": [{"text": "Milk\n1 L\n₹100", "handle": "h"}],
        "billText": "Item total\n₹100\nDelivery charge\n₹25\nDiscount\n-₹15\nGrand total\n₹110",
        "etaText": "",
    }
    summary = cart_summary_from_raw(raw, provider="blinkit")
    discount = next(fee for fee in summary.fees if fee.label == "Discount")
    assert discount.amount == -15.0
    assert summary.reconciles is True


def test_unreadable_fee_line_breaks_reconciliation_rather_than_lying():
    """The whole point: a missed fee must not look like a cheap cart."""
    raw = {
        "lines": [{"text": "Milk\n1 L\n₹100", "handle": "h"}],
        "billText": "Item total\n₹100\nGrand total\n₹132",
        "etaText": "",
    }
    summary = cart_summary_from_raw(raw, provider="blinkit")
    assert summary.reconciles is False
    assert "132" in summary.reconciliation_error


def test_empty_cart():
    summary = cart_summary_from_raw({"lines": [], "billText": "", "etaText": ""}, provider="blinkit")
    assert summary.lines == []
    assert summary.total == 0
    assert summary.reconciles is True
