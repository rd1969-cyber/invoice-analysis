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
    zone_basis: str = "n/a"  # 'exact' | 'estimated' | 'manual' | 'n/a'

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

    def margin_price(self, target_margin: float):
        """Margin pricing: price so gross margin = target_margin (% of SELL).

        sell = cost / (1 - target_margin)  =>  margin$ = sell * target_margin.
        (This differs from cost-plus markup, which is a % of COST.)
        Returns (sell, margin, margin_pct, customer_savings) in cents, or None.
        customer_savings = competitor price - sell; can be negative if the margin
        price lands ABOVE the customer's current price.
        """
        if self.my_cost_cents is None:
            return None
        tm = min(max(target_margin, 0.0), 0.95)
        sell = round(self.my_cost_cents / (1 - tm))
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


def build_rows(
    invoices: list[ParsedInvoice],
    cards: dict[str, RateCardData],
    manual_costs: dict[str, dict] | None = None,
) -> list[ComparisonRow]:
    """Build comparison rows, quoting the cheapest carrier for each shipment.

    ``cards`` maps carrier name -> loaded rate card, e.g.
    {"DHL": ..., "Canpar": ..., "Purolator": ...}.
    ``manual_costs`` maps tracking number -> {"cost_cents", "carrier", "service"}
    to override the computed cost (manual costing / carriers with no rate card).
    """
    from app.parsers.ups import _classify_scope
    from app.rating.carriers import quote_all

    def _zone_basis(q):
        if q is None:
            return "n/a"
        w = " ".join(q.warnings).upper()
        return "estimated" if ("ESTIMATED" in w or "PLACEHOLDER" in w) else "exact"

    manual_costs = manual_costs or {}
    rows: list[ComparisonRow] = []
    for inv in invoices:
        for s in inv.shipments:
            scope = _classify_scope(s.dest_postal or "", s.dest_country, s.service or "")
            quotes = quote_all(_to_input(s, scope), cards)
            carrier_costs = {q.our_carrier: q.cost_cents for q in quotes}
            best = min(quotes, key=lambda q: q.cost_cents) if quotes else None
            my_cost = best.cost_cents if best else None
            my_carrier = best.our_carrier if best else None
            my_service = best.our_service if best else None
            zone_basis = _zone_basis(best)

            # Manual cost override (e.g. carriers with no rate card / manual costing).
            man = manual_costs.get((s.tracking_number or "").strip())
            if man is not None:
                my_cost = man["cost_cents"]
                my_carrier = man.get("carrier") or "Manual"
                my_service = man.get("service") or "Manual cost"
                carrier_costs = {**carrier_costs, my_carrier: my_cost}
                zone_basis = "manual"

            rows.append(
                ComparisonRow(
                    tracking=s.tracking_number or "-",
                    service=s.service or "-",
                    scope=scope,
                    competitor_pays_cents=s.total_charge_cents,
                    my_cost_cents=my_cost,
                    my_carrier=my_carrier,
                    my_service=my_service,
                    quote=best,
                    carrier_costs=carrier_costs,
                    zone_basis=zone_basis,
                )
            )
    return rows


ALL_CARRIERS = ["Purolator", "Canpar", "DHL"]


def parse_manual_costs(records: list[dict]) -> dict[str, dict]:
    """Build a manual-cost override map from rows of dicts (e.g. a CSV/Excel).

    Looks for a tracking-like key and a cost/price-like key; carrier and service
    are optional. Returns {tracking: {"cost_cents", "carrier", "service"}}.
    """
    out: dict[str, dict] = {}
    if not records:
        return out
    keys = list(records[0].keys())

    def find(*needles):
        for k in keys:
            kl = str(k).lower()
            if any(n in kl for n in needles):
                return k
        return None

    tk = find("track", "tracking", "reference", "shipment")
    ck = find("cost", "price", "my cost")
    carrier_k = find("carrier")
    svc_k = find("service")
    if tk is None or ck is None:
        return out
    for r in records:
        track = str(r.get(tk, "")).strip()
        raw = r.get(ck)
        if not track or raw in (None, ""):
            continue
        try:
            cents = int(round(float(str(raw).replace("$", "").replace(",", "")) * 100))
        except ValueError:
            continue
        out[track] = {
            "cost_cents": cents,
            "carrier": (str(r.get(carrier_k)).strip() if carrier_k and r.get(carrier_k) else None),
            "service": (str(r.get(svc_k)).strip() if svc_k and r.get(svc_k) else None),
        }
    return out


def rows_to_records(
    rows: list[ComparisonRow],
    target_customer_savings: float = 0.15,
    min_margin_pct: float = 0.10,
    target_margin: float = 0.20,
) -> list[dict]:
    """Flatten comparison rows into dicts for tables / DataFrames / Excel.

    Money fields are floats in dollars. ``status`` is 'HIGH' (red) or 'LOW'.
    Includes BOTH pricing models per row:
      * beat_*  — beat the competitor by ``target_customer_savings`` (floored at margin)
      * mgn_*   — margin pricing: price so gross margin = ``target_margin`` (% of sell)
    and each carrier's cost side by side (``Purolator_cost`` etc.).
    ``suggested_*`` mirror the beat model for backward compatibility.
    """
    # Carrier cost columns: standard order first, then any extra carriers seen.
    seen = {c for r in rows for c in r.carrier_costs}
    carriers = [c for c in ALL_CARRIERS if c in seen] + sorted(seen - set(ALL_CARRIERS))

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
            "zone_basis": r.zone_basis,
        }
        # per-carrier costs side by side
        for carrier in carriers:
            c = r.carrier_costs.get(carrier)
            rec[f"{carrier}_cost"] = (c / 100) if c is not None else None

        # beat-competitor model
        beat = r.suggested(target_customer_savings, min_margin_pct) if r.serviceable else None
        rec["beat_sell"] = rec["beat_margin"] = rec["beat_margin_pct"] = rec["beat_savings"] = None
        if beat:
            sell, margin, mpct, savings = beat
            rec.update(beat_sell=sell / 100, beat_margin=margin / 100,
                       beat_margin_pct=round(mpct, 4), beat_savings=savings / 100)

        # margin model
        mg = r.margin_price(target_margin) if r.serviceable else None
        rec["mgn_sell"] = rec["mgn_margin"] = rec["mgn_margin_pct"] = rec["mgn_savings"] = None
        if mg:
            sell, margin, mpct, savings = mg
            rec.update(mgn_sell=sell / 100, mgn_margin=margin / 100,
                       mgn_margin_pct=round(mpct, 4), mgn_savings=savings / 100)

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
    # Margin model is "competitive" on a lane when its price lands at/below UPS.
    mgn_win = [r for r in serv if (r.get("mgn_savings") or 0) >= 0]
    estimated_zone = sum(1 for r in serv if r.get("zone_basis") == "estimated")
    return {
        "shipments": len(records),
        "serviceable": len(serv),
        "no_rate": len(records) - len(serv),
        "winnable": len(win),
        "estimated_zone": estimated_zone,
        "competitor_total": sum(r["competitor_pays"] for r in serv),
        "my_cost_total": sum(r["my_cost"] for r in serv if r["my_cost"] is not None),
        # beat-competitor model
        "total_margin": sum(r["beat_margin"] for r in win if r["beat_margin"]),
        "total_customer_savings": sum(r["beat_savings"] for r in win if r["beat_savings"]),
        "by_carrier_lanes": dict(by_carrier_lanes),
        "by_carrier_margin": dict(by_carrier_margin),
        # margin model
        "margin_winnable": len(mgn_win),
        "margin_total_margin": sum(r["mgn_margin"] for r in mgn_win if r["mgn_margin"]),
        "margin_total_customer_savings": sum(
            r["mgn_savings"] for r in mgn_win if r["mgn_savings"]),
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
