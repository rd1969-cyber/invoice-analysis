"""Domestic zone resolution from carrier FSA->zone charts.

Domestic parcel rates depend on a zone determined by the destination postal code
(for a fixed origin). Carriers publish this as an FSA->zone chart (FSA = the
first 3 chars of a Canadian postal code, e.g. "B4C"). Those charts are not in the
rate cards, so until one is loaded the app estimates the zone from the
destination province (see carriers.py). Once you upload a chart, exact zones are
used.

Charts can be dropped in as Excel or PDF; the loader scans for (FSA, zone) pairs
regardless of exact layout (two-column lists are the common form). Loaded charts
live in a module registry the UI populates, mirroring how fuel rates work.
"""
from __future__ import annotations

import re

from app.rating.cards import Grid, _read_pdf, _read_xls, _read_xlsx, _zone_label

_FSA_RE = re.compile(r"^[A-Za-z]\d[A-Za-z]$")          # e.g. B4C
_POSTAL_RE = re.compile(r"^[A-Za-z]\d[A-Za-z]\s?\d[A-Za-z]\d$")  # full postal
_ZONE_RE = re.compile(r"^(?:[A-Za-z]?\d{1,2}|[A-Za-z]\d{2}|N\d{1,2}|D\d{2})$")

# carrier -> {FSA: zone_label}
ZONE_CHARTS: dict[str, dict[str, str]] = {}


def _fsa(value: str) -> str | None:
    v = str(value).strip().upper().replace(" ", "")
    if _FSA_RE.match(v):
        return v
    if _POSTAL_RE.match(v):
        return v[:3]
    return None


def _looks_like_zone(value) -> bool:
    return bool(_ZONE_RE.match(str(value).strip()))


def parse_zone_grid(rows: Grid) -> dict[str, str]:
    """Extract an FSA->zone mapping from a grid (Excel/PDF).

    For each row, find a cell that is an FSA (or full postal) and pair it with the
    first zone-looking cell in the same row.
    """
    mapping: dict[str, str] = {}
    for row in rows:
        cells = [c for c in row if c is not None and str(c).strip() != ""]
        fsa = None
        zone = None
        for c in cells:
            f = _fsa(c)
            if f and fsa is None:
                fsa = f
                continue
            if fsa is not None and zone is None and _looks_like_zone(c):
                zone = _zone_label(c)
        if fsa and zone:
            mapping[fsa] = zone
    return mapping


def load_zone_chart(path: str) -> dict[str, str]:
    ext = path.lower().rsplit(".", 1)[-1]
    if ext == "xls":
        grids = _read_xls(path)
    elif ext in ("xlsx", "xlsm"):
        grids = _read_xlsx(path)
    elif ext == "pdf":
        grids = _read_pdf(path)
    else:
        raise ValueError(f"Unsupported zone-chart format: .{ext}")
    mapping: dict[str, str] = {}
    for grid in grids.values():
        mapping.update(parse_zone_grid(grid))
    return mapping


def set_chart(carrier: str, mapping: dict[str, str]) -> None:
    ZONE_CHARTS[carrier] = mapping


def resolve_zone(carrier: str, dest_postal: str | None) -> str | None:
    """Exact zone from a loaded chart, or None if no chart / FSA not found."""
    if not dest_postal:
        return None
    chart = ZONE_CHARTS.get(carrier)
    if not chart:
        return None
    fsa = str(dest_postal).strip().upper().replace(" ", "")[:3]
    return chart.get(fsa)
