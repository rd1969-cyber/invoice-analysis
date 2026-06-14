"""DHL rater — computes OUR cost to ship a parcel under the DHL/InXpress card.

Covers US-bound and international parcels (the scopes the DHL card supports).
Quotes every applicable DHL product and returns the cheapest as the best option.

TWO INPUTS ARE NOT IN THE RATE CARD and are currently PLACEHOLDERS — replace
with your real values when you have them:

  * COUNTRY -> DHL ZONE map (DHL Express Worldwide uses zones N1-N14). Economy
    Select is single-zone N1 = US, so US-bound is already defensible.
  * DHL FUEL SURCHARGE % (published monthly by DHL).

Everything is clearly flagged so the numbers are honest about what's real vs
placeholder.
"""
from __future__ import annotations

import math

from app.rating.dhl_card import RateCardData
from app.rating.engine import Quote, ShipmentInput

# --------------------------------------------------------------------------- #
# >>> PLACEHOLDERS — replace with your real DHL account values <<<
# --------------------------------------------------------------------------- #
DHL_FUEL_PCT = 0.27  # PLACEHOLDER: current DHL fuel surcharge (e.g. 0.27 = 27%)

# PLACEHOLDER country -> DHL Express Worldwide zone. US is anchored by Economy
# Select (single-zone N1 = US). The rest need your published DHL zone chart.
COUNTRY_TO_ZONE: dict[str, str] = {
    "US": "N1",   # anchored: Economy Select N1 == US
    "GB": "N5",   # PLACEHOLDER
    "DE": "N5",   # PLACEHOLDER
    "FR": "N5",   # PLACEHOLDER
    "AU": "N9",   # PLACEHOLDER
}
DEFAULT_ZONE = "N5"  # PLACEHOLDER fallback for unmapped countries

DIM_DIVISOR_IN_LB = 139.0  # DHL dimensional divisor (in^3 per lb)


def dhl_billable_weight(s: ShipmentInput) -> tuple[float, str]:
    """max(actual, dimensional), rounded UP to the next whole lb (card is per-lb)."""
    actual = s.actual_weight or s.billed_weight or 0.0
    dim = None
    if None not in (s.length, s.width, s.height):
        dim = (s.length * s.width * s.height) / DIM_DIVISOR_IN_LB
    billable = max(actual, dim) if dim else actual
    billable = max(1.0, math.ceil(billable))
    note = f"actual {actual:g}" + (f" vs DIM {dim:.1f}" if dim else "") + f" -> {billable:g}lb"
    return billable, note


def _candidate_products(scope: str, weight: float) -> list[str]:
    """Which DHL products to quote for this shipment."""
    if scope == "us_bound_parcel":
        prods = ["DHL Express Worldwide - Package"]
        if weight >= 23:  # Economy Select only exists from 23 lb
            prods.append("DHL Economy Select - Package")
        return prods
    if scope == "international":
        return ["DHL Express Worldwide - Package"]
    return []  # domestic: no DHL product


def quote_dhl(
    s: ShipmentInput,
    card: RateCardData,
    fuel_pct: float = DHL_FUEL_PCT,
    zone_map: dict[str, str] | None = None,
) -> Quote | None:
    """Return the cheapest applicable DHL quote, or None if DHL can't serve it."""
    zone_map = zone_map or COUNTRY_TO_ZONE
    weight, wnote = dhl_billable_weight(s)
    products = _candidate_products(s.scope, weight)
    if not products:
        return None

    zone = "N1" if s.scope == "us_bound_parcel" else zone_map.get(
        (s.dest_country or "").upper(), DEFAULT_ZONE
    )
    zone_is_placeholder = not (s.scope == "us_bound_parcel" or
                               (s.dest_country or "").upper() in zone_map)

    best: Quote | None = None
    for prod_name in products:
        prod = card.get(prod_name)
        if prod is None:
            continue
        base_cents, detail = prod.quote_base_cents(zone, weight)
        if base_cents is None:
            continue
        q = Quote(our_carrier="DHL", our_service=prod_name, currency=card.currency)
        q.add("BASE", "DHL base", base_cents, f"{wnote}; zone {zone}; {detail}")
        if fuel_pct:
            q.add("FUEL", "Fuel surcharge", round(base_cents * fuel_pct),
                  f"{fuel_pct:.1%} of base [PLACEHOLDER %]")
        if zone_is_placeholder:
            q.warnings.append(f"Zone {zone} for {s.dest_country} is a PLACEHOLDER")
        if best is None or q.cost_cents < best.cost_cents:
            best = q
    return best
