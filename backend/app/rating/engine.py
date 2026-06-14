"""Core rate-engine types and orchestration.

The engine takes a normalized shipment and a rate card, and produces a fully
itemized quote. The math lives in scope-specific raters (domestic parcel,
US-bound, international, ...) registered against ``ShipmentScope``.

Money is handled in whole cents (int) end-to-end to stay reproducible.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field

# Imported lazily where needed to avoid a hard dependency on the ORM in pure
# rating logic / unit tests.


@dataclass
class ShipmentInput:
    """The minimal, ORM-free shipment facts the engine needs to rate.

    Keeping this separate from the ``Shipment`` ORM model means the rate engine
    can be unit-tested in isolation with plain dicts/objects.
    """

    scope: str  # matches models.ShipmentScope value
    service: str | None
    origin_postal: str | None
    dest_postal: str | None
    dest_country: str | None
    actual_weight: float | None
    billed_weight: float | None
    weight_unit: str = "lb"
    length: float | None = None
    width: float | None = None
    height: float | None = None
    dim_unit: str = "in"
    package_count: int = 1
    residential: bool = False
    signature: bool = False
    zone: str | None = None


@dataclass
class LineItem:
    """One auditable component of a quote (base, fuel, an accessorial, ...)."""

    code: str
    label: str
    amount_cents: int
    detail: str = ""  # human-readable derivation, e.g. "55 lb @ zone 3 = $42.10"


@dataclass
class Quote:
    """The engine's output for one carrier/service option."""

    our_carrier: str
    our_service: str
    currency: str
    line_items: list[LineItem] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def cost_cents(self) -> int:
        return sum(li.amount_cents for li in self.line_items)

    def add(self, code: str, label: str, amount_cents: int, detail: str = "") -> None:
        self.line_items.append(LineItem(code, label, amount_cents, detail))

    def to_breakdown(self) -> dict:
        """Serializable breakdown stored on RateQuote.breakdown for drill-down."""
        return {
            "carrier": self.our_carrier,
            "service": self.our_service,
            "currency": self.currency,
            "cost_cents": self.cost_cents,
            "line_items": [dataclasses.asdict(li) for li in self.line_items],
            "warnings": self.warnings,
        }


# --------------------------------------------------------------------------- #
# Rater registry
# --------------------------------------------------------------------------- #
class Rater:
    """Base class for a scope-specific rater. Subclasses implement ``rate``."""

    scope: str = ""

    def rate(self, shipment: ShipmentInput, rate_card: "RateCard") -> list[Quote]:
        raise NotImplementedError


_RATERS: dict[str, Rater] = {}


def register(rater: Rater) -> Rater:
    _RATERS[rater.scope] = rater
    return rater


def rate_shipment(shipment: ShipmentInput, rate_card: "RateCard") -> list[Quote]:
    """Rate a shipment using the rater registered for its scope."""
    rater = _RATERS.get(shipment.scope)
    if rater is None:
        raise NotImplementedError(f"No rater registered for scope '{shipment.scope}'")
    return rater.rate(shipment, rate_card)


# --------------------------------------------------------------------------- #
# Rate card abstraction (concrete loading lands once we see a real card)
# --------------------------------------------------------------------------- #
@dataclass
class RateCard:
    """A parsed rate card. The concrete structure (zone tables, fuel schedule,
    accessorial rules, DIM divisor, discounts) is filled in once we see your
    real InXpress/carrier card — this is the stable interface the raters use.
    """

    card_id: str
    carrier: str
    currency: str = "CAD"
    # Populated from the real card:
    services: list[str] = field(default_factory=list)
    dim_divisor: float | None = None  # e.g. 139 (in^3/lb) or 166
    fuel_pct: float = 0.0  # current fuel surcharge as a fraction, e.g. 0.155
    raw: dict = field(default_factory=dict)  # full parsed card for rater use
