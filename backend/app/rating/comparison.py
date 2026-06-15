"""Comparison & margin engine.

For each shipment, this puts three numbers side by side:

  * what the customer PAYS today (the competitor's net charge on their invoice)
  * what it COSTS ME to carry it under my carrier (DHL) rate card
  * the DIFFERENCE  =  what_they_pay - my_cost

Sign convention (matches the requested red/black):

  * my cost is HIGH  (my_cost > what_they_pay)  -> difference negative -> RED
    I can't beat their price; uncompetitive on this lane.
  * my cost is LOW   (my_cost < what_they_pay)  -> difference positive -> BLACK
    There's room to win and still make margin.

Then it suggests a margin to WIN the business: a sell price set below the
competitor's price (so the customer saves) while keeping a healthy margin over
my cost. ``target_customer_savings`` is how much of a discount we offer the
customer off their current price (default 15%); the rest of the spread is margin.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.parsers import ParsedInvoice, ParsedShipment
from app.rating.cards import RateCardData
from app.rating.carriers import quote_best
from app.rating.engine import Quote, ShipmentInput

RED = "\033[31m"
BOLD = "\033[1m"
RESET = "\033[0m"


@dataclass
class ComparisonRow:
    tracking: str
    service: str
    scope: str
    competitor_pays_cents: int
    my_cost_cents: int | None
    my_carrier: str | None
    my_service: str | None
    quote: Quote | None
    carrier_costs: dict[str, int] = None  # carrier -> cost cents (all carriers quoted)

    def __post_init__(self):
        if self.carrier_costs is None:
            self.carrier_costs = {}

    @property
    def serviceable(self) -> bool:
        return self.my_cost_cents is not None

    @property
    def difference_cents(self) -> int | None:
        """what_they_pay - my_cost. Negative => my cost is HIGH (red)."""
        if self.my_cost_cents is None:
            return None
        return self.competitor_pays_cents - self.my_cost_cents

    @property
    def is_high(self) -> bool:
        """My cost is higher than what they pay (uncompetitive)."""
        d = self.difference_cents
        return d is not None and d < 0

    def suggested(self, target_customer_savings: float, min_margin_pct: float):
        """Return (sell_cents, margin_cents, margin_pct, customer_savings_cents) or None.

        Sell below the competitor's price by ``target_customer_savings`` so the
        customer saves, but never below ``min_margin_pct`` over my cost.
        """
        if self.my_cost_cents is None or self.is_high:
            return None
        floor = round(self.my_cost_cents * (1 + min_margin_pct))
        target_sell = round(self.competitor_pays_cents * (1 - target_customer_savings))
        sell = max(target_sell, floor)
        sell = min(sell, self.competitor_pays_cents)  # never price above competitor
        margin = sell - self.my_cost_cents
        margin_pct = margin / sell if sell else 0.0
        savings = self.competitor_pays_cents - sell
        return sell, margin, margin_pct, savings

    def cost_plus(self, markup_pct: float):
        """Cost-plus pricing: sell = cost x (1 + markup).

        Returns (sell, margin, margin_pct, customer_savings) in cents, or None if
        unserviceable. customer_savings = competitor price - sell; it can be
        negative (the cost-plus price would land ABOVE the customer's UPS price).
        """
        if self.my_cost_cents is None:
            return None
        sell = round(self.my_cost_cents * (1 + markup_pct))
        margin = sell - self.my_cost_cents
        margin_pct = margin / sell if sell else 0.0
        savings = self.competitor_pays_cents - sell
        return sell, margin, margin_pct, savings


def _to_input(s: ParsedShipment, scope: str) -> ShipmentInput:
    return ShipmentInput(
        scope=scope,
        service=s.service,
        origin_postal=None,
        dest_postal=s.dest_postal,
        dest_country=s.dest_country,
        actual_weight=s.actual_weight,
        billed_weight=s.billed_weight,
        length=s.length,
        width=s.width,
        height=s.height,
        package_count=s.package_count,
    )


def build_rows(invoices: list[ParsedInvoice], cards: dict[str, RateCardData]) -> list[ComparisonRow]:
    """Build comparison rows, quoting the cheapest carrier for each shipment.

    ``cards`` maps carrier name -> loaded rate card, e.g.
    {"DHL": ..., "Canpar": ..., "Purolator": ...}.
    """
    from app.parsers.ups import _classify_scope
    from app.rating.carriers import quote_all

    rows: list[ComparisonRow] = []
    for inv in invoices:
        for s in inv.shipments:
            scope = _classify_scope(s.dest_postal or "", s.dest_country, s.service or "")
            quotes = quote_all(_to_input(s, scope), cards)
            best = min(quotes, key=lambda q: q.cost_cents) if quotes else None
            rows.append(
                ComparisonRow(
                    tracking=s.tracking_number or "-",
                    service=s.service or "-",
                    scope=scope,
                    competitor_pays_cents=s.total_charge_cents,
                    my_cost_cents=best.cost_cents if best else None,
                    my_carrier=best.our_carrier if best else None,
                    my_service=best.our_service if best else None,
                    quote=best,
                    carrier_costs={q.our_carrier: q.cost_cents for q in quotes},
                )
            )
    return rows


ALL_CARRIERS = ["Purolator", "Canpar", "DHL"]


def rows_to_records(
    rows: list[ComparisonRow],
    target_customer_savings: float = 0.15,
    min_margin_pct: float = 0.10,
    markup_pct: float = 0.25,
) -> list[dict]:
    """Flatten comparison rows into dicts for tables / DataFrames / Excel.

    Money fields are floats in dollars. ``status`` is 'HIGH' (red) or 'LOW'.
    Includes BOTH pricing models per row:
      * beat_*  — beat the competitor by ``target_customer_savings`` (floored at margin)
      * cp_*    — cost-plus: cost x (1 + ``markup_pct``)
    and each carrier's cost side by side (``Purolator_cost`` etc.).
    ``suggested_*`` mirror the beat model for backward compatibility.
    """
    recs: list[dict] = []
    for r in rows:
        rec = {
            "tracking": r.tracking,
            "competitor_service": r.service,
            "scope": r.scope,
            "competitor_pays": r.competitor_pays_cents / 100,
            "my_carrier": r.my_carrier or "",
            "my_service": r.my_service or "",
            "my_cost": (r.my_cost_cents / 100) if r.serviceable else None,
            "difference": (r.difference_cents / 100) if r.serviceable else None,
            "status": "NO RATE" if not r.serviceable else ("HIGH" if r.is_high else "LOW"),
        }
        # per-carrier costs side by side
        for carrier in ALL_CARRIERS:
            c = r.carrier_costs.get(carrier)
            rec[f"{carrier}_cost"] = (c / 100) if c is not None else None

        # beat-competitor model
        beat = r.suggested(target_customer_savings, min_margin_pct) if r.serviceable else None
        rec["beat_sell"] = rec["beat_margin"] = rec["beat_margin_pct"] = rec["beat_savings"] = None
        if beat:
            sell, margin, mpct, savings = beat
            rec.update(beat_sell=sell / 100, beat_margin=margin / 100,
                       beat_margin_pct=round(mpct, 4), beat_savings=savings / 100)

        # cost-plus model
        cp = r.cost_plus(markup_pct) if r.serviceable else None
        rec["cp_sell"] = rec["cp_margin"] = rec["cp_margin_pct"] = rec["cp_savings"] = None
        if cp:
            sell, margin, mpct, savings = cp
            rec.update(cp_sell=sell / 100, cp_margin=margin / 100,
                       cp_margin_pct=round(mpct, 4), cp_savings=savings / 100)

        # backward-compat aliases (beat model)
        rec.update(suggested_sell=rec["beat_sell"], margin=rec["beat_margin"],
                   margin_pct=rec["beat_margin_pct"], customer_savings=rec["beat_savings"])
        recs.append(rec)
    return recs


def summarize(records: list[dict]) -> dict:
    """Portfolio totals from rows_to_records output."""
    serv = [r for r in records if r["status"] != "NO RATE"]
    win = [r for r in serv if r["status"] == "LOW"]
    from collections import defaultdict

    by_carrier_lanes: dict[str, int] = defaultdict(int)
    by_carrier_margin: dict[str, float] = defaultdict(float)
    for r in win:
        if r["margin"]:
            by_carrier_lanes[r["my_carrier"]] += 1
            by_carrier_margin[r["my_carrier"]] += r["margin"]
    # Cost-plus is "competitive" on a lane when its price lands at/below UPS.
    cp_win = [r for r in serv if (r.get("cp_savings") or 0) >= 0]
    return {
        "shipments": len(records),
        "serviceable": len(serv),
        "no_rate": len(records) - len(serv),
        "winnable": len(win),
        "competitor_total": sum(r["competitor_pays"] for r in serv),
        "my_cost_total": sum(r["my_cost"] for r in serv if r["my_cost"] is not None),
        # beat-competitor model
        "total_margin": sum(r["beat_margin"] for r in win if r["beat_margin"]),
        "total_customer_savings": sum(r["beat_savings"] for r in win if r["beat_savings"]),
        "by_carrier_lanes": dict(by_carrier_lanes),
        "by_carrier_margin": dict(by_carrier_margin),
        # cost-plus model
        "costplus_winnable": len(cp_win),
        "costplus_total_margin": sum(r["cp_margin"] for r in cp_win if r["cp_margin"]),
        "costplus_total_customer_savings": sum(
            r["cp_savings"] for r in cp_win if r["cp_savings"]),
    }


def format_table(
    rows: list[ComparisonRow],
    target_customer_savings: float = 0.15,
    min_margin_pct: float = 0.10,
    limit: int = 25,
    color: bool = True,
) -> str:
    def m(c):
        return f"{c/100:,.2f}"

    def paint(text, red):
        if not color:
            return text
        return f"{RED}{text}{RESET}" if red else text

    serviceable = [r for r in rows if r.serviceable]
    out = [
        "=" * 104,
        "RATE COMPARISON & SUGGESTED MARGIN   (competitor = UPS; my carriers = Canpar/Purolator/DHL)",
        f"Offer customer {target_customer_savings:.0%} savings vs their current price; "
        f"floor {min_margin_pct:.0%} margin over my cost.  Best (cheapest) carrier shown per lane.",
        "RED = my cost is HIGH (can't beat their price).  BLACK = competitive.",
        "=" * 104,
        f"{'Tracking':19}{'Scope':14}{'BestCarr':10}{'UPSpays':>9}{'Mycost':>9}{'Diff':>9}"
        f"{'Sell':>9}{'Margin':>9}{'Mgn%':>6}",
        "-" * 104,
    ]
    for r in sorted(serviceable, key=lambda x: -(x.difference_cents or 0))[:limit]:
        diff = r.difference_cents
        sug = r.suggested(target_customer_savings, min_margin_pct)
        if sug:
            sell, margin, mpct, _ = sug
            sell_s, margin_s, mpct_s = m(sell), m(margin), f"{mpct:.0%}"
        else:
            sell_s = margin_s = mpct_s = "-"
        line = (
            f"{r.tracking:19}{r.scope:14}{(r.my_carrier or '')[:9]:10}"
            f"{m(r.competitor_pays_cents):>9}{m(r.my_cost_cents):>9}{m(diff):>9}"
            f"{sell_s:>9}{margin_s:>9}{mpct_s:>6}"
        )
        out.append(paint(line, r.is_high))

    # Portfolio summary
    from collections import defaultdict

    comp = sum(r.competitor_pays_cents for r in serviceable)
    cost = sum(r.my_cost_cents for r in serviceable)
    winnable = [r for r in serviceable if not r.is_high]
    won_margin = won_savings = 0
    won_by_carrier: dict[str, int] = defaultdict(int)
    margin_by_carrier: dict[str, int] = defaultdict(int)
    for r in winnable:
        sug = r.suggested(target_customer_savings, min_margin_pct)
        if sug:
            won_margin += sug[1]
            won_savings += sug[3]
            won_by_carrier[r.my_carrier or "?"] += 1
            margin_by_carrier[r.my_carrier or "?"] += sug[1]
    n_unserviceable = sum(1 for r in rows if not r.serviceable)
    out += [
        "-" * 104,
        f"Serviceable (a carrier rate exists): {len(serviceable)} of {len(rows)} "
        f"shipments  (no rate: {n_unserviceable})",
        f"Customer pays UPS:   ${comp/100:,.2f}",
        f"My best cost:        ${cost/100:,.2f}   "
        + paint(f"(I'm {'HIGH' if cost > comp else 'LOW'} overall by ${abs(comp - cost)/100:,.2f})",
                cost > comp),
        f"Winnable lanes:      {len(winnable)} of {len(serviceable)}",
    ]
    for carr in sorted(won_by_carrier, key=lambda c: -margin_by_carrier[c]):
        out.append(
            f"   via {carr:10} {won_by_carrier[carr]:4} lanes   "
            f"margin ${margin_by_carrier[carr]/100:,.2f}"
        )
    out += [
        f"If won @ {target_customer_savings:.0%} customer savings:  "
        f"customer saves ${won_savings/100:,.2f}, my total margin ${won_margin/100:,.2f}",
        "=" * 104,
        f"{BOLD}NOTE:{RESET} Fuel = current published rates (Purolator Express 38%/Ground 27.25% "
        "verified Jun-2026; DHL 18.75% & Canpar 28% estimated). Carrier-specific DIM weight IS "
        "applied. Remaining estimate: DOMESTIC zones (from province) pending carrier FSA->zone "
        "charts. US-bound DHL anchored (Economy Select N1 = US).",
    ]
    return "\n".join(out)
