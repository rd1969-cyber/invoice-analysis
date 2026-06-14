# Freight IQ — Freight Invoice Intelligence Platform

Upload competitor carrier invoices → parse & normalize them → re-rate against your
own carrier rates → surface savings, margin, and leakage → produce internal and
customer-facing reports.

## Status

**Phase 1 (in progress): MVP vertical slice.**
One carrier, structured input (CSV/EDI before PDF), domestic parcel rating only,
a single savings comparison table — proving the rate math is correct on real data.

## Architecture

```
Upload → Document Processing → Normalized Data Model → Rate Engine → Analysis → UI → Reporting
```

| Layer | Tech | Notes |
|-------|------|-------|
| Backend / API | Python + FastAPI | Best ecosystem for parsing, OCR, AI fallback |
| Database | SQLAlchemy → SQLite (dev) / Postgres (prod) | Swappable via `DATABASE_URL` |
| Parsing | carrier-specific parsers + Claude API fallback | confidence-scored, low-confidence → manual review |
| Rate engine | pure Python, deterministic, unit-tested | **no AI in the math** — every dollar reproducible |
| Frontend | Next.js + React + TS + Tailwind | drill-down everywhere |
| Reporting | openpyxl (Excel), report templates | internal vs customer mode |

### Two non-negotiable principles

1. **Multi-tenancy-ready from day one.** Every table carries `tenant_id`. Phase 1 is
   internal-only, but going customer-facing is a config flip, not a rewrite.
2. **Full provenance.** Every normalized field traces back to its raw source line +
   a confidence score. Any number on any report must drill down to the shipment,
   the invoice line, and the original document behind it.

## Repo layout

```
backend/
  app/
    models.py        # the normalized data model (schema)
    db.py            # SQLAlchemy engine/session
    parsers/         # carrier-specific parsers + AI fallback
    rating/          # the rate engine (the moat)
    analysis/        # metrics / KPIs
    api/             # FastAPI routes
  tests/             # rate engine + parser tests
frontend/            # Next.js app (added once schema is stable)
samples/
  invoices/          # << drop real carrier invoices here
  rate_cards/        # << drop your rate card(s) here
```

## Build order

1. **MVP slice** — one carrier (structured export) → normalize → domestic parcel rating → savings table. *(now)*
2. AI fallback parser + PDF/OCR for messier carriers.
3. Accessorial / DIM weight / fuel sophistication in the rate engine.
4. Analysis dashboards + reporting (internal vs customer mode).
5. Breadth — more carriers, US-bound, international, returns, collect/3rd-party, LTL.

## Local dev

```bash
cd backend
python -m venv .venv && .venv\Scripts\activate   # Windows
pip install -r requirements.txt
uvicorn app.main:app --reload
```
Defaults to a local `freightiq.db` SQLite file. Set `DATABASE_URL` for Postgres.
