"""Carrier configuration and the unified "best carrier" rater.

Given a shipment and the loaded rate cards, this quotes every carrier/service
that can serve the shipment's scope and returns the cheapest option as MY cost.

  * domestic_parcel        -> Canpar + Purolator (ground/standard parcel)
  * us_bound / international -> DHL  (see rating/dhl.py)

CARRIER-SPECIFIC DIMENSIONAL WEIGHT is applied via rating/dim.py.

>>> PLACEHOLDERS (replace with real account values) <<<
  * FUEL_PCT per carrier     — domestic fuel surcharges are not in the cards.
  * DOMESTIC ZONE resolution — domestic zones depend on origin->destination
    postal and the carrier zone charts (not in the cards). Until those charts
    are loaded, zones are ESTIMATED from destination province for the fixed NS
    origin, and every estimated quote is flagged.
"""
from __future__ import annotations

from app.rating.cards import RateCardData
from app.rating.dim import billable_weight_lb
from app.rating.engine import Quote, ShipmentInput
from app.rating.fuel import fuel_rate

# Standard ground/parcel products to quote per domestic carrier (cheapest wins).
DOMESTIC_PRODUCTS: dict[str, list[str]] = {
    "Canpar": ["Ground Single", "Express Parcel Single", "Select Parcel Single"],
    "Purolator": ["Purolator Ground", "Purolator Express"],
}

# --------------------------------------------------------------------------- #
# >>> PLACEHOLDER domestic zone estimation (origin = NS / Atlantic Canada) <<<
# Canadian postal FIRST letter -> province; province -> estimated zone index
# (1 = local, 16 = farthest). Replace with the carriers' real FSA->zone charts.
# --------------------------------------------------------------------------- #
_POSTAL_PROVINCE = {
    "A": "NL", "B": "NS", "C": "PE", "E": "NB",
    "G": "QC", "H": "QC", "J": "QC",
    "K": "ON", "L": "ON", "M": "ON", "N": "ON", "P": "ON",
    "R": "MB", "S": "SK", "T": "AB", "V": "BC",
    "X": "NT", "Y": "YT",
}
_PROVINCE_ZONE_FROM_NS = {  # PLACEHOLDER estimate from a Nova Scotia origin
    "NS": 1, "PE": 2, "NB": 2, "NL": 4,
    "QC": 6, "ON": 8, "MB": 11, "SK": 12, "AB": 13, "BC": 14,
    "NT": 15, "NU": 16, "YT": 16,
}


def estimate_domestic_zone_index(dest_postal: str | None) -> int | None:
    if not dest_postal:
        return None
    prov = _POSTAL_PROVINCE.get(dest_postal.strip()[:1].upper())
    if prov is None:
        return None
    return _PROVINCE_ZONE_FROM_NS.get(prov)


def _zone_label(carrier: str, idx: int) -> str:
    return f"D{idx:02d}" if carrier == "Purolator" else str(idx)


def quote_domestic(s: ShipmentInput, carrier: str, card: RateCardData) -> Quote | None:
    """Cheapest standard parcel quote for one domestic carrier (or None).

    Uses the carrier's real FSA->zone chart when one has been loaded; otherwise
    falls back to estimating the zone from the destination province.
    """
    from app.rating.zones import resolve_zone

    exact = resolve_zone(carrier, s.dest_postal)
    if exact is not None:
        zone, zone_exact = exact, True
    else:
        idx = estimate_domestic_zone_index(s.dest_postal)
        if idx is None:
            return None
        zone, zone_exact = _zone_label(carrier, idx), False

    best: Quote | None = None
    for prod_name in DOMESTIC_PRODUCTS.get(carrier, []):
        prod = card.get(prod_name)
        if prod is None:
            continue
        bw, wnote = billable_weight_lb(carrier, prod_name, s.actual_weight or s.billed_weight,
                                       s.length, s.width, s.height)
        base, detail = prod.quote_base_cents(zone, bw)
        if base is None:
            continue
        q = Quote(our_carrier=carrier, our_service=prod_name, currency=card.currency)
        ztag = zone if zone_exact else f"{zone}(est)"
        q.add("BASE", f"{carrier} base", base, f"{wnote}; zone {ztag}; {detail}")
        fr = fuel_rate(carrier, prod_name)
        if fr.pct:
            tag = "" if fr.verified else " [est]"
            q.add("FUEL", "Fuel surcharge", round(base * fr.pct),
                  f"{fr.pct:.2%} of base (eff {fr.effective}){tag}")
        if not zone_exact:
            q.warnings.append(f"Domestic zone {zone} ESTIMATED from province (no FSA->zone chart)")
        if best is None or q.cost_cents < best.cost_cents:
            best = q
    return best


def quote_best(s: ShipmentInput, cards: dict[str, RateCardData]) -> Quote | None:
    """Best (cheapest) quote across all applicable carriers for this shipment."""
    candidates: list[Quote] = []

    if s.scope == "domestic_parcel":
        for carrier in ("Canpar", "Purolator"):
            card = cards.get(carrier)
            if card is not None:
                q = quote_domestic(s, carrier, card)
                if q is not None:
                    candidates.append(q)

    elif s.scope in ("us_bound_parcel", "international"):
        from app.rating.dhl import quote_dhl

        card = cards.get("DHL")
        if card is not None:
            q = quote_dhl(s, card)
            if q is not None:
                candidates.append(q)

    if not candidates:
        return None
    return min(candidates, key=lambda q: q.cost_cents)
