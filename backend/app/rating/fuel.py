"""Carrier fuel surcharges.

Fuel surcharges are NOT in the rate cards (cards are "exclusive of all
surcharges"). Carriers set them separately for AIR (express) vs GROUND service,
and reset them weekly/monthly off fuel-price indices — so these values are
point-in-time and must be refreshed. Each entry records its effective period,
source, and whether it was verified from the carrier vs estimated.

Update cadence:
  * Purolator Express (courier/air): monthly, first Monday (Natural Resources Canada gasoline index)
  * Purolator Ground:                monthly (Kalibrate diesel index)
  * DHL Express:                     weekly since 2026-04-13
  * Canpar (single, diesel-based):   weekly

Last refreshed: 2026-06-14.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FuelRate:
    pct: float
    effective: str
    source: str
    verified: bool  # True = pulled from carrier/aggregator; False = estimated


# Keyed by (carrier, service_class) where service_class is "air" or "ground".
FUEL: dict[tuple[str, str], FuelRate] = {
    ("Purolator", "air"): FuelRate(
        0.380, "2026-06-01..2026-07-05",
        "purolatorinternational.com/resources/latest-fuel-surcharges", True),
    ("Purolator", "ground"): FuelRate(
        0.2725, "2026-06-01",
        "purolatorinternational.com/resources/latest-fuel-surcharges", True),
    ("DHL", "air"): FuelRate(
        0.1875, "2026-01 (weekly since 2026-04-13)",
        "parcelpath.com/dhl-shipping-rates-canada", False),
    ("Canpar", "ground"): FuelRate(
        0.28, "estimate (weekly diesel index; exact weekly rate not published online)",
        "estimated from peer ground carriers", False),
}

_FALLBACK = FuelRate(0.0, "none", "no rate configured", False)


def service_class(carrier: str, service: str | None) -> str:
    """Classify a service as 'air' or 'ground' for fuel purposes."""
    s = (service or "").lower()
    if carrier == "DHL":
        return "air"
    if carrier == "Canpar":
        return "ground"  # Canpar publishes a single diesel-based surcharge
    if carrier == "Purolator":
        return "ground" if "ground" in s else "air"
    return "ground"


def fuel_rate(carrier: str, service: str | None) -> FuelRate:
    return FUEL.get((carrier, service_class(carrier, service)), _FALLBACK)


def fuel_pct(carrier: str, service: str | None) -> float:
    return fuel_rate(carrier, service).pct
