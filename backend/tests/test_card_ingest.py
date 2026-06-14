"""Tests for multi-format rate-card ingestion (Excel .xls/.xlsx + PDF)."""
import glob
import os
import tempfile

import pytest

from app.rating.cards import load_any, parse_grid


def test_parse_grid_single_cell_layout():
    rows = [
        ["Prepared  for: INXPRESS"],
        ["My Carrier Ground"],
        ["Value in CAD"],
        ["Weight(lb)", "1", "2", "3"],
        [1, 10.00, 12.00, 14.00],
        [2, 11.00, 13.00, 15.00],
    ]
    card = parse_grid(rows, "Test")
    p = card.get("My Carrier Ground")
    assert p is not None
    assert p.zones == ["1", "2", "3"]
    assert p.quote_base_cents("2", 2)[0] == 1300  # row 2, zone '2' = $13.00


def test_parse_grid_ignores_none_trailing_cells():
    rows = [
        ["Prepared for: INXPRESS"],
        ["Carrier X", None, None],
        ["Weight(lb)", "1", None],
        [1, 9.99, None],
    ]
    card = parse_grid(rows, "X")
    assert list(card.products) == ["Carrier X"]  # not "Carrier X None None"


def test_parse_grid_pdf_textsplit_with_overage():
    rows = [
        ["Prepared", "for:", "INXPRESS"],
        ["DHL", "Express", "Worldwide", "-", "Package"],
        ["Value", "in", "CAD"],
        ["Weight(lb)", "N1", "N2"],
        ["1", "96.20", "152.97"],
        ["2", "104.28", "182.52"],
        ["Non-Document", "above", "200", "lb", "(Multiply", "by", "zone", "rate)"],
        ["Weight(lb)", "N1", "N2"],
        ["200.1", "7.80", "8.10"],
    ]
    card = parse_grid(rows, "DHL")
    p = card.get("DHL Express Worldwide - Package")
    assert p.quote_base_cents("N1", 2)[0] == 10428
    assert p.quote_base_cents("N1", 300)[0] == int(round(7.80 * 300 * 100))  # overage


def test_load_any_xlsx_round_trip():
    pytest.importorskip("openpyxl")
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "DIFFERENT"
    for r in [["Prepared  for: INXPRESS"], ["Carrier Ground"], ["Value in CAD"],
              ["Weight(lb)", "1", "2"], [1, 8.00, 9.00], [2, 8.50, 9.50]]:
        ws.append(r)
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tf:
        path = tf.name
    wb.save(path)
    try:
        card = load_any(path, "Carrier")
        assert card.get("Carrier Ground").quote_base_cents("2", 2)[0] == 950
    finally:
        os.unlink(path)


def test_load_any_real_xls_still_works():
    files = glob.glob(os.path.join("samples", "rate_cards", "CANPAR_Domestic.xls"))
    if not files:
        pytest.skip("no sample Canpar card")
    card = load_any(files[0], "Canpar")
    assert card.get("Express Parcel Single").quote_base_cents("1", 1)[0] == 1871


def test_load_any_rejects_unknown_format():
    with pytest.raises(ValueError):
        load_any("foo.txt", "X")
