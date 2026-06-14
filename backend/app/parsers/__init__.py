"""Document parsers — turn raw uploads into normalized records.

Strategy (per the architecture):

  1. Detect carrier / invoice type from the document.
  2. Run the matching carrier-specific parser (high accuracy, structured input).
  3. If none matches, or confidence is low, fall back to the AI parser.
  4. Attach a confidence score; low-confidence records route to manual review.

Concrete parsers are written against your real sample files. This module defines
the stable interface they implement.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class ParsedShipment:
    """Normalized shipment fields a parser extracts (carrier-agnostic)."""

    tracking_number: str | None = None
    reference: str | None = None
    service: str | None = None
    ship_date: str | None = None  # ISO; converted to date on persist
    origin_postal: str | None = None
    dest_postal: str | None = None
    dest_country: str | None = None
    actual_weight: float | None = None
    billed_weight: float | None = None
    length: float | None = None
    width: float | None = None
    height: float | None = None
    package_count: int = 1
    base_charge_cents: int = 0
    fuel_cents: int = 0
    accessorials: list[dict] = field(default_factory=list)  # {type, amount_cents, desc}
    tax_cents: int = 0
    total_charge_cents: int = 0
    field_confidence: dict[str, float] = field(default_factory=dict)
    source_line_index: int | None = None


@dataclass
class ParsedInvoice:
    invoice_number: str | None = None
    invoice_date: str | None = None
    due_date: str | None = None
    account_number: str | None = None
    carrier: str | None = None
    currency: str = "CAD"
    total_spend_cents: int = 0
    tax_cents: int = 0
    shipments: list[ParsedShipment] = field(default_factory=list)
    confidence: float = 1.0


class Parser(Protocol):
    name: str

    def can_parse(self, filename: str, sample_text: str) -> float:
        """Return 0..1 confidence that this parser handles the document."""
        ...

    def parse(self, path: str) -> list[ParsedInvoice]:
        ...
