"""Tests for the comparison & suggested-margin logic.

Uses controlled ComparisonRow inputs so the margin math is verified independently
of whether the real DHL card happens to be competitive.
"""
from app.rating.comparison import ComparisonRow


def _row(comp_cents, cost_cents):
    return ComparisonRow(
        tracking="T", service="svc", scope="us_bound_parcel",
        competitor_pays_cents=comp_cents, my_cost_cents=cost_cents,
        dhl_service="DHL", quote=None,
    )


def test_low_cost_is_competitive_black():
    r = _row(10000, 6000)  # they pay $100, my cost $60
    assert r.difference_cents == 4000
    assert r.is_high is False


def test_high_cost_is_red():
    r = _row(6000, 10000)  # my cost exceeds their price
    assert r.difference_cents == -4000
    assert r.is_high is True


def test_unserviceable_when_no_cost():
    r = _row(6000, None)
    assert r.serviceable is False
    assert r.difference_cents is None
    assert r.suggested(0.15, 0.10) is None


def test_suggested_margin_at_target_savings():
    # They pay $100, my cost $60. Offer 15% customer savings -> sell $85.
    r = _row(10000, 6000)
    sell, margin, mpct, savings = r.suggested(0.15, 0.10)
    assert sell == 8500
    assert margin == 2500          # 85 - 60
    assert round(mpct, 4) == round(2500 / 8500, 4)
    assert savings == 1500         # customer saves $15


def test_margin_floor_protects_against_too_deep_a_discount():
    # They pay $100, my cost $95. 15% savings would mean sell $85 < cost -> floor.
    # Floor = cost * (1 + 10%) = $104.50, capped at competitor price $100.
    r = _row(10000, 9500)
    sell, margin, mpct, savings = r.suggested(0.15, 0.10)
    assert sell == 10000           # capped at competitor price (never above)
    assert margin == 500           # 100 - 95
    assert savings == 0


def test_high_cost_has_no_suggestion():
    r = _row(6000, 10000)
    assert r.suggested(0.15, 0.10) is None
