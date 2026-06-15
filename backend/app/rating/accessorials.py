"""Carrier accessorial fees + per-component pricing components.

The rate cards hold base rates only. When you ship a parcel that needs
residential delivery, an adult signature, special handling, or goes to a remote
area, your carrier charges an accessorial fee on top. Those fees are published
(and editable here, since your reseller fees may differ).

Which accessorials apply to a shipment is read from the competitor invoice we
parsed (a residential delivery is residential on any carrier; a signature
requirement carries over; etc.). We then add YOUR carrier's fee for each, so
your cost is complete — and each becomes its own margin component.

Defaults below are current published Canadian fees (2025-2026) where found, else
reasonable estimates. All are editable in the app and flagged accordingly.
"""
from __future__ import annotations

# Pricing components (each can carry its own margin). 'base' and 'fuel' come from
# the raters; the rest are accessorials priced from the fee table below.
COMPONENTS = ["base", "fuel", "residential", "adult_signature", "special_handling", "remote_area"]
COMPONENT_LABELS = {
    "base": "Base rate",
    "fuel": "Fuel surcharge",
    "residential": "Residential delivery",
    "adult_signature": "Adult signature",
    "special_handling": "Special handling",
    "remote_area": "Remote area",
}
ACCESSORIAL_COMPONENTS = ["residential", "adult_signature", "special_handling", "remote_area"]

# Line-item code <-> component (codes used by the raters' Quote line items).
COMPONENT_CODE = {"residential": "RESI", "adult_signature": "SIG",
                  "special_handling": "SPECIAL", "remote_area": "REMOTE"}
CODE_COMPONENT = {"BASE": "base", "FUEL": "fuel", **{v: k for k, v in COMPONENT_CODE.items()}}

# Map the parsed competitor accessorial types -> our components.
COMPETITOR_TO_COMPONENT = {
    "residential": "residential",
    "signature": "adult_signature",
    "additional_handling": "special_handling",
    "oversize": "special_handling",
    "das": "remote_area",
}

# carrier -> component -> fee in cents (editable). Sources noted in chat.
FEES: dict[str, dict[str, int]] = {
    "Canpar": {"residential": 1000, "adult_signature": 700, "special_handling": 2100,
               "remote_area": 1200},
    "Purolator": {"residential": 465, "adult_signature": 600, "special_handling": 2760,
                  "remote_area": 550},
    "DHL": {"residential": 600, "adult_signature": 865, "special_handling": 0,
            "remote_area": 4750},
}


def fee(carrier: str, component: str) -> int:
    """Accessorial fee (cents) for a carrier/component, 0 if not configured."""
    return FEES.get(carrier, {}).get(component, 0)


def applicable_components(parsed_shipment) -> list[str]:
    """Which accessorial components apply, from the parsed competitor shipment."""
    found: list[str] = []
    for a in getattr(parsed_shipment, "accessorials", []):
        comp = COMPETITOR_TO_COMPONENT.get(a.get("type"))
        if comp and comp not in found:
            found.append(comp)
    return found


def normalize_margins(margins) -> dict[str, float]:
    """Accept a single % (float) or a per-component dict; return a component dict."""
    if isinstance(margins, (int, float)):
        return {"default": float(margins)}
    return dict(margins or {})
