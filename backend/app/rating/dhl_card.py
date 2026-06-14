"""Loader for the DHL / InXpress rate card (.xls).

The workbook has three sheets: OUTBOUND (CA -> world, weight in lb, zones N1-N14),
INBOUND (world -> CA, kg, N1-N5), DIFFERENT (third party, kg, A-H). Each sheet is
a stack of product blocks:

    Prepared for ... INXPRESS
    <Product name>            e.g. "DHL Express Worldwide - Package"
    Value in CAD
    Weight(lb)  N1  N2 ... N14   <- header row with zone columns
    1.0   <price>  <price> ...    <- weight breakpoint rows
    ...
    Non-Document above 200 lb (Multiply shipment weight by zone rate)
    Weight(lb)  N1 ...
    200.1  <per-lb rate>          <- overage: price per lb above the breakpoint

This loader returns a structured ``RateCardData`` the DHL rater consumes. Prices
are stored as integer cents.

NOTE: the .xls is OLE2 but trips a known xlrd false-positive, so we open with
``ignore_workbook_corruption=True``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

import xlrd


def _cents(v) -> int | None:
    if v is None or v == "":
        return None
    if isinstance(v, str):
        v = v.replace(",", "").strip()
        if not v:
            return None
    try:
        return int((Decimal(str(v)) * 100).to_integral_value())
    except Exception:
        return None


@dataclass
class Product:
    name: str
    unit: str  # "lb" or "kg"
    zones: list[str]  # e.g. ["N1", ..., "N14"]
    # breakpoints[zone] -> sorted list of (max_weight, price_cents)
    breakpoints: dict[str, list[tuple[float, int]]] = field(default_factory=dict)
    # overage[zone] -> price_cents_per_unit above the top breakpoint
    overage: dict[str, int] = field(default_factory=dict)

    def quote_base_cents(self, zone: str, weight: float) -> tuple[int | None, str]:
        bp = self.breakpoints.get(zone)
        if not bp:
            return None, f"zone {zone} not in product {self.name}"
        for max_w, price in bp:
            if weight <= max_w:
                return price, f"{weight:g}{self.unit} <= {max_w:g} band @ {self.name}/{zone}"
        ov = self.overage.get(zone)
        if ov is not None:
            return int(round(ov * weight)), f"{weight:g}{self.unit} x {ov/100:.2f}/{self.unit} overage"
        return None, f"{weight:g}{self.unit} exceeds table for {self.name}/{zone}"


@dataclass
class RateCardData:
    carrier: str = "DHL"
    currency: str = "CAD"
    products: dict[str, Product] = field(default_factory=dict)

    def get(self, name: str) -> Product | None:
        return self.products.get(name)


def _is_num(v) -> bool:
    if isinstance(v, (int, float)):
        return True
    if isinstance(v, str):
        try:
            float(v.replace(",", ""))
            return True
        except ValueError:
            return False
    return False


def load_sheet(path: str, sheet_name: str) -> RateCardData:
    bk = xlrd.open_workbook(path, ignore_workbook_corruption=True)
    sh = bk.sheet_by_name(sheet_name)
    card = RateCardData()

    r = 0
    current: Product | None = None
    is_overage = False
    while r < sh.nrows:
        row = [sh.cell_value(r, c) for c in range(sh.ncols)]
        c0 = str(row[0]).strip()

        if c0.upper().startswith("DHL"):
            unit = "kg" if "kg" in "".join(str(x) for x in row).lower() else "lb"
            current = Product(name=c0, unit=unit, zones=[])
            card.products[c0] = current
            is_overage = False
            r += 1
            continue

        if c0.startswith("Weight("):
            current_zones = [str(row[c]).strip() for c in range(1, sh.ncols) if str(row[c]).strip()]
            if current is not None:
                current.unit = "kg" if "kg" in c0.lower() else "lb"
                if not current.zones:
                    current.zones = current_zones
            r += 1
            continue

        if "above" in c0.lower() and "lb" in c0.lower():
            is_overage = True  # next Weight() + numeric rows are per-lb overage
            r += 1
            continue

        if current is not None and _is_num(row[0]):
            weight = float(str(row[0]).replace(",", ""))
            for i, zone in enumerate(current.zones):
                price = _cents(row[i + 1]) if i + 1 < len(row) else None
                if price is None:
                    continue
                if is_overage:
                    current.overage[zone] = price
                else:
                    current.breakpoints.setdefault(zone, []).append((weight, price))
            r += 1
            continue

        r += 1

    # sort breakpoints by weight
    for prod in card.products.values():
        for zone in prod.breakpoints:
            prod.breakpoints[zone].sort()
    return card


def load_outbound(path: str) -> RateCardData:
    return load_sheet(path, "OUTBOUND")
