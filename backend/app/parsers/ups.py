"""UPS Delivery Service Invoice parser (Canada).

Built against real SureShot/AC Dispensing invoices. UPS text-based PDFs extract
cleanly with pdfplumber, so this is a deterministic line-grammar parser (no OCR
needed for these). It handles both detail sections:

  * "UPS WorldShip" / domestic   — Canadian postal + numeric zone
  * "Worldwide Service"          — US/international, with country line

Shipment detail grammar (one shipment):

    [MM/DD] [pickup#] [entry]1Z................  Service  POSTAL  ZONE  W lbs  PUB  CREDIT  BILLED
    Customer Weight X lbs
    <Accessorial name>  [PUB]  [CREDIT]  BILLED        (1-3 trailing numbers; BILLED = last)
    Customer Entered Dimensions = L x W x H in
    Total  PUB  CREDIT  BILLED
    1st ref: ...  2nd ref: ...
    Sender :...  Receiver:...
    <address lines, optional 2-letter country>

Every shipment gets per-field and overall confidence; the BILLED total is
cross-checked against base + accessorials and flagged on mismatch.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

import pdfplumber

from app.parsers import ParsedInvoice, ParsedShipment

# --------------------------------------------------------------------------- #
# Regexes
# --------------------------------------------------------------------------- #
_NUM = r"-?[\d,]+\.\d{2}"
_TRACK = r"1Z[A-Z0-9]{16}"

# Shipment header line: locate the 1Z tracking, then service / postal / zone /
# weight / published / credit / billed at the end of the line.
#
# Postal is OPTIONAL (Returns have no postal column). Weight may be a number
# ("2 lbs") or the literal "Letter" (envelope shipments). Postal is matched
# against specific shapes (Canadian A#A #A#, US ZIP, or short alphanumeric) so
# the non-greedy service field doesn't swallow it.
_POSTAL = r"[A-Z]\d[A-Z]\s?\d[A-Z]\d|\d{5}(?:-\d{4})?|[A-Z][A-Z0-9]{1,4}\s?\d[A-Z]{0,2}"
RE_HEADER = re.compile(
    rf"(?P<track>{_TRACK})\s+"
    rf"(?P<service>.+?)"
    rf"(?:\s+(?P<postal>{_POSTAL}))?"
    rf"\s+(?P<zone>\d{{2,4}})\s+"
    rf"(?P<weight>\d+(?:\.\d+)?|Letter)(?:\s+lbs)?\s+"
    rf"(?P<pub>{_NUM})\s+(?P<credit>{_NUM})\s+(?P<billed>{_NUM})\s*$"
)
RE_DATE_PREFIX = re.compile(r"^(?P<date>\d{2}/\d{2})\b")
RE_CUST_WEIGHT = re.compile(r"Customer Weight\s+(?P<w>\d+(?:\.\d+)?)\s*lbs", re.I)
RE_DIMS = re.compile(
    r"Dimensions\s*=\s*(?P<l>[\d.]+)\s*x\s*(?P<w>[\d.]+)\s*x\s*(?P<h>[\d.]+)\s*in", re.I
)
RE_BILLABLE_DIMS = re.compile(
    r"Billable Audited Dimensions\s*=\s*(?P<l>[\d.]+)\s*x\s*(?P<w>[\d.]+)\s*x\s*(?P<h>[\d.]+)",
    re.I,
)
RE_TOTAL = re.compile(rf"^Total\s+(?P<pub>{_NUM})\s+(?P<credit>{_NUM})\s+(?P<billed>{_NUM})\s*$")
RE_REF = re.compile(r"1st ref:\s*(?P<r1>\S+)(?:\s+2nd ref:\s*(?P<r2>.+))?")
RE_ACCESSORIAL = re.compile(rf"^(?P<name>[A-Za-z][A-Za-z &/\-]+?)\s+(?P<nums>(?:{_NUM}\s*){{1,3}})$")
RE_COUNTRY = re.compile(r"^(?P<cc>[A-Z]{2})$")
RE_CA_POSTAL = re.compile(r"^[A-Z]\d[A-Z]\s*\d[A-Z]\d$")
RE_US_ZIP = re.compile(r"^\d{5}(?:-\d{4})?$")

# Invoice header fields
RE_INV_NUMBER = re.compile(r"Invoice Number\s+(?P<v>\S+)")
RE_INV_DATE = re.compile(r"Invoice Date\s+(?P<v>[A-Za-z]+ \d{1,2}, \d{4})")
RE_DUE_DATE = re.compile(r"Invoice Due Date\s+(?P<v>[A-Za-z]+ \d{1,2}, \d{4})")
RE_ACCOUNT = re.compile(r"Account Number\s+(?P<v>\S+)")
RE_AMOUNT_DUE = re.compile(rf"Amount due this period\s+(?:CAD\s+)?(?P<v>{_NUM})")
# Tax detail lines, e.g. "Total Taxes HST R105453328 82.19" / "Total Taxes GST ... 6.91"
RE_TAX_LINE = re.compile(rf"Total Taxes\s+(?P<kind>GST|HST|QST|PST)\b.*?(?P<v>{_NUM})\s*$", re.M)

# Accessorial name -> normalized type
ACC_MAP = [
    ("fuel", "fuel"),
    ("residential", "residential"),
    ("delivery area", "das"),
    ("signature", "signature"),
    ("adult signature", "signature"),
    ("brokerage", "brokerage"),
    ("duty and tax", "brokerage"),
    ("customs", "customs"),
    ("international processing", "other"),
    ("address correction", "address_correction"),
    ("additional handling", "additional_handling"),
    ("large package", "oversize"),
    ("over maximum", "oversize"),
]


def _cents(s: str) -> int:
    try:
        return int((Decimal(s.replace(",", "")) * 100).to_integral_value())
    except (InvalidOperation, AttributeError):
        return 0


def _acc_type(name: str) -> str:
    low = name.lower()
    for needle, t in ACC_MAP:
        if needle in low:
            return t
    return "other"


def _classify_scope(postal: str, country: str | None, service: str) -> str:
    if country and country != "CA":
        return "us_bound_parcel" if country == "US" else "international"
    if RE_US_ZIP.match(postal or ""):
        return "us_bound_parcel"
    if RE_CA_POSTAL.match((postal or "").replace(" ", "")):
        return "domestic_parcel"
    if "worldwide" in service.lower():
        return "international"
    return "domestic_parcel"


# --------------------------------------------------------------------------- #
# Parser
# --------------------------------------------------------------------------- #
class UPSParser:
    name = "ups_canada_v1"

    def can_parse(self, filename: str, sample_text: str) -> float:
        t = sample_text.lower()
        score = 0.0
        if "delivery service invoice" in t:
            score += 0.5
        if "ups.com" in t:
            score += 0.3
        if "incentive" in t and "tracking number" in t:
            score += 0.2
        return min(score, 1.0)

    def parse(self, path: str) -> list[ParsedInvoice]:
        with pdfplumber.open(path) as pdf:
            pages = [(p.extract_text() or "") for p in pdf.pages]
        full = "\n".join(pages)

        inv = ParsedInvoice(carrier="UPS", currency="CAD")
        if m := RE_INV_NUMBER.search(full):
            inv.invoice_number = m.group("v")
        if m := RE_INV_DATE.search(full):
            inv.invoice_date = m.group("v")
        if m := RE_DUE_DATE.search(full):
            inv.due_date = m.group("v")
        if m := RE_ACCOUNT.search(full):
            inv.account_number = m.group("v")
        if m := RE_AMOUNT_DUE.search(full):
            inv.total_spend_cents = _cents(m.group("v"))
        for tm in RE_TAX_LINE.finditer(full):
            inv.taxes[tm.group("kind")] = inv.taxes.get(tm.group("kind"), 0) + _cents(tm.group("v"))
        inv.tax_cents = sum(inv.taxes.values())

        inv.shipments = self._parse_shipments(pages)
        # Confidence: fraction of shipments whose Total reconciled.
        if inv.shipments:
            ok = sum(1 for s in inv.shipments if s.field_confidence.get("total_reconciled", 0) >= 1)
            inv.confidence = round(ok / len(inv.shipments), 3)
        return [inv]

    def _parse_shipments(self, pages: list[str]) -> list[ParsedShipment]:
        shipments: list[ParsedShipment] = []
        current: ParsedShipment | None = None
        last_date: str | None = None
        running_total_cents = 0  # base + accessorials, to reconcile vs Total

        def close(ship: ParsedShipment | None, running: int) -> None:
            if ship is None:
                return
            ship.field_confidence["total_reconciled"] = (
                1 if abs(ship.total_charge_cents - running) <= 1 else 0
            )
            shipments.append(ship)

        for page in pages:
            for raw in page.splitlines():
                line = raw.strip()
                if not line:
                    continue

                if dm := RE_DATE_PREFIX.match(line):
                    last_date = dm.group("date")

                hm = RE_HEADER.search(line)
                if hm:
                    close(current, running_total_cents)
                    current = ParsedShipment()
                    running_total_cents = 0
                    current.tracking_number = hm.group("track")
                    current.service = hm.group("service").strip()
                    current.dest_postal = (hm.group("postal") or "").strip() or None
                    current.field_confidence["zone"] = 1.0
                    wt = hm.group("weight")
                    current.billed_weight = 0.0 if wt == "Letter" else float(wt)
                    current.base_charge_cents = _cents(hm.group("billed"))
                    running_total_cents = current.base_charge_cents
                    current.ship_date = last_date
                    current.field_confidence["header"] = 1.0
                    continue

                if current is None:
                    continue

                if cw := RE_CUST_WEIGHT.search(line):
                    current.actual_weight = float(cw.group("w"))
                    continue

                bdm = RE_BILLABLE_DIMS.search(line)
                dm2 = bdm or RE_DIMS.search(line)
                if dm2:
                    current.length = float(dm2.group("l"))
                    current.width = float(dm2.group("w"))
                    current.height = float(dm2.group("h"))
                    continue

                tm = RE_TOTAL.match(line)
                if tm:
                    current.total_charge_cents = _cents(tm.group("billed"))
                    current.total_published_cents = _cents(tm.group("pub"))
                    continue

                if rm := RE_REF.search(line):
                    current.reference = rm.group("r1")
                    continue

                if cm := RE_COUNTRY.match(line):
                    current.dest_country = cm.group("cc")
                    continue

                am = RE_ACCESSORIAL.match(line)
                if am and not line.startswith("Total"):
                    nums = re.findall(_NUM, am.group("nums"))
                    billed = _cents(nums[-1])
                    t = _acc_type(am.group("name"))
                    current.accessorials.append(
                        {"type": t, "amount_cents": billed, "desc": am.group("name").strip()}
                    )
                    running_total_cents += billed
                    if t == "fuel":
                        current.fuel_cents += billed
                    continue

        close(current, running_total_cents)

        # Dedupe: a tracking number can appear in both the primary detail section
        # and the "Adjustments & Other Charges" section. Keep the instance that
        # reconciled (the authoritative detail line); otherwise the richer one.
        best: dict[str, ParsedShipment] = {}
        order: list[str] = []
        for s in shipments:
            key = s.tracking_number or id(s)
            prev = best.get(key)
            if prev is None:
                best[key] = s
                order.append(key)
                continue
            s_ok = s.field_confidence.get("total_reconciled", 0)
            p_ok = prev.field_confidence.get("total_reconciled", 0)
            if (s_ok, s.total_charge_cents) > (p_ok, prev.total_charge_cents):
                best[key] = s
        shipments = [best[k] for k in order]

        # Post-process: scope + default country
        for s in shipments:
            if s.dest_country is None and RE_CA_POSTAL.match((s.dest_postal or "").replace(" ", "")):
                s.dest_country = "CA"
        return shipments
