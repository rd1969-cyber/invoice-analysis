"""DHL rate-card loading (thin wrapper over the generic ``cards`` loader).

Kept for backwards compatibility; the parsing logic now lives in ``cards.py``.
"""
from __future__ import annotations

from app.rating.cards import Product, RateCardData, load_card

__all__ = ["Product", "RateCardData", "load_card", "load_outbound"]


def load_outbound(path: str) -> RateCardData:
    return load_card(path, "OUTBOUND", "DHL")
