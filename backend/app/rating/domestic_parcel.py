"""Domestic parcel rater (MVP scope).

This implements the *shape* of domestic parcel rating against our rate card:

    billable weight  = max(actual, dimensional)         (DIM weight)
    base charge      = lookup(service, zone, billable weight)
    fuel             = base * fuel_pct
    accessorials     = residential, signature, DAS, ...  (rules from the card)
    cost             = base + fuel + accessorials

The concrete rate tables, zone matrix, fuel schedule, and accessorial fees are
loaded from the real rate card once available. Until then the lookups raise a
clear "not yet loaded" error rather than guessing — the rate engine must never
fabricate numbers.
"""
from __future__ import annotations

import math

from app.rating.engine import Quote, RateCard, Rater, ShipmentInput, register

# Standard DIM divisors if the card doesn't specify (in^3 per lb).
DEFAULT_DIM_DIVISOR_IN_LB = 139.0


def dimensional_weight(s: ShipmentInput, divisor: float) -> float | None:
    """L x W x H / divisor. Returns None if dimensions are missing."""
    if None in (s.length, s.width, s.height) or divisor <= 0:
        return None
    return (s.length * s.width * s.height) / divisor


def billable_weight(s: ShipmentInput, divisor: float) -> tuple[float | None, str]:
    """max(actual, dimensional), with a human-readable explanation."""
    dim = dimensional_weight(s, divisor)
    actual = s.actual_weight
    if actual is None and dim is None:
        return None, "no weight or dimensions available"
    if dim is None:
        return actual, f"actual {actual} {s.weight_unit} (no dims for DIM)"
    if actual is None:
        return math.ceil(dim), f"DIM {dim:.1f} (LxWxH/{divisor:g}); no actual weight"
    billable = max(actual, math.ceil(dim))
    chosen = "DIM" if math.ceil(dim) > actual else "actual"
    return billable, f"max(actual {actual}, DIM {dim:.1f}) -> {chosen} {billable}"


class DomesticParcelRater(Rater):
    scope = "domestic_parcel"

    def rate(self, s: ShipmentInput, card: RateCard) -> list[Quote]:
        divisor = card.dim_divisor or DEFAULT_DIM_DIVISOR_IN_LB
        bw, bw_detail = billable_weight(s, divisor)

        # Which of our services to quote: those in the card, else the input service.
        services = card.services or ([s.service] if s.service else [])
        if not services:
            return []

        quotes: list[Quote] = []
        for service in services:
            q = Quote(our_carrier=card.carrier, our_service=service, currency=card.currency)

            if bw is None:
                q.warnings.append("Cannot rate: no weight or dimensions.")
                quotes.append(q)
                continue

            base_cents = self._base_rate_cents(card, service, s.zone, bw)
            if base_cents is None:
                q.warnings.append(
                    f"No base rate loaded for service={service}, zone={s.zone}, "
                    f"weight={bw}. Load the rate card to enable this lookup."
                )
                quotes.append(q)
                continue

            q.add("BASE", f"Base ({service})", base_cents, f"{bw_detail}; zone {s.zone}")

            if card.fuel_pct:
                fuel_cents = round(base_cents * card.fuel_pct)
                q.add("FUEL", "Fuel surcharge", fuel_cents, f"{card.fuel_pct:.1%} of base")

            for code, label, cents, detail in self._accessorials(card, s):
                q.add(code, label, cents, detail)

            quotes.append(q)
        return quotes

    # ----- card-driven lookups (stubs until a real card is loaded) --------- #
    def _base_rate_cents(
        self, card: RateCard, service: str, zone: str | None, billable_wt: float
    ) -> int | None:
        """Look up the base rate from the card's zone x weight table.

        Returns None until the real card is loaded. Wired to ``card.raw`` once
        we know the card's structure (e.g. card.raw['rates'][service][zone]).
        """
        rates = card.raw.get("rates") if card.raw else None
        if not rates:
            return None
        # Expected shape (finalized against the real card):
        #   rates[service][zone] -> list of (max_weight, price_cents) breakpoints
        table = rates.get(service, {}).get(str(zone))
        if not table:
            return None
        for max_weight, price_cents in table:
            if billable_wt <= max_weight:
                return int(price_cents)
        return None  # over the top breakpoint -> per-lb overage handled later

    def _accessorials(self, card: RateCard, s: ShipmentInput):
        """Yield (code, label, cents, detail) for applicable accessorials.

        Driven by the card's accessorial schedule. Empty until loaded.
        """
        acc = (card.raw or {}).get("accessorials", {})
        if s.residential and "residential" in acc:
            yield "RESI", "Residential", int(acc["residential"]), "residential delivery"
        if s.signature and "signature" in acc:
            yield "SIG", "Signature", int(acc["signature"]), "signature required"
        # DAS, oversize, additional handling, etc. added against the real card.


register(DomesticParcelRater())
