"""Tests for FSA->zone chart parsing and resolution."""
from app.rating import zones
from app.rating.carriers import quote_domestic
from app.rating.engine import ShipmentInput
from app.rating.cards import parse_grid


def test_parse_zone_grid_two_column():
    rows = [
        ["FSA", "Zone"],
        ["B4C", "1"],
        ["M5V", "8"],
        ["V6B 1A1", "14"],   # full postal -> FSA
        ["junk", "row"],
    ]
    m = zones.parse_zone_grid(rows)
    assert m == {"B4C": "1", "M5V": "8", "V6B": "14"}


def test_resolve_zone_uses_chart_then_none():
    zones.ZONE_CHARTS.clear()
    assert zones.resolve_zone("Canpar", "B4C 4H2") is None  # no chart loaded
    zones.set_chart("Canpar", {"B4C": "3"})
    assert zones.resolve_zone("Canpar", "B4C4H2") == "3"
    assert zones.resolve_zone("Canpar", "Z9Z9Z9") is None   # FSA not in chart
    zones.ZONE_CHARTS.clear()


def test_chart_overrides_province_estimate_in_quote():
    # Build a tiny Canpar card with a distinctive price at zone '3'.
    card = parse_grid([
        ["Prepared for: INXPRESS"],
        ["Ground Single"],
        ["Weight(lb)", "1", "2", "3", "4"],
        [1, 10.00, 20.00, 30.00, 40.00],
    ], "Canpar")
    s = ShipmentInput(scope="domestic_parcel", service=None, origin_postal=None,
                      dest_postal="B4C4H2", dest_country="CA", actual_weight=1.0,
                      billed_weight=1.0)

    zones.ZONE_CHARTS.clear()
    q_est = quote_domestic(s, "Canpar", card)  # province estimate -> NS = zone 1
    assert any("ESTIMATED" in w for w in q_est.warnings)

    zones.set_chart("Canpar", {"B4C": "3"})
    q_exact = quote_domestic(s, "Canpar", card)  # chart -> zone 3 = $30 base
    assert not any("ESTIMATED" in w for w in q_exact.warnings)
    base = next(li for li in q_exact.line_items if li.code == "BASE")
    assert base.amount_cents == 3000
    zones.ZONE_CHARTS.clear()
