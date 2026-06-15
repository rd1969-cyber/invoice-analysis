"""Tests for manual cost overrides and the margin pricing model."""
from app.parsers import ParsedInvoice, ParsedShipment
from app.rating.comparison import (
    ComparisonRow, build_rows, parse_manual_costs, rows_to_records,
)


def test_parse_manual_costs_flexible_columns():
    rows = [
        {"Tracking #": "1ZABC", "My Cost": "42.50", "Carrier": "FedEx", "Service": "Ground"},
        {"Tracking #": "1ZDEF", "My Cost": "$1,200.00"},
        {"Tracking #": "", "My Cost": "5.00"},          # skipped (no tracking)
        {"Tracking #": "1ZGHI", "My Cost": "bad"},       # skipped (bad cost)
    ]
    m = parse_manual_costs(rows)
    assert m["1ZABC"]["cost_cents"] == 4250
    assert m["1ZABC"]["carrier"] == "FedEx"
    assert m["1ZDEF"]["cost_cents"] == 120000
    assert "1ZGHI" not in m and "" not in m


def test_manual_cost_overrides_quote():
    s = ParsedShipment(tracking_number="1ZTEST", service="Standard",
                       dest_postal="B4C4H2", dest_country="CA",
                       actual_weight=2.0, billed_weight=2.0, total_charge_cents=5000)
    inv = ParsedInvoice(invoice_number="X", carrier="UPS", shipments=[s])
    manual = {"1ZTEST": {"cost_cents": 3000, "carrier": "FedEx", "service": "Ground"}}
    rows = build_rows([inv], cards={}, manual_costs=manual)
    r = rows[0]
    assert r.my_cost_cents == 3000
    assert r.my_carrier == "FedEx"
    assert r.carrier_costs.get("FedEx") == 3000


def test_margin_model_is_percent_of_sell():
    r = ComparisonRow("T", "svc", "domestic_parcel", 10000, 6000, "Purolator", "Ground",
                      None, {"Purolator": 6000})
    sell, margin, mpct, savings = r.margin_price(0.25)  # 25% margin
    assert sell == 8000          # 6000 / 0.75
    assert margin == 2000        # 8000 - 6000
    assert round(mpct, 4) == 0.25
    assert savings == 2000       # 10000 - 8000
