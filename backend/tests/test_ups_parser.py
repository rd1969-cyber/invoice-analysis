"""Tests for the UPS parser against the real sample invoices.

These run only if the sample PDFs are present (they're git-ignored), so the suite
stays green on a clean checkout while still giving strong coverage locally.
"""
import glob
import os

import pytest

from app.parsers.ups import UPSParser

SAMPLES = sorted(glob.glob(os.path.join("samples", "invoices", "*.PDF")))
pytestmark = pytest.mark.skipif(not SAMPLES, reason="no sample invoices present")


def test_detects_ups_invoice():
    with open(SAMPLES[0], "rb"):
        pass
    p = UPSParser()
    assert p.can_parse(SAMPLES[0], "Delivery Service Invoice ups.com tracking number incentive") > 0.7


def test_parses_invoice_header():
    [inv] = UPSParser().parse(SAMPLES[0])
    assert inv.carrier == "UPS"
    assert inv.currency == "CAD"
    assert inv.invoice_number
    assert inv.total_spend_cents > 0
    assert inv.shipments


def test_reconciliation_rate_is_high():
    """At least 90% of shipments must reconcile (base + accessorials == Total)."""
    total = recon = 0
    for f in SAMPLES:
        [inv] = UPSParser().parse(f)
        for s in inv.shipments:
            total += 1
            recon += s.field_confidence.get("total_reconciled", 0)
    assert total > 0
    assert recon / total >= 0.90, f"only {recon}/{total} reconciled"


def test_extracts_known_shipment_fields():
    """First shipment of invoice 61146 page 4 has known golden values."""
    target = next(f for f in SAMPLES if "61146" in f)
    [inv] = UPSParser().parse(target)
    s = next(s for s in inv.shipments if s.tracking_number == "1ZE88F612057631095")
    assert s.service == "Standard"
    assert s.dest_postal == "T4C1M1"
    assert s.billed_weight == 2.0
    assert s.actual_weight == 0.5
    assert s.base_charge_cents == 1988  # $19.88 net billed
    assert s.fuel_cents == 755  # $7.55
    assert s.total_charge_cents == 2743  # $27.43
    assert (s.length, s.width, s.height) == (11.0, 6.0, 4.0)
