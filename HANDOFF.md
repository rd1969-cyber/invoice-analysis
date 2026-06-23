# Freight IQ — project handoff / current state

Single-file orientation for picking this up in a new conversation. Read this
first, then the code. Everything is committed to git in this repo.

## What it is
Freight invoice intelligence app for **InXpress Edmonton & Atlantic Canada**.
Upload a customer's competitor carrier invoices → parse & normalize → re-rate
against the user's carrier rate cards → show savings, cost vs price, and suggested
margin → export branded internal (with margin) and customer (savings only) reports.

## How to run
```
cd C:\Users\rober\freight-iq
run_app.bat                      # installs deps + launches Streamlit
```
App: http://localhost:8501 . Or `streamlit run ui/streamlit_app.py`.
Tests: `cd <repo>; set PYTHONPATH=backend; python -m pytest -q`  (45 passing).
**Verify UI changes with Streamlit AppTest, not just an HTTP 200 ping** — a 200
only means the server is up; AppTest actually runs the script and catches errors:
```
python -c "from streamlit.testing.v1 import AppTest; at=AppTest.from_file('ui/streamlit_app.py',default_timeout=90).run(); print(at.exception or 'clean')"
```
On Windows, kill/relaunch the app via PowerShell (git-bash `pkill` does NOT kill
the python process — stale processes cause cached-module ImportErrors):
```
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | ? { $_.CommandLine -like '*streamlit*' } | % { Stop-Process -Id $_.ProcessId -Force }
```

## Architecture (backend/app)
- `parsers/ups.py` — UPS Canada invoice PDF parser (pdfplumber). Extracts per
  shipment: tracking, service, dest postal, zone, actual+billed weight, dims,
  base, accessorials, **published + net charge**, refs, country. ~94% Total
  reconciliation; low-confidence flagged. Captures invoice `origin_postal`,
  GST/HST taxes. (UPS is the *competitor* on the invoices.)
- `rating/cards.py` — generic InXpress rate-sheet loader. `load_any(path,carrier,
  sheet)` reads **.xls/.xlsx/.pdf** into a grid; `parse_grid` handles the block
  layout (product / Weight() header / zone cols / weight rows / overage).
  `adjust_card` scales base rates ±%. Trademark-tolerant product lookup.
- `rating/dim.py` — carrier-specific dimensional weight. UPS/DHL/Purolator ÷139,
  Canpar Ground ÷166, Canpar Select ÷137. `billable_weight_lb()`.
- `rating/fuel.py` — fuel surcharges per carrier × air/ground, dated+sourced.
  Purolator Express 38% / Ground 27.25% (verified Jun-2026); DHL 18.75%, Canpar
  28% (estimated).
- `rating/accessorials.py` — carrier accessorial FEES (residential, adult_
  signature, special_handling, remote_area), editable. Maps competitor invoice
  accessorials → components. Pricing components + per-component margin helpers.
- `rating/carriers.py` — `DOMESTIC_CARRIERS` registry (Canpar, Purolator + any
  added at runtime via `register_domestic_carrier`), zone resolver (real FSA→zone
  chart if loaded, else **province estimate** flagged), `quote_domestic`,
  `quote_all` (every applicable carrier), `quote_best`. Adds accessorial line
  items, then **fuel on base+accessorials**.
- `rating/dhl.py` + `dhl_card.py` — DHL rater (US-bound/international). US anchored
  (Economy Select N1=US); non-US country→zone is a PLACEHOLDER.
- `rating/zones.py` — load carrier FSA→zone charts (Excel/PDF); `resolve_zone`.
- `rating/comparison.py` — the core. `build_rows(invoices, cards, manual_costs,
  ups_discount)` → `ComparisonRow`s (carrier costs, zone_basis, pickup/delivery,
  weights, dims, weight_basis). `rows_to_records(rows, savings, min_margin,
  margins)` → flat dicts with BOTH pricing models + per-carrier cost columns.
  `summarize`. Two pricing models:
    - **beat**: sell = competitor × (1 − customer_savings), floored at margin.
    - **margin** (per-component): sell_i = cost_i / (1 − margin_i), summed.
- `reporting/excel.py` — branded internal + customer xlsx (red/black, logo).
- `brand.py` — InXpress palette/fonts/contact/logos.
- `models.py`/`db.py` — SQLAlchemy normalized schema (tenant_id + provenance),
  **not yet wired to the app** (app runs in-memory off parsed objects).

## UI (ui/streamlit_app.py) — 3 tabs
1. Invoices: upload PDFs (or samples) → spend KPIs, accessorial+tax breakout,
   per-shipment detail (CSV export).
2. Rate cards: per-carrier upload (Excel/PDF), "Add another carrier", FSA→zone
   chart upload, manual-cost upload, editable accessorial fee table.
3. Comparison & margin: best-carrier table + all-carriers side-by-side (incl.
   pickup/delivery postal, actual+billable weight, dims, weight basis), KPIs,
   Excel download. Sidebar: per-component margins, beat savings %/floor, UPS DAP
   discount, per-carrier rate ±% (discount), report mode/basis.

## Pricing model recap (per user)
- They price with **margin** (% of sell), per component (base/fuel/residential/
  adult signature/special handling/remote area). Fuel applies to base+accessorials.
- UPS = **DAP** dynamic pricing → modeled as published charge (from invoice) ×
  (1 − DAP discount slider), carrier "UPS(yours)".

## Current data & key findings (sample customer: SureShot/AC Dispensing, acct E88F61)
- 9 UPS invoices: $28,671.88 / 481 shipments / fuel 24.6% of spend. Customer is
  deeply discounted (~40–65% off UPS published).
- **DHL** not competitive (premium air vs discounted ground) — 0 winnable.
- **Domestic via Purolator** is the opportunity (Purolator << Canpar on these).
- **Domestic numbers are ESTIMATED** until FSA→zone charts load — flagged in UI.

## PENDING from user (the blockers)
1. **Canpar + Purolator FSA→zone charts** — #1 blocker for trustworthy domestic $.
2. **Updated base rates** (user gathering) — drop in Tab 2.
3. Tune **accessorial fees** to real reseller rates (Tab 2).
4. Other (non-UPS) invoice examples → need new parsers.
5. Real DHL invoice → confirm if DHL card already includes their discount.

## Deployment (Streamlit Community Cloud — free)
- **LIVE:** https://shipping-invoice-analysis-jorben.streamlit.app/ (set public/allow-list
  via app → Settings → Sharing).
- ⚠️ **Main file path MUST be `ui/streamlit_app.py`.** A deploy that points at
  `backend/app/brand.py` (constants only, no UI) gives a silent BLANK page — this is
  not editable after deploy, so delete + redeploy to fix. This cost a long debug once.
- GitHub remote: **https://github.com/rd1969-cyber/invoice-analysis** (private), branch `main`.
- Root `requirements.txt` holds the lean runtime deps (streamlit/pandas/pdfplumber/
  openpyxl/xlrd) — Streamlit Cloud reads this. `backend/requirements.txt` keeps the
  full FastAPI/DB/test set, NOT needed by the hosted app.
- App self-injects `backend/` onto `sys.path` (streamlit_app.py top), so no PYTHONPATH
  needed on the cloud. Main file path for deploy: `ui/streamlit_app.py`.
- Deploy at https://share.streamlit.io → New app → repo `rd1969-cyber/invoice-analysis`,
  branch `main`, main file `ui/streamlit_app.py`, Advanced → Python 3.12.
- Restrict viewers via app **Settings → Sharing** (email allow-list).
- Real customer data (samples/, reports/, *.db) is gitignored — keep it that way.

## Possible next steps
PDF/PPTX exec report; wire DB persistence; FedEx/Purolator/DHL invoice parsers;
DHL international country→zone map.
