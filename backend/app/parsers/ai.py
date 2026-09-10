"""Carrier-agnostic AI invoice parser.

Fallback parser for any carrier without a hand-written line-grammar parser
(Day & Ross, netParcel, Manitoulin, Purolator, Canpar, Midland, Armour, ...).

Design notes
------------
* Emits the SAME ``ParsedInvoice`` / ``ParsedShipment`` objects as ``UPSParser``,
  so everything downstream (rating, zones, margins, Excel export) is untouched.
* Money is integer CENTS everywhere, matching the rest of the pipeline.
* Accessorial ``type`` values are restricted to the same vocabulary ``ups.py``
  emits, so ``app.rating.accessorials`` fee lookups keep matching. The carrier's
  own wording is preserved in ``desc``.
* Text is extracted with pdfplumber and sent to Claude in PAGE BATCHES. Large
  invoices (60+ shipments) blow the model's output token budget in one call;
  batching by page keeps every response small and complete.
* Every shipment is reconciled (base + accessorials + tax vs stated total) and
  flagged via ``field_confidence["total_reconciled"]``, same contract as UPS.

Requires: ANTHROPIC_API_KEY in the environment (Streamlit secrets).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

import pdfplumber

from app.parsers import ParsedInvoice, ParsedShipment

MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")
PAGES_PER_BATCH = 4
MAX_TOKENS = 8000

CUSTOM_CARRIERS_PATH = os.path.join(os.path.dirname(__file__), "custom_carriers.json")


# --------------------------------------------------------------------------- #
# Carrier registry
# --------------------------------------------------------------------------- #
@dataclass
class CarrierProfile:
    """What the extractor needs to know to read one carrier's invoices."""

    name: str
    mode: str = "parcel"            # "parcel" | "freight"
    id_type: str = "tracking number"
    dim_factor: str = ""
    codes: str = ""
    weight_unit: str = "LB"
    notes: str = ""
    reseller: bool = False          # invoice layout is theirs, freight is someone else's
    underlying: str = ""            # e.g. "UPS, Purolator" — carriers they resell
    custom: bool = False


PARCEL_CARRIERS = [
    CarrierProfile("UPS", "parcel", "1Z tracking number", "139 in3/lb",
                   "RES,DAS,ESAS,EAS,SAT,ADCR,AHS,LIB,DDU"),
    CarrierProfile("FedEx", "parcel", "12-digit tracking number", "139 in3/lb",
                   "RES,DAS,ESAS,AHS,SAT,ODA,USAB"),
    CarrierProfile("Purolator", "parcel", "PIN number", "5000 cm3/kg",
                   "RES,DAS,FS,SIG,PPKUP,HNDLG", weight_unit="LB"),
    CarrierProfile("Canpar", "parcel", "tracking/PIN number", "5000 cm3/kg",
                   "RES,DAS,FS,SIG,RURAL"),
    CarrierProfile("DHL Express", "parcel", "10-digit waybill number", "5000 cm3/kg",
                   "RES,RF,ODA,SPH,PVT", weight_unit="KG"),
    CarrierProfile("Midland Courier", "parcel", "waybill/tracking number", "5000 cm3/kg",
                   "FS,RES,SIG"),
    CarrierProfile("Day & Ross Courier", "parcel",
                   "Shipment/Expedition ID (format A########)", "5000 cm3/kg",
                   "FUE,APPTDL,DETPDL,RES,HST,GST"),
    CarrierProfile("MBW", "parcel", "tracking/waybill number", "5000 cm3/kg", "FS,RES,SIG"),
    CarrierProfile("netParcel", "parcel", "carrier tracking number (often UPS 1Z format)",
                   "139 in3/lb", "Residential Surcharge,Peak Season Surcharge,"
                   "Delivery Area Surcharge,Fuel,Address Correction,Declared Value,HST,GST",
                   reseller=True, underlying="UPS",
                   notes="Each shipment block shows its own 'Carrier:' line and that "
                         "carrier's tracking number. Sender/Consignee appear as single "
                         "comma-separated address strings."),
    CarrierProfile("Sameday Worldwide", "parcel",
                   "underlying carrier's tracking number (usually UPS 1Z format)",
                   "139 in3/lb", "FS,RES,DAS,SIG,APPT,Fuel,HST,GST",
                   reseller=True, underlying="UPS",
                   notes="Sameday's own invoice layout — it does NOT look like a UPS "
                         "invoice. Charge descriptions are Sameday's wording, but the "
                         "tracking numbers are the underlying carrier's."),
]

FREIGHT_CARRIERS = [
    CarrierProfile("Day & Ross Freight", "freight",
                   "Shipment/Expedition ID (format A########), also the BOL number",
                   "N/A - weight-based LTL",
                   "FUE,APPTDL,DETPDL,DRREWEIGH,PURTPT,CHAS,HST,GST",
                   notes="The Shipment/Expedition number IS the BOL and the tracking number. "
                         "Weights shown as 'Actual Weight' and 'Billed Weight' in LB."),
    CarrierProfile("Midland Transport", "freight", "PRO/waybill number",
                   "N/A - LTL class-based", "FS,APPT,DET,RES,HST"),
    CarrierProfile("Armour Transport", "freight", "PRO number",
                   "N/A - LTL class-based", "FS,APPT,DET,HST"),
    CarrierProfile("Apex", "freight", "PRO number", "N/A - LTL class-based",
                   "FS,APPT,RES,HST"),
    CarrierProfile("ABF Freight", "freight", "PRO number", "N/A - LTL class-based",
                   "FS,APPT,DET,RES,HST"),
    CarrierProfile("WCE", "freight", "PRO/waybill number", "N/A - LTL class-based",
                   "FS,APPT,RES,HST"),
    CarrierProfile("Manitoulin", "freight", "PRO number", "N/A - LTL class-based",
                   "FS,APPT,DET,RES,HST,GST"),
    CarrierProfile("Transforce/TFI", "freight", "PRO/waybill number",
                   "N/A - LTL class-based", "FS,APPT,DET,RES,HST"),
]


def load_custom_carriers() -> list[CarrierProfile]:
    """Carriers added through the UI. Empty list if none saved yet."""
    if not os.path.exists(CUSTOM_CARRIERS_PATH):
        return []
    try:
        with open(CUSTOM_CARRIERS_PATH, encoding="utf-8") as f:
            return [CarrierProfile(**{**c, "custom": True}) for c in json.load(f)]
    except (OSError, ValueError, TypeError):
        return []


def save_custom_carrier(profile: CarrierProfile) -> None:
    """Append a carrier to the JSON store.

    NOTE: on Streamlit Cloud the filesystem is ephemeral — additions survive
    until the app restarts. Commit custom_carriers.json to the repo to make a
    carrier permanent.
    """
    existing = load_custom_carriers()
    existing = [c for c in existing if c.name.lower() != profile.name.lower()]
    existing.append(profile)
    payload = [{k: v for k, v in c.__dict__.items() if k != "custom"} for c in existing]
    with open(CUSTOM_CARRIERS_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def delete_custom_carrier(name: str) -> None:
    remaining = [c for c in load_custom_carriers() if c.name.lower() != name.lower()]
    payload = [{k: v for k, v in c.__dict__.items() if k != "custom"} for c in remaining]
    with open(CUSTOM_CARRIERS_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def all_carriers(mode: str | None = None) -> list[CarrierProfile]:
    """Built-ins plus saved custom carriers, optionally filtered by mode."""
    carriers = PARCEL_CARRIERS + FREIGHT_CARRIERS + load_custom_carriers()
    if mode:
        carriers = [c for c in carriers if c.mode == mode]
    return carriers


def get_carrier(name: str) -> CarrierProfile | None:
    for c in all_carriers():
        if c.name.lower() == (name or "").lower():
            return c
    return None


# --------------------------------------------------------------------------- #
# Normalization helpers
# --------------------------------------------------------------------------- #
# Same output vocabulary ups.py emits — keep in sync or accessorial fee lookups
# in app.rating.accessorials will silently miss.
ACC_MAP = [
    ("fuel", "fuel"), ("fue", "fuel"), ("carburant", "fuel"),
    ("residential", "residential"), ("res ", "residential"),
    ("delivery area", "das"), ("das", "das"), ("rural", "das"), ("extended area", "das"),
    ("adult signature", "signature"), ("signature", "signature"), ("sig", "signature"),
    ("brokerage", "brokerage"), ("duty and tax", "brokerage"), ("customs", "customs"),
    ("address correction", "address_correction"), ("adcr", "address_correction"),
    ("additional handling", "additional_handling"), ("ahs", "additional_handling"),
    ("handling", "additional_handling"),
    ("large package", "oversize"), ("over maximum", "oversize"), ("oversize", "oversize"),
]

RE_CA_POSTAL = re.compile(r"^[A-Z]\d[A-Z]\s*\d[A-Z]\d$")
RE_US_ZIP = re.compile(r"^\d{5}(?:-\d{4})?$")
TAX_KINDS = ("HST", "GST", "QST", "PST")


def _cents(value) -> int:
    """Dollars (float/str/Decimal) -> integer cents. Never raises."""
    if value in (None, "", "-"):
        return 0
    try:
        cleaned = str(value).replace("$", "").replace(",", "").strip()
        return int((Decimal(cleaned) * 100).to_integral_value())
    except (InvalidOperation, ValueError, TypeError):
        return 0


def _acc_type(code: str, desc: str) -> str:
    blob = f"{code} {desc}".lower()
    for needle, kind in ACC_MAP:
        if needle in blob:
            return kind
    return "other"


def _norm_postal(raw: str | None) -> str | None:
    """Uppercase; strip spaces from Canadian postals so FSA[:3] lookups work."""
    if not raw:
        return None
    p = str(raw).upper().strip()
    squashed = p.replace(" ", "")
    if RE_CA_POSTAL.match(squashed):
        return squashed
    if RE_US_ZIP.match(p):
        return p
    return squashed or None


def _float(value) -> float | None:
    try:
        f = float(str(value).replace(",", "").strip())
        return f if f > 0 else None
    except (ValueError, TypeError):
        return None


# --------------------------------------------------------------------------- #
# Prompt
# --------------------------------------------------------------------------- #
SCHEMA = """{
  "carrier": "", "invoice_number": "", "invoice_date": "", "due_date": "",
  "account_number": "", "currency": "CAD", "invoice_total": 0,
  "origin_postal": "",
  "shipments": [{
    "tracking_number": "", "reference": "", "ship_date": "",
    "origin_city": "", "origin_state": "", "origin_postal": "", "origin_country": "",
    "dest_city": "", "dest_state": "", "dest_postal": "", "dest_country": "",
    "service": "", "package_count": 1,
    "weight_actual": 0, "weight_billed": 0, "weight_unit": "LB",
    "length": 0, "width": 0, "height": 0,
    "commodity": "",
    "base_charge": 0,
    "accessorials": [{"code": "", "description": "", "amount": 0}],
    "taxes": [{"code": "", "description": "", "amount": 0}],
    "total_charge": 0,
    "pricing_notes": ""
  }]
}"""

ADDRESS_RULES = """ADDRESS PARSING
Sender/Consignee are often ONE comma-separated string. Split them apart.
The postal/ZIP almost always sits IMMEDIATELY BEFORE the state/province code:
  "Name, Street, City, City, T5T7R6, AB, Canada"  -> city Edmonton, postal T5T7R6, state AB
  "Name, Street, City, 45011, OH, United States"  -> city Hamilton, postal 45011, state OH
- Canadian postal: 6 chars, letter-digit alternating (T5T7R6). Return WITHOUT a space.
- US ZIP: 5 digits, KEEP leading zeros (07631 stays 07631, not 7631). ZIP+4 allowed.
- state/province = the 2-letter code (AB, ON, BC, OH, CA, NJ).
- Never invent a postal code. Empty string if genuinely absent."""


def _build_prompt(profile: CarrierProfile) -> str:
    notes = f"\nCARRIER NOTES: {profile.notes}" if profile.notes else ""
    reseller_block = ""
    if profile.reseller:
        under = profile.underlying or "another carrier"
        reseller_block = f"""

RESELLER INVOICE — IMPORTANT
{profile.name} is a RESELLER. The invoice layout is {profile.name}'s own and will NOT
look like a {under} invoice — different columns, different wording, different structure.
Do not expect the underlying carrier's format. Read the layout in front of you.
- The freight is carried by {under}, so tracking numbers are in {under}'s format.
- Each shipment block may name its own carrier on a "Carrier:" line. Use it if present.
- Charge descriptions use {profile.name}'s wording, not {under}'s codes. Map by MEANING:
  anything describing fuel is fuel, anything describing a residential or home delivery
  is residential, anything describing a remote/extended/rural area is a delivery area
  surcharge, and so on. Never skip a charge line just because the wording is unfamiliar.
- The header total belongs to the whole invoice; each shipment has its own total."""

    return f"""You extract shipment data from freight invoices. Return ONLY valid JSON \
starting with {{ and ending with }}. No markdown, no commentary.

CARRIER: {profile.name} ({'small parcel' if profile.mode == 'parcel' else 'LTL freight'})
SHIPMENT IDENTIFIER: {profile.id_type}
DIM FACTOR: {profile.dim_factor or 'unknown'}
KNOWN CHARGE CODES: {profile.codes or 'unknown'}{notes}{reseller_block}

{SCHEMA}

{ADDRESS_RULES}

CHARGE RULES
- tracking_number MUST be populated using the identifier above. Never leave it blank.
- base_charge = the freight/base line only, BEFORE fuel, accessorials and tax.
- Put fuel surcharge in "accessorials" like any other line (code "FUE" or "FUEL").
- Every other surcharge goes in "accessorials" with its code, description and amount.
- Put HST/GST/QST/PST in "taxes", NOT in accessorials.
- total_charge = the invoice total for that shipment, INCLUDING tax.
- All amounts are plain numbers in dollars, no currency symbols (e.g. 128.20).
- If actual and billed weight differ, report both. Otherwise set them equal.
- Extract EVERY shipment in the text. Never summarize or skip one.
- Return only the shipments present in the text you were given."""


# --------------------------------------------------------------------------- #
# API call
# --------------------------------------------------------------------------- #
def _api_key() -> str:
    """Look in the environment first, then Streamlit secrets.

    Streamlit Cloud usually exports secrets as env vars, but not when the key
    sits inside a [section] — reading st.secrets directly covers both cases.
    Whitespace and stray quotes are stripped: pasting a key with a trailing
    newline or wrapping quotes is the most common cause of a 401.
    """
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        try:
            import streamlit as st

            key = st.secrets.get("ANTHROPIC_API_KEY", "")
        except Exception:  # noqa: BLE001 — not running under Streamlit
            key = ""
    return str(key).strip().strip('"').strip("'").strip()


def key_fingerprint() -> str:
    """Masked description of the key in use, safe to show in the UI."""
    key = _api_key()
    if not key:
        return "no key found"
    return f"{key[:14]}…{key[-4:]} ({len(key)} chars)"


def _client():
    from anthropic import Anthropic

    key = _api_key()
    if not key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Add it to Streamlit secrets "
            "(Settings -> Secrets) as a top-level key, not inside a [section]."
        )
    if not key.startswith("sk-ant-"):
        raise RuntimeError(
            f"The key found does not look like an Anthropic API key — it starts "
            f"'{key[:8]}…'. Anthropic keys begin with 'sk-ant-'. Create one at "
            f"console.anthropic.com under API Keys."
        )
    return Anthropic(api_key=key)


def _extract_json(raw: str) -> dict:
    """Tolerate stray prose or code fences around the JSON object."""
    cleaned = raw.replace("```json", "").replace("```", "").strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"No JSON object in model response: {raw[:200]}")
    return json.loads(cleaned[start:end + 1])


def _call(system: str, user_text: str) -> dict:
    resp = _client().messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=system,
        messages=[{"role": "user", "content": user_text}],
    )
    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    return _extract_json(text)


# --------------------------------------------------------------------------- #
# Mapping to the pipeline dataclasses
# --------------------------------------------------------------------------- #
def _to_shipment(raw: dict, profile: CarrierProfile) -> ParsedShipment:
    s = ParsedShipment()
    s.tracking_number = (raw.get("tracking_number") or "").strip() or None
    s.reference = (raw.get("reference") or "").strip() or None
    s.service = (raw.get("service") or "").strip() or None
    s.ship_date = (raw.get("ship_date") or "").strip() or None
    s.origin_postal = _norm_postal(raw.get("origin_postal"))
    s.dest_postal = _norm_postal(raw.get("dest_postal"))
    s.dest_country = (raw.get("dest_country") or "").strip()[:2].upper() or None

    s.actual_weight = _float(raw.get("weight_actual"))
    s.billed_weight = _float(raw.get("weight_billed")) or s.actual_weight
    s.length = _float(raw.get("length"))
    s.width = _float(raw.get("width"))
    s.height = _float(raw.get("height"))
    try:
        s.package_count = max(1, int(raw.get("package_count") or 1))
    except (ValueError, TypeError):
        s.package_count = 1

    s.base_charge_cents = _cents(raw.get("base_charge"))

    acc_total = 0
    for a in raw.get("accessorials") or []:
        amount = _cents(a.get("amount"))
        code = (a.get("code") or "").strip()
        desc = (a.get("description") or "").strip()
        if not amount and not code and not desc:
            continue
        kind = _acc_type(code, desc)
        s.accessorials.append(
            {"type": kind, "amount_cents": amount, "desc": (desc or code).strip()}
        )
        acc_total += amount
        if kind == "fuel":
            s.fuel_cents += amount

    for t in raw.get("taxes") or []:
        s.tax_cents += _cents(t.get("amount"))

    s.total_charge_cents = _cents(raw.get("total_charge"))
    s.total_published_cents = s.total_charge_cents  # no published/list column on these

    # Reconcile: some carriers' totals include tax, some don't. Accept either.
    computed_with_tax = s.base_charge_cents + acc_total + s.tax_cents
    computed_no_tax = s.base_charge_cents + acc_total
    reconciled = min(
        abs(s.total_charge_cents - computed_with_tax),
        abs(s.total_charge_cents - computed_no_tax),
    ) <= 2
    s.field_confidence["total_reconciled"] = 1.0 if reconciled else 0.0
    s.field_confidence["header"] = 1.0 if s.tracking_number else 0.0
    s.field_confidence["postal"] = 1.0 if s.dest_postal else 0.0
    s.field_confidence["parser"] = 0.85  # AI-extracted, not line-grammar

    if s.dest_country is None and s.dest_postal:
        if RE_CA_POSTAL.match(s.dest_postal):
            s.dest_country = "CA"
        elif RE_US_ZIP.match(s.dest_postal):
            s.dest_country = "US"
    return s


# --------------------------------------------------------------------------- #
# Parser
# --------------------------------------------------------------------------- #
class AIParser:
    """Generic parser driven by an LLM, constrained by a CarrierProfile.

    Usage:
        parser = AIParser(carrier="Day & Ross Freight")
        invoices = parser.parse("/tmp/whatever.pdf")
    """

    name = "ai_generic_v1"

    def __init__(self, carrier: str | CarrierProfile | None = None,
                 pages_per_batch: int = PAGES_PER_BATCH):
        if isinstance(carrier, CarrierProfile):
            self.profile = carrier
        else:
            self.profile = get_carrier(carrier or "") or CarrierProfile(
                name=carrier or "Unknown carrier"
            )
        self.pages_per_batch = max(1, pages_per_batch)

    def can_parse(self, filename: str, sample_text: str) -> float:
        """Low score by design: this is the fallback after specific parsers."""
        return 0.2 if sample_text.strip() else 0.0

    # -- text extraction ---------------------------------------------------- #
    def _pages(self, path: str) -> list[str]:
        ext = os.path.splitext(path)[1].lower()
        if ext == ".pdf":
            with pdfplumber.open(path) as pdf:
                return [(p.extract_text() or "") for p in pdf.pages]
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
        # Chunk flat text so batching still applies to large CSV exports.
        lines = text.splitlines()
        size = 200
        return ["\n".join(lines[i:i + size]) for i in range(0, len(lines), size)] or [text]

    # -- main --------------------------------------------------------------- #
    def parse(self, path: str) -> list[ParsedInvoice]:
        pages = [p for p in self._pages(path) if p.strip()]
        if not pages:
            raise ValueError(
                f"No extractable text in {os.path.basename(path)}. "
                "The PDF is likely a scan — it needs OCR before parsing."
            )

        system = _build_prompt(self.profile)
        inv = ParsedInvoice(carrier=self.profile.name, currency="CAD")
        seen: dict[str, ParsedShipment] = {}
        order: list[str] = []
        header_done = False
        errors: list[str] = []

        batches = [
            pages[i:i + self.pages_per_batch]
            for i in range(0, len(pages), self.pages_per_batch)
        ]

        for n, batch in enumerate(batches, start=1):
            body = "\n\n".join(batch)
            instruction = (
                f"Invoice text, part {n} of {len(batches)}. Extract every shipment "
                f"that appears below. Return JSON only.\n\n{body}"
            )
            try:
                data = _call(system, instruction)
            except Exception as exc:  # noqa: BLE001 — one bad batch must not kill the file
                inv.confidence = min(inv.confidence, 0.5)
                errors.append(f"batch {n}/{len(batches)}: {type(exc).__name__}: {exc}")
                print(f"[AIParser] batch {n}/{len(batches)} failed: {exc}")
                continue

            if not header_done:
                inv.invoice_number = (data.get("invoice_number") or "").strip() or None
                inv.invoice_date = (data.get("invoice_date") or "").strip() or None
                inv.due_date = (data.get("due_date") or "").strip() or None
                inv.account_number = (data.get("account_number") or "").strip() or None
                inv.origin_postal = _norm_postal(data.get("origin_postal"))
                inv.currency = (data.get("currency") or "CAD").strip().upper()[:3] or "CAD"
                inv.total_spend_cents = _cents(data.get("invoice_total"))
                header_done = bool(inv.invoice_number)

            for raw in data.get("shipments") or []:
                ship = _to_shipment(raw, self.profile)
                if not ship.tracking_number and not ship.total_charge_cents:
                    continue
                key = ship.tracking_number or f"_{len(order)}"
                prev = seen.get(key)
                if prev is None:
                    seen[key] = ship
                    order.append(key)
                    if inv.origin_postal is None and ship.origin_postal:
                        inv.origin_postal = ship.origin_postal
                    for t in raw.get("taxes") or []:
                        kind = (t.get("code") or t.get("description") or "").upper()
                        match = next((k for k in TAX_KINDS if k in kind), None)
                        if match:
                            inv.taxes[match] = inv.taxes.get(match, 0) + _cents(t.get("amount"))
                elif ship.field_confidence.get("total_reconciled", 0) > prev.field_confidence.get(
                    "total_reconciled", 0
                ):
                    seen[key] = ship  # keep the cleaner duplicate

        # Surface failures instead of silently returning an empty invoice.
        if not seen and errors:
            raise RuntimeError(
                f"Extraction failed for {os.path.basename(path)} using model "
                f"'{MODEL}' — " + " | ".join(errors[:3])
            )
        if not seen:
            chars = sum(len(p) for p in pages)
            raise ValueError(
                f"No shipments found in {os.path.basename(path)}. Read {chars:,} "
                f"characters across {len(pages)} page(s) and called the model "
                f"{len(batches)} time(s) with no errors — the text extracted but "
                f"nothing matched the '{self.profile.name}' profile. Check the "
                f"carrier selection."
            )

        inv.shipments = [seen[k] for k in order]
        inv.tax_cents = sum(inv.taxes.values()) or sum(s.tax_cents for s in inv.shipments)
        if not inv.total_spend_cents:
            inv.total_spend_cents = sum(s.total_charge_cents for s in inv.shipments)

        if inv.shipments:
            ok = sum(
                1 for s in inv.shipments
                if s.field_confidence.get("total_reconciled", 0) >= 1
            )
            inv.confidence = round(min(inv.confidence, ok / len(inv.shipments)), 3)
        else:
            inv.confidence = 0.0

        return [inv]
