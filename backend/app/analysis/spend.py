"""Spend analysis over normalized invoices.

Computes the executive metrics from the spec directly off parsed invoices:
total spend, spend by service / scope, fuel as % of spend, accessorial leakage,
residential & DAS exposure, and the most expensive shipments. Pure aggregation
over ``ParsedInvoice`` objects so it works before the DB layer is wired in.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from app.parsers import ParsedInvoice, ParsedShipment


@dataclass
class SpendReport:
    invoice_count: int = 0
    shipment_count: int = 0
    total_billed_cents: int = 0
    fuel_cents: int = 0
    accessorial_cents: int = 0  # all accessorials incl. fuel
    residential_count: int = 0
    residential_cents: int = 0
    by_service: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    by_scope: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    by_accessorial: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    tax_cents: int = 0
    by_tax: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    top_shipments: list[ParsedShipment] = field(default_factory=list)
    low_confidence_count: int = 0

    @property
    def avg_cost_per_shipment(self) -> float:
        return (self.total_billed_cents / self.shipment_count / 100) if self.shipment_count else 0.0

    @property
    def fuel_pct(self) -> float:
        return (self.fuel_cents / self.total_billed_cents) if self.total_billed_cents else 0.0


def _scope(s: ParsedShipment) -> str:
    from app.parsers.ups import _classify_scope

    return _classify_scope(s.dest_postal or "", s.dest_country, s.service or "")


# Accessorial columns broken out per shipment (order = display order).
ACCESSORIAL_COLUMNS = [
    "fuel", "residential", "das", "signature", "brokerage", "customs",
    "additional_handling", "oversize", "address_correction", "other",
]
ACCESSORIAL_LABELS = {
    "fuel": "Fuel", "residential": "Residential", "das": "DAS",
    "signature": "Signature", "brokerage": "Brokerage", "customs": "Customs",
    "additional_handling": "Add'l handling", "oversize": "Oversize",
    "address_correction": "Addr correction", "other": "Other acc.",
}


def shipment_breakdown(s: ParsedShipment) -> dict[str, float]:
    """Per-shipment charge breakdown in dollars: base, each accessorial type, total.

    (Taxes are invoice-level on UPS, not per shipment — see SpendReport.by_tax.)
    """
    acc = {k: 0 for k in ACCESSORIAL_COLUMNS}
    for a in s.accessorials:
        t = a["type"] if a["type"] in acc else "other"
        acc[t] += a["amount_cents"]
    out = {"base": s.base_charge_cents / 100}
    out.update({k: acc[k] / 100 for k in ACCESSORIAL_COLUMNS})
    out["tax"] = s.tax_cents / 100
    out["total"] = s.total_charge_cents / 100
    return out


def analyze(invoices: list[ParsedInvoice], top_n: int = 10) -> SpendReport:
    r = SpendReport(invoice_count=len(invoices))
    all_ships: list[ParsedShipment] = []

    for inv in invoices:
        r.tax_cents += inv.tax_cents
        for kind, amt in (inv.taxes or {}).items():
            r.by_tax[kind] += amt
        for s in inv.shipments:
            all_ships.append(s)
            r.shipment_count += 1
            r.total_billed_cents += s.total_charge_cents
            r.by_service[s.service or "Unknown"] += s.total_charge_cents
            r.by_scope[_scope(s)] += s.total_charge_cents
            if s.field_confidence.get("total_reconciled", 0) != 1:
                r.low_confidence_count += 1
            for a in s.accessorials:
                r.accessorial_cents += a["amount_cents"]
                r.by_accessorial[a["type"]] += a["amount_cents"]
                if a["type"] == "fuel":
                    r.fuel_cents += a["amount_cents"]
                if a["type"] == "residential":
                    r.residential_count += 1
                    r.residential_cents += a["amount_cents"]

    r.top_shipments = sorted(all_ships, key=lambda s: s.total_charge_cents, reverse=True)[:top_n]
    return r


def format_report(r: SpendReport) -> str:
    def money(c: int) -> str:
        return f"${c / 100:,.2f}"

    lines = [
        "=" * 64,
        "FREIGHT SPEND ANALYSIS",
        "=" * 64,
        f"Invoices analyzed:        {r.invoice_count}",
        f"Shipments:                {r.shipment_count}",
        f"Total shipment spend:     {money(r.total_billed_cents)}",
        f"Avg cost / shipment:      ${r.avg_cost_per_shipment:,.2f}",
        f"Fuel surcharge:           {money(r.fuel_cents)}  ({r.fuel_pct:.1%} of spend)",
        f"All accessorials:         {money(r.accessorial_cents)}",
        f"Residential deliveries:   {r.residential_count}  ({money(r.residential_cents)})",
        f"Low-confidence (review):  {r.low_confidence_count}",
        "",
        "Spend by scope:",
    ]
    for k, v in sorted(r.by_scope.items(), key=lambda x: -x[1]):
        lines.append(f"   {k:20} {money(v):>12}")
    lines.append("")
    lines.append("Spend by service (top 8):")
    for k, v in sorted(r.by_service.items(), key=lambda x: -x[1])[:8]:
        lines.append(f"   {k:24} {money(v):>12}")
    lines.append("")
    lines.append("Accessorial leakage by type:")
    for k, v in sorted(r.by_accessorial.items(), key=lambda x: -x[1]):
        lines.append(f"   {k:24} {money(v):>12}")
    lines.append("")
    lines.append("Top 10 most expensive shipments:")
    for s in r.top_shipments:
        lines.append(
            f"   {s.tracking_number}  {(s.service or '')[:18]:18} "
            f"{(s.dest_postal or '-'):9} {money(s.total_charge_cents):>10}"
        )
    lines.append("=" * 64)
    return "\n".join(lines)
