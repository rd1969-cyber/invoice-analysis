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


def test_per_component_margin_uses_quote_line_items():
    from app.rating.engine import Quote
    q = Quote(our_carrier="Purolator", our_service="Ground", currency="CAD")
    q.add("BASE", "Base", 5000)          # $50 base
    q.add("FUEL", "Fuel", 1000)          # $10 fuel
    q.add("RESI", "Residential", 500)    # $5 residential
    r = ComparisonRow("T", "svc", "domestic_parcel", 10000, q.cost_cents,
                      "Purolator", "Ground", q, {"Purolator": q.cost_cents})
    # base 30%, fuel 20%, residential 50%
    margins = {"base": 0.30, "fuel": 0.20, "residential": 0.50, "default": 0.0}
    sell, margin, mpct, _ = r.margin_price(margins)
    # 5000/0.7=7143 + 1000/0.8=1250 + 500/0.5=1000 = 9393
    assert sell == round(5000 / 0.7) + round(1000 / 0.8) + round(500 / 0.5)
    assert margin == sell - q.cost_cents


def test_applicable_components_from_shipment():
    from app.rating.accessorials import applicable_components
    s = ParsedShipment(tracking_number="X", accessorials=[
        {"type": "residential", "amount_cents": 505, "desc": "r"},
        {"type": "signature", "amount_cents": 600, "desc": "s"},
        {"type": "fuel", "amount_cents": 100, "desc": "f"},  # not an accessorial component
    ])
    comps = applicable_components(s)
    assert "residential" in comps and "adult_signature" in comps
    assert "fuel" not in comps
