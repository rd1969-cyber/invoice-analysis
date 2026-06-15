"""Tests for registering an additional domestic carrier at runtime."""
from app.parsers import ParsedInvoice, ParsedShipment
from app.rating import carriers as carriersmod
from app.rating.cards import parse_grid
from app.rating.carriers import register_domestic_carrier
from app.rating.comparison import build_rows


def _fedex_card():
    return parse_grid([
        ["Prepared for: INXPRESS"],
        ["FedEx Ground"],
        ["Weight(lb)", "1", "2", "3"],
        [1, 8.00, 9.00, 10.00],
        [2, 8.50, 9.50, 11.00],
    ], "FedEx")


def test_register_and_quote_new_carrier():
    card = _fedex_card()
    register_domestic_carrier("FedEx", products=None, zone_prefix="",
                              dim_divisor=139.0, fuel_pct=0.0)
    try:
        assert "FedEx" in carriersmod.DOMESTIC_CARRIERS
        s = ParsedShipment(tracking_number="1ZX", service="Standard",
                           dest_postal="B4C4H2", dest_country="CA",  # NS -> est zone index 1
                           actual_weight=1.0, billed_weight=1.0, total_charge_cents=2000)
        inv = ParsedInvoice(invoice_number="I", carrier="UPS", shipments=[s])
        rows = build_rows([inv], cards={"FedEx": card})
        r = rows[0]
        assert r.my_carrier == "FedEx"
        assert r.carrier_costs.get("FedEx") == 800  # zone 1 @ 1lb = $8.00, 0% fuel
    finally:
        carriersmod.DOMESTIC_CARRIERS.pop("FedEx", None)


def test_zone_prefix_applied_for_new_carrier():
    register_domestic_carrier("Acme", zone_prefix="Z")
    try:
        assert carriersmod._zone_label("Acme", 3) == "Z03"
        assert carriersmod._zone_label("Canpar", 3) == "3"
        assert carriersmod._zone_label("Purolator", 3) == "D03"
    finally:
        carriersmod.DOMESTIC_CARRIERS.pop("Acme", None)
