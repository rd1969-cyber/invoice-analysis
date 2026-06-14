"""Tests for carrier-specific dimensional weight."""
from app.rating.dim import billable_weight_lb, divisor_for


def test_divisor_per_carrier():
    assert divisor_for("DHL") == 139.0
    assert divisor_for("Purolator") == 139.0
    assert divisor_for("UPS") == 139.0


def test_canpar_divisor_is_service_specific():
    assert divisor_for("Canpar", "Ground Single") == 166.0
    assert divisor_for("Canpar", "Select Parcel Single") == 137.0
    assert divisor_for("Canpar", "Express Parcel Single") == 166.0
    assert divisor_for("Canpar", None) == 166.0  # default


def test_billable_uses_dim_when_larger():
    # 18x18x18 = 5832 in^3. At /139 -> 41.96 -> ceil 42; actual 10 -> DIM wins.
    bw, note = billable_weight_lb("DHL", None, 10.0, 18, 18, 18)
    assert bw == 42
    assert "DIM" in note


def test_billable_uses_actual_when_larger():
    bw, _ = billable_weight_lb("Purolator", "Purolator Ground", 50.0, 6, 6, 6)
    assert bw == 50


def test_canpar_select_vs_ground_dim_differs():
    # Same box, different Canpar service -> different DIM divisor -> different billable.
    ground, _ = billable_weight_lb("Canpar", "Ground Single", 1.0, 20, 20, 20)   # /166
    select, _ = billable_weight_lb("Canpar", "Select Parcel Single", 1.0, 20, 20, 20)  # /137
    assert select > ground  # smaller divisor => heavier dimensional weight


def test_missing_dims_falls_back_to_actual():
    bw, note = billable_weight_lb("DHL", None, 7.3, None, None, None)
    assert bw == 8  # ceil(7.3)
    assert "no dims" in note
