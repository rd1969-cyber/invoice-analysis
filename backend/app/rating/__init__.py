"""The rate engine — re-rates shipments against our carrier rate cards.

Deterministic and auditable: no AI, no hidden state. Given the same shipment and
rate card, it always returns the same numbers, and every number comes with a
line-item breakdown explaining how it was derived.
"""
