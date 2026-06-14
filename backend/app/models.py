"""Normalized data model — the common schema all carrier invoices map into.

Design rules carried by every table:

1. MULTI-TENANCY: every row carries ``tenant_id``. Phase 1 is internal-only
   (a single tenant), but no query is ever written without a tenant filter, so
   going customer-facing later is a config change, not a migration.

2. PROVENANCE: normalized records link back to the raw source they were derived
   from (``RawDocument`` / ``RawLine``) plus a ``parse_confidence`` score. Any
   number on any report can be drilled down to the original document.

Money is stored as integer minor units (cents) to avoid float rounding errors,
exposed through helper properties. All amounts are in the row's ``currency``.
"""
from __future__ import annotations

import datetime as dt
import enum
import uuid

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


# --------------------------------------------------------------------------- #
# Enums
# --------------------------------------------------------------------------- #
class Carrier(str, enum.Enum):
    UPS = "UPS"
    FEDEX = "FedEx"
    PUROLATOR = "Purolator"
    DHL = "DHL"
    FREIGHTCOM = "Freightcom"
    CANADA_POST = "CanadaPost"
    OTHER = "Other"


class ShipmentScope(str, enum.Enum):
    """Rating regime a shipment falls under — drives which rate rules apply."""

    DOMESTIC_PARCEL = "domestic_parcel"
    US_BOUND_PARCEL = "us_bound_parcel"
    INTERNATIONAL = "international"
    RETURN = "return"
    THIRD_PARTY = "third_party"
    LTL = "ltl"


class ParseStatus(str, enum.Enum):
    PENDING = "pending"
    PARSED = "parsed"
    NEEDS_REVIEW = "needs_review"  # low confidence — manual review queue
    REVIEWED = "reviewed"
    FAILED = "failed"


class AccessorialType(str, enum.Enum):
    FUEL = "fuel"
    RESIDENTIAL = "residential"
    DELIVERY_AREA_SURCHARGE = "das"
    OVERSIZE = "oversize"
    ADDITIONAL_HANDLING = "additional_handling"
    SIGNATURE = "signature"
    BROKERAGE = "brokerage"
    CUSTOMS = "customs"
    ADDRESS_CORRECTION = "address_correction"
    DIM_ADJUSTMENT = "dim_adjustment"
    TAX = "tax"
    DISCOUNT = "discount"
    OTHER = "other"


# --------------------------------------------------------------------------- #
# Tenancy
# --------------------------------------------------------------------------- #
class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_internal: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())


class TenantMixin:
    """Mixin that puts a tenant_id FK on every business table."""

    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenants.id"), nullable=False, index=True
    )


# --------------------------------------------------------------------------- #
# Provenance: raw uploaded source
# --------------------------------------------------------------------------- #
class RawDocument(Base, TenantMixin):
    """An uploaded file (PDF/CSV/Excel) before any normalization."""

    __tablename__ = "raw_documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    content_type: Mapped[str] = mapped_column(String(128), nullable=True)
    storage_path: Mapped[str] = mapped_column(String(1024), nullable=True)
    sha256: Mapped[str] = mapped_column(String(64), nullable=True, index=True)  # dedupe
    detected_carrier: Mapped[Carrier | None] = mapped_column(Enum(Carrier), nullable=True)
    parser_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    parse_status: Mapped[ParseStatus] = mapped_column(
        Enum(ParseStatus), default=ParseStatus.PENDING, index=True
    )
    uploaded_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())

    lines: Mapped[list["RawLine"]] = relationship(back_populates="document")


class RawLine(Base, TenantMixin):
    """A raw row/line extracted from a document, kept verbatim for audit.

    ``payload`` holds the original cells/text as JSON-ish text so a reviewer can
    always see exactly what the parser saw.
    """

    __tablename__ = "raw_lines"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    document_id: Mapped[str] = mapped_column(ForeignKey("raw_documents.id"), index=True)
    line_number: Mapped[int] = mapped_column(Integer)
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    payload: Mapped[str] = mapped_column(Text)  # original cells/text, JSON-encoded

    document: Mapped[RawDocument] = relationship(back_populates="lines")


# --------------------------------------------------------------------------- #
# Normalized: invoices
# --------------------------------------------------------------------------- #
class Invoice(Base, TenantMixin):
    __tablename__ = "invoices"
    __table_args__ = (
        UniqueConstraint("tenant_id", "carrier", "invoice_number", name="uq_invoice_per_tenant"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    source_document_id: Mapped[str | None] = mapped_column(
        ForeignKey("raw_documents.id"), nullable=True, index=True
    )

    invoice_number: Mapped[str] = mapped_column(String(128), index=True)
    invoice_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    due_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    account_number: Mapped[str | None] = mapped_column(String(128), nullable=True)
    carrier: Mapped[Carrier] = mapped_column(Enum(Carrier), index=True)
    currency: Mapped[str] = mapped_column(String(3), default="CAD")

    total_spend_cents: Mapped[int] = mapped_column(Integer, default=0)
    tax_cents: Mapped[int] = mapped_column(Integer, default=0)

    parse_confidence: Mapped[float] = mapped_column(Float, default=1.0)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())

    shipments: Mapped[list["Shipment"]] = relationship(back_populates="invoice")

    @property
    def total_spend(self) -> float:
        return self.total_spend_cents / 100


# --------------------------------------------------------------------------- #
# Normalized: shipments
# --------------------------------------------------------------------------- #
class Shipment(Base, TenantMixin):
    __tablename__ = "shipments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    invoice_id: Mapped[str] = mapped_column(ForeignKey("invoices.id"), index=True)
    source_line_id: Mapped[str | None] = mapped_column(
        ForeignKey("raw_lines.id"), nullable=True
    )

    # Identity / routing
    tracking_number: Mapped[str | None] = mapped_column(String(128), index=True)
    reference: Mapped[str | None] = mapped_column(String(256), nullable=True)  # RMA/ticket/PO
    carrier: Mapped[Carrier] = mapped_column(Enum(Carrier), index=True)
    service: Mapped[str | None] = mapped_column(String(128), nullable=True)
    scope: Mapped[ShipmentScope] = mapped_column(
        Enum(ShipmentScope), default=ShipmentScope.DOMESTIC_PARCEL, index=True
    )
    ship_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)

    origin_postal: Mapped[str | None] = mapped_column(String(16), nullable=True)
    dest_postal: Mapped[str | None] = mapped_column(String(16), nullable=True)
    dest_country: Mapped[str | None] = mapped_column(String(2), nullable=True)
    zone: Mapped[str | None] = mapped_column(String(16), nullable=True)

    # Weight / dims
    actual_weight: Mapped[float | None] = mapped_column(Float, nullable=True)
    billed_weight: Mapped[float | None] = mapped_column(Float, nullable=True)
    weight_unit: Mapped[str] = mapped_column(String(3), default="lb")
    length: Mapped[float | None] = mapped_column(Float, nullable=True)
    width: Mapped[float | None] = mapped_column(Float, nullable=True)
    height: Mapped[float | None] = mapped_column(Float, nullable=True)
    dim_unit: Mapped[str] = mapped_column(String(3), default="in")
    package_count: Mapped[int] = mapped_column(Integer, default=1)

    # Charges (cents). Accessorials are itemized in the Accessorial table; the
    # common ones are also denormalized here for fast querying/reporting.
    currency: Mapped[str] = mapped_column(String(3), default="CAD")
    base_charge_cents: Mapped[int] = mapped_column(Integer, default=0)
    fuel_cents: Mapped[int] = mapped_column(Integer, default=0)
    residential_cents: Mapped[int] = mapped_column(Integer, default=0)
    das_cents: Mapped[int] = mapped_column(Integer, default=0)
    oversize_cents: Mapped[int] = mapped_column(Integer, default=0)
    signature_cents: Mapped[int] = mapped_column(Integer, default=0)
    brokerage_cents: Mapped[int] = mapped_column(Integer, default=0)
    other_accessorial_cents: Mapped[int] = mapped_column(Integer, default=0)
    tax_cents: Mapped[int] = mapped_column(Integer, default=0)
    total_charge_cents: Mapped[int] = mapped_column(Integer, default=0)

    parse_confidence: Mapped[float] = mapped_column(Float, default=1.0)

    invoice: Mapped[Invoice] = relationship(back_populates="shipments")
    accessorials: Mapped[list["Accessorial"]] = relationship(back_populates="shipment")
    quotes: Mapped[list["RateQuote"]] = relationship(back_populates="shipment")

    @property
    def total_charge(self) -> float:
        return self.total_charge_cents / 100


class Accessorial(Base, TenantMixin):
    __tablename__ = "accessorials"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    shipment_id: Mapped[str] = mapped_column(ForeignKey("shipments.id"), index=True)
    type: Mapped[AccessorialType] = mapped_column(Enum(AccessorialType), index=True)
    amount_cents: Mapped[int] = mapped_column(Integer, default=0)
    description: Mapped[str | None] = mapped_column(String(256), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    shipment: Mapped[Shipment] = relationship(back_populates="accessorials")


# --------------------------------------------------------------------------- #
# Rate engine output: what a shipment WOULD cost under our rates
# --------------------------------------------------------------------------- #
class RateQuote(Base, TenantMixin):
    """A re-rated cost for a shipment under one of our carrier/service options.

    ``breakdown`` stores the full line-item math as JSON so the quote is fully
    auditable (this is how a savings number drills down to its derivation).
    """

    __tablename__ = "rate_quotes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    shipment_id: Mapped[str] = mapped_column(ForeignKey("shipments.id"), index=True)

    rate_card_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    our_carrier: Mapped[Carrier] = mapped_column(Enum(Carrier))
    our_service: Mapped[str] = mapped_column(String(128))
    currency: Mapped[str] = mapped_column(String(3), default="CAD")

    # Our cost (what we pay the carrier) vs our sell (what we'd charge the customer)
    our_cost_cents: Mapped[int] = mapped_column(Integer, default=0)
    our_sell_cents: Mapped[int] = mapped_column(Integer, default=0)

    competitor_total_cents: Mapped[int] = mapped_column(Integer, default=0)  # snapshot
    estimated_savings_cents: Mapped[int] = mapped_column(Integer, default=0)
    estimated_margin_cents: Mapped[int] = mapped_column(Integer, default=0)

    is_best_option: Mapped[bool] = mapped_column(Boolean, default=False)
    breakdown: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON line-items
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())

    shipment: Mapped[Shipment] = relationship(back_populates="quotes")
