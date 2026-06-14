"""Tests for the deterministic core of the rate engine.

These cover the data-independent math (DIM weight, billable weight, fuel,
breakdown auditability). Rate-table lookups are tested once a real card is
loaded into a fixture.
"""
from app.rating.domestic_parcel import (
    DEFAULT_DIM_DIVISOR_IN_LB,
    billable_weight,
    dimensional_weight,
)
from app.rating.engine import RateCard, ShipmentInput, rate_shipment


def _ship(**kw) -> ShipmentInput:
    base = dict(
        scope="domestic_parcel",
        service="Express",
        origin_postal="M5V1A1",
        dest_postal="V6B1A1",
        dest_country="CA",
        actual_weight=10.0,
        billed_weight=None,
        zone="3",
    )
    base.update(kw)
    return ShipmentInput(**base)


def test_dimensional_weight():
    s = _ship(length=12, width=12, height=12)  # 1728 in^3
    dim = dimensional_weight(s, DEFAULT_DIM_DIVISOR_IN_LB)
    assert round(dim, 2) == round(1728 / 139.0, 2)


def test_dimensional_weight_missing_dims_is_none():
    assert dimensional_weight(_ship(), DEFAULT_DIM_DIVISOR_IN_LB) is None


def test_billable_prefers_dim_when_larger():
    # 24x24x24 = 13824 in^3 / 139 ≈ 99.5 -> ceil 100, beats 10 lb actual
    bw, detail = billable_weight(_ship(length=24, width=24, height=24), 139.0)
    assert bw == 100
    assert "DIM" in detail


def test_billable_prefers_actual_when_larger():
    bw, _ = billable_weight(_ship(actual_weight=50, length=6, width=6, height=6), 139.0)
    assert bw == 50


def test_engine_warns_instead_of_fabricating_without_card():
    # No rates loaded -> engine must warn, never invent a price.
    card = RateCard(card_id="empty", carrier="UPS", services=["Express"], fuel_pct=0.15)
    quotes = rate_shipment(_ship(), card)
    assert len(quotes) == 1
    assert quotes[0].cost_cents == 0
    assert any("No base rate loaded" in w for w in quotes[0].warnings)


def test_engine_rates_and_audits_with_card():
    card = RateCard(
        card_id="c1",
        carrier="UPS",
        services=["Express"],
        fuel_pct=0.15,
        dim_divisor=139.0,
        raw={
            # rates[service][zone] -> [(max_weight, price_cents), ...]
            "rates": {"Express": {"3": [(5, 1500), (25, 4200), (9999, 9000)]}},
        },
    )
    [q] = rate_shipment(_ship(actual_weight=10), card)
    # 10 lb -> 25 lb breakpoint = $42.00 base; fuel 15% = $6.30; total $48.30
    assert q.cost_cents == 4200 + 630
    codes = [li.code for li in q.line_items]
    assert codes == ["BASE", "FUEL"]
    # every line item carries a human-readable derivation
    assert all(li.detail for li in q.line_items)
