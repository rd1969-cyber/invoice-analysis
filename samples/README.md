# Drop your real sample data here

The parsers and the rate engine's concrete lookups are built directly against
these files, so the more representative they are, the better.

## What to drop

### `invoices/`
A few **real carrier invoices** — the messier and more varied, the better:

- Ideally start with a **structured export** (CSV / Excel / EDI) from your
  highest-volume carrier (UPS or FedEx). Structured input lets us prove the
  pipeline end-to-end fastest.
- Then add **PDF invoices** (UPS, FedEx, Purolator, DHL, Freightcom) so we can
  build the PDF/OCR + AI fallback path against the real layouts.
- Include at least one invoice with **accessorials** (residential, DAS, fuel,
  signature, brokerage) so we can validate the breakdown logic.

### `rate_cards/`
At least one of **your** rate cards (InXpress / carrier):

- Whatever format you have — Excel zone matrix, PDF, etc.
- We need: services offered, zone x weight base-rate table, the **fuel
  surcharge** basis, **DIM divisor**, and the **accessorial fee schedule**.

## Privacy note

This folder is git-ignored. Files stay local on your machine; nothing is
uploaded anywhere by the scaffold. If any sample contains data you'd rather not
share, redact tracking numbers / account numbers first — the formats and amounts
are what matter for building the parsers.
