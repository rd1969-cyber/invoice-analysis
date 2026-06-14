"""Carrier-specific dimensional ("volumetric") weight.

Each carrier bills the GREATER of actual scale weight and dimensional weight,
where dimensional weight = (L x W x H) / divisor. The divisor differs by carrier
and, for Canpar, by service tier:

    Carrier      in^3/lb   notes
    --------     -------   ----------------------------------------------
    UPS (CA)       139     L*W*H(in)/139  (== 5000 cm^3/kg)
    DHL Express    139     L*W*H(in)/139  (== 5000 cm^3/kg)
    Purolator      139     L*W*H(in)/139  (== 5000 cm^3/kg)
    Canpar Ground  166     12.4 lb/ft^3
    Canpar Select  137     15 lb/ft^3
    Canpar Express 166     (treated as standard)

Sources: carrier published DIM rules (2025-2026). Divisors are configurable here
so they can be corrected per account/service without touching rating code.
"""
from __future__ import annotations

import math

# in^3 per lb. "_default" applies unless a service-specific override matches a
# substring of the product/service name (case-insensitive).
DIM_DIVISORS: dict[str, dict[str, float]] = {
    "UPS": {"_default": 139.0},
    "DHL": {"_default": 139.0},
    "Purolator": {"_default": 139.0},
    "Canpar": {"_default": 166.0, "select": 137.0, "ground": 166.0, "express": 166.0},
}


def divisor_for(carrier: str, service: str | None = None) -> float:
    table = DIM_DIVISORS.get(carrier, {"_default": 139.0})
    if service:
        low = service.lower()
        for key, val in table.items():
            if key != "_default" and key in low:
                return val
    return table["_default"]


def dimensional_weight_lb(length, width, height, divisor: float) -> float | None:
    """L*W*H (inches) / divisor. None if any dimension is missing."""
    if None in (length, width, height) or divisor <= 0:
        return None
    return (length * width * height) / divisor


def billable_weight_lb(
    carrier: str,
    service: str | None,
    actual_lb: float | None,
    length=None,
    width=None,
    height=None,
    round_up: bool = True,
) -> tuple[float, str]:
    """Greater of actual and carrier-specific dimensional weight.

    Returns (billable_lb, explanation). Rounds up to the next whole lb by default
    (rate tables are per whole lb). Minimum 1 lb.
    """
    div = divisor_for(carrier, service)
    actual = actual_lb or 0.0
    dim = dimensional_weight_lb(length, width, height, div)

    if dim is None:
        billable = actual
        note = f"actual {actual:g}lb (no dims; {carrier} DIM/{div:g})"
    else:
        billable = max(actual, dim)
        chosen = "DIM" if dim > actual else "actual"
        note = f"max(actual {actual:g}, DIM {dim:.1f} @/{div:g}) -> {chosen}"

    if round_up:
        billable = max(1.0, math.ceil(billable))
    return billable, note
