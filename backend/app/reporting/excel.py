"""Branded Excel export of the rate comparison.

Two modes (per the spec):
  * internal  — full picture: my carrier, my cost, difference, margin, markup
  * customer  — savings story only: their current price, your price, savings.
                NO cost or margin shown.

Difference uses the requested colour convention: RED when my cost is HIGH
(uncompetitive), black/dark when competitive. Styled with InXpress brand colours.
"""
from __future__ import annotations

import io

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from app import brand

_BLUE = brand.MIDNIGHT_BLUE.lstrip("#")
_GREEN = brand.SPRING_GREEN.lstrip("#")
_RED = brand.RED.lstrip("#")
_SURFACE = brand.SURFACE_BLUE.lstrip("#")

_HEADER_FILL = PatternFill("solid", fgColor=_BLUE)
_SUBHEAD_FILL = PatternFill("solid", fgColor=_SURFACE)
_WHITE_BOLD = Font(name="Calibri", bold=True, color="FFFFFF", size=12)
_BLUE_BOLD = Font(name="Calibri", bold=True, color=_BLUE)
_RED_FONT = Font(name="Calibri", color=_RED, bold=True)
_DARK_FONT = Font(name="Calibri", color=_BLUE)
_MONEY = "#,##0.00"
_PCT = "0%"


def _title_block(ws, title: str, mode: str, settings: dict) -> int:
    # Blue logo on white (brand: blue logo on light backgrounds), above the band.
    logo = brand.logo_path(on_dark=False)
    base = 1
    if logo:
        try:
            from openpyxl.drawing.image import Image as XLImage

            img = XLImage(logo)
            img.width, img.height = 200, 41  # keep 1000x206 aspect
            ws.add_image(img, "A1")
            ws.row_dimensions[1].height = 34
            base = 2
        except Exception:
            base = 1

    ws.merge_cells(f"A{base}:F{base}")
    ws[f"A{base}"] = f"{brand.APP_NAME} — {title}"
    ws[f"A{base}"].font = Font(name="Calibri", bold=True, color="FFFFFF", size=16)
    ws[f"A{base}"].fill = _HEADER_FILL
    ws[f"A{base}"].alignment = Alignment(vertical="center", indent=1)
    ws.row_dimensions[base].height = 30

    c = brand.CONTACT
    ws.merge_cells(f"A{base+1}:F{base+1}")
    ws[f"A{base+1}"] = f"{c['business_name']}  |  {c['email']}  |  {c['phone']}"
    ws[f"A{base+1}"].font = Font(name="Calibri", color="FFFFFF", size=9)
    ws[f"A{base+1}"].fill = PatternFill("solid", fgColor=_GREEN)
    ws.row_dimensions[base + 1].height = 16

    row = base + 3
    bits = []
    if "target_customer_savings" in settings:
        bits.append(f"Target customer savings: {settings['target_customer_savings']:.0%}")
    if mode == "internal" and "min_margin_pct" in settings:
        bits.append(f"Min margin floor: {settings['min_margin_pct']:.0%}")
    if bits:
        ws.cell(row, 1, "  •  ".join(bits)).font = Font(name="Calibri", italic=True, color="666666")
        row += 2
    return row


def _kpis(ws, start_row: int, summary: dict, mode: str) -> int:
    r = start_row
    ws.cell(r, 1, "Summary").font = _BLUE_BOLD
    r += 1
    if mode == "internal":
        pairs = [
            ("Shipments", summary["shipments"]),
            ("Serviceable", summary["serviceable"]),
            ("Winnable lanes", summary["winnable"]),
            ("Competitor spend", f"${summary['competitor_total']:,.2f}"),
            ("My cost", f"${summary['my_cost_total']:,.2f}"),
            ("Total margin (if won)", f"${summary['total_margin']:,.2f}"),
        ]
    else:
        pairs = [
            ("Shipments reviewed", summary["shipments"]),
            ("Lanes we can save you on", summary["winnable"]),
            ("Total estimated savings", f"${summary['total_customer_savings']:,.2f}"),
        ]
    for label, val in pairs:
        ws.cell(r, 1, label).font = Font(name="Calibri", color="666666")
        ws.cell(r, 2, val).font = _BLUE_BOLD
        r += 1
    return r + 1


def _autofit(ws, widths: dict[int, int]):
    for col, w in widths.items():
        ws.column_dimensions[get_column_letter(col)].width = w


def build_workbook(records: list[dict], summary: dict, mode: str, settings: dict) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Comparison" if mode == "internal" else "Your savings"
    ws.sheet_view.showGridLines = False

    title = "Internal margin report" if mode == "internal" else "Your savings summary"
    row = _title_block(ws, title, mode, settings)
    row = _kpis(ws, row, summary, mode)

    if mode == "internal":
        headers = ["Tracking", "Competitor svc", "Scope", "They pay", "Best carrier",
                   "My service", "My cost", "Difference", "Status", "Suggested sell",
                   "Margin", "Margin %"]
        keys = ["tracking", "competitor_service", "scope", "competitor_pays", "my_carrier",
                "my_service", "my_cost", "difference", "status", "suggested_sell",
                "margin", "margin_pct"]
        money_cols = {4, 7, 8, 10, 11}
        pct_cols = {12}
        data = records
    else:
        headers = ["Tracking", "Service", "Current price", "Your InXpress price",
                   "You save", "% saved"]
        keys = ["tracking", "competitor_service", "competitor_pays", "suggested_sell",
                "customer_savings", "margin_pct"]
        money_cols = {3, 4, 5}
        pct_cols = {6}
        # customer view: only winnable lanes, and % saved is savings/current
        data = [r for r in records if r["status"] == "LOW" and r["suggested_sell"]]
        for r in data:
            r["_pct_saved"] = (r["customer_savings"] / r["competitor_pays"]) if r["competitor_pays"] else 0

    head_row = row
    for c, h in enumerate(headers, 1):
        cell = ws.cell(head_row, c, h)
        cell.font = _WHITE_BOLD
        cell.fill = _HEADER_FILL
        cell.alignment = Alignment(vertical="center")
    ws.row_dimensions[head_row].height = 20
    row += 1

    for rec in data:
        is_high = rec.get("status") == "HIGH"
        for c, key in enumerate(keys, 1):
            if mode == "customer" and key == "margin_pct":
                val = rec.get("_pct_saved")
            else:
                val = rec.get(key)
            cell = ws.cell(row, c, val)
            if c in money_cols and isinstance(val, (int, float)):
                cell.number_format = _MONEY
            if c in pct_cols and isinstance(val, (int, float)):
                cell.number_format = _PCT
            # red/black convention on difference + status (internal)
            if mode == "internal" and key in ("difference", "status"):
                cell.font = _RED_FONT if is_high else _DARK_FONT
            elif mode == "customer" and key in ("customer_savings", "margin_pct"):
                cell.font = Font(name="Calibri", bold=True, color=_GREEN.upper())
        row += 1

    widths = ({1: 20, 2: 16, 3: 16, 4: 11, 5: 12, 6: 22, 7: 11, 8: 11, 9: 8, 10: 13, 11: 11, 12: 9}
              if mode == "internal" else {1: 20, 2: 18, 3: 13, 4: 17, 5: 11, 6: 9})
    _autofit(ws, widths)
    ws.freeze_panes = ws.cell(head_row + 1, 1)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
