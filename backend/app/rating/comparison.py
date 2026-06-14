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
from app.rating.dhl import quote_dhl
from app.rating.dhl_card import RateCardData
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
    dhl_service: str | None
    quote: Quote | None

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


def build_rows(invoices: list[ParsedInvoice], card: RateCardData, **kw) -> list[ComparisonRow]:
    from app.parsers.ups import _classify_scope

    rows: list[ComparisonRow] = []
    for inv in invoices:
        for s in inv.shipments:
            scope = _classify_scope(s.dest_postal or "", s.dest_country, s.service or "")
            q = quote_dhl(_to_input(s, scope), card, **kw)
            rows.append(
                ComparisonRow(
                    tracking=s.tracking_number or "-",
                    service=s.service or "-",
                    scope=scope,
                    competitor_pays_cents=s.total_charge_cents,
                    my_cost_cents=q.cost_cents if q else None,
                    dhl_service=q.our_service if q else None,
                    quote=q,
                )
            )
    return rows


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
        "=" * 100,
        "RATE COMPARISON & SUGGESTED MARGIN   (competitor = UPS, my carrier = DHL)",
        f"Offer customer {target_customer_savings:.0%} savings vs their current price; "
        f"floor {min_margin_pct:.0%} margin over my cost.",
        "RED = my cost is HIGH (can't beat their price).  BLACK = competitive.",
        "=" * 100,
        f"{'Tracking':20}{'Scope':16}{'UPS pays':>10}{'My cost':>10}{'Diff':>10}"
        f"{'Sugg.sell':>10}{'Margin':>10}{'Mgn%':>6}",
        "-" * 100,
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
            f"{r.tracking:20}{r.scope:16}{m(r.competitor_pays_cents):>10}"
            f"{m(r.my_cost_cents):>10}{m(diff):>10}{sell_s:>10}{margin_s:>10}{mpct_s:>6}"
        )
        out.append(paint(line, r.is_high))

    # Portfolio summary
    comp = sum(r.competitor_pays_cents for r in serviceable)
    cost = sum(r.my_cost_cents for r in serviceable)
    winnable = [r for r in serviceable if not r.is_high]
    won_margin = 0
    won_savings = 0
    for r in winnable:
        sug = r.suggested(target_customer_savings, min_margin_pct)
        if sug:
            won_margin += sug[1]
            won_savings += sug[3]
    n_unserviceable = sum(1 for r in rows if not r.serviceable)
    out += [
        "-" * 100,
        f"Serviceable by DHL: {len(serviceable)} shipments "
        f"(domestic/other not covered: {n_unserviceable})",
        f"Customer pays UPS:   ${comp/100:,.2f}",
        f"My DHL cost:         ${cost/100:,.2f}   "
        + paint(f"(I'm {'HIGH' if cost>comp else 'LOW'} overall by ${abs(comp-cost)/100:,.2f})",
                cost > comp),
        f"Winnable lanes:      {len(winnable)} of {len(serviceable)}",
        f"If won @ {target_customer_savings:.0%} customer savings:  "
        f"customer saves ${won_savings/100:,.2f}, my margin ${won_margin/100:,.2f}",
        "=" * 100,
        f"{BOLD}NOTE:{RESET} DHL fuel % and non-US zones are PLACEHOLDERS until you load "
        "your real DHL fuel rate and zone chart. US-bound numbers are anchored "
        "(Economy Select N1 = US).",
    ]
    return "\n".join(out)
