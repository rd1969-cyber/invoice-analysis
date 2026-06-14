"""Tests for the records/summary helpers and the Excel export."""
from app.rating.comparison import ComparisonRow, rows_to_records, summarize
from app.reporting.excel import build_workbook


def _row(track, comp, cost, carrier="Purolator"):
    return ComparisonRow(
        tracking=track, service="Standard", scope="domestic_parcel",
        competitor_pays_cents=comp, my_cost_cents=cost,
        my_carrier=carrier, my_service="Ground", quote=None,
    )


def _records():
    rows = [
        _row("A", 10000, 4000),   # winnable
        _row("B", 5000, 9000),    # HIGH
        ComparisonRow("C", "Std", "domestic_parcel", 8000, None, None, None, None),  # no rate
    ]
    return rows_to_records(rows, 0.15, 0.10)


def test_records_status_and_margin():
    recs = _records()
    by = {r["tracking"]: r for r in recs}
    assert by["A"]["status"] == "LOW"
    assert by["A"]["suggested_sell"] == 85.0   # 15% off $100
    assert by["A"]["margin"] == 45.0           # 85 - 40
    assert by["B"]["status"] == "HIGH"
    assert by["B"]["margin"] is None
    assert by["C"]["status"] == "NO RATE"


def test_summary_totals():
    s = summarize(_records())
    assert s["shipments"] == 3
    assert s["serviceable"] == 2
    assert s["winnable"] == 1
    assert s["no_rate"] == 1
    assert s["total_margin"] == 45.0
    assert s["by_carrier_lanes"] == {"Purolator": 1}


def test_excel_workbook_builds_both_modes():
    recs = _records()
    summ = summarize(recs)
    settings = {"target_customer_savings": 0.15, "min_margin_pct": 0.10}
    for mode in ("internal", "customer"):
        data = build_workbook(recs, summ, mode, settings)
        assert data[:2] == b"PK"  # xlsx is a zip
        assert len(data) > 2000
