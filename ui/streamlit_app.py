"""Freight IQ — branded Streamlit app (InXpress Edmonton & Atlantic Canada).

Run:  streamlit run ui/streamlit_app.py   (from the project root)

Flow: upload competitor invoices -> parse -> adjust rate cards / fuel / markup ->
see the red/black rate comparison + suggested margin -> export internal / customer
reports. Sits directly on the rating engine in backend/app.
"""
from __future__ import annotations

import os
import sys
import tempfile

# Make the backend package importable when run via `streamlit run`.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "backend"))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from app import brand  # noqa: E402
from app.analysis.spend import (  # noqa: E402
    ACCESSORIAL_COLUMNS, ACCESSORIAL_LABELS, analyze, SpendReport, shipment_breakdown,
)
from app.parsers.ups import UPSParser  # noqa: E402
from app.rating import carriers as carriersmod  # noqa: E402
from app.rating import fuel as fuelmod  # noqa: E402
from app.rating import zones as zonesmod  # noqa: E402
from app.rating.carriers import register_domestic_carrier  # noqa: E402

BUILTIN_DOMESTIC = ("Canpar", "Purolator")
from app.rating.cards import adjust_card, load_any, load_card  # noqa: E402
from app.rating.comparison import (  # noqa: E402
    build_rows, parse_manual_costs, rows_to_records, summarize,
)
from app.reporting.excel import build_workbook  # noqa: E402

SAMPLES_INV = os.path.join(_ROOT, "samples", "invoices")
SAMPLES_CARDS = os.path.join(_ROOT, "samples", "rate_cards")

# Default rate cards: (carrier, filename-glob, sheet)
DEFAULT_CARDS = [
    ("DHL", "12800001_DHL", "OUTBOUND"),
    ("Canpar", "CANPAR_Domestic", "DIFFERENT"),
    ("Purolator", "Purolator_Domestic_1", "DIFFERENT"),
]

st.set_page_config(page_title=f"{brand.APP_NAME} — InXpress", page_icon="📦", layout="wide")


# --------------------------------------------------------------------------- #
# Branding
# --------------------------------------------------------------------------- #
def _logo_data_uri(on_dark: bool = True) -> str | None:
    import base64

    path = brand.logo_path(on_dark=on_dark)
    if not path:
        return None
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    return f"data:image/png;base64,{b64}"


def inject_brand_css() -> None:
    logo = _logo_data_uri(on_dark=True)
    # Official white logo on the dark header; fall back to a wordmark if missing.
    logo_html = (
        f'<img src="{logo}" alt="InXpress" style="height:34px;"/>' if logo
        else '<span class="fiq-logo">In<span class="x">X</span>press</span>'
    )
    st.markdown(
        f"""
        <style>
        @import url('{brand.GOOGLE_FONTS_URL}');
        html, body, [class*="css"] {{ font-family: '{brand.BODY_FONT}', sans-serif; }}
        h1, h2, h3 {{ font-family: '{brand.HEADLINE_FONT}', sans-serif;
                      color: {brand.MIDNIGHT_BLUE}; letter-spacing: -0.01em; }}
        .fiq-header {{ background:{brand.MIDNIGHT_BLUE}; padding:16px 24px; border-radius:10px;
                       display:flex; align-items:center; justify-content:space-between; gap:18px; }}
        .fiq-left {{ display:flex; align-items:center; gap:14px; }}
        .fiq-logo {{ font-family:'{brand.HEADLINE_FONT}',sans-serif; font-weight:700;
                     font-size:26px; color:#fff; }}
        .fiq-logo .x {{ color:{brand.SPRING_GREEN}; }}
        .fiq-sub {{ color:#cdd7e6; font-size:13px; }}
        .stButton>button, .stDownloadButton>button {{
            background:{brand.MIDNIGHT_BLUE}; color:#fff; border:0; border-radius:8px; font-weight:600; }}
        .stButton>button:hover, .stDownloadButton>button:hover {{ background:{brand.VIVID_BLUE}; color:#fff; }}
        [data-testid="stMetricValue"] {{ color:{brand.MIDNIGHT_BLUE}; }}
        </style>
        <div class="fiq-header">
          <div class="fiq-left">{logo_html}
               <span class="fiq-sub">{brand.APP_NAME} — {brand.APP_TAGLINE}</span></div>
          <div class="fiq-sub">{brand.CONTACT['business_name']} · {brand.CONTACT['phone']}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


# --------------------------------------------------------------------------- #
# Cached compute
# --------------------------------------------------------------------------- #
@st.cache_data(show_spinner="Parsing invoices…")
def parse_invoices(files: list[tuple[str, bytes]]) -> list:
    parser = UPSParser()
    invoices = []
    for name, data in files:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tf:
            tf.write(data)
            path = tf.name
        try:
            invoices.extend(parser.parse(path))
        finally:
            os.unlink(path)
    return invoices


@st.cache_data(show_spinner="Loading rate cards…")
def load_card_bytes(carrier: str, sheet: str, filename: str, data: bytes):
    """Load a rate card from uploaded bytes — .xls, .xlsx, or .pdf."""
    suffix = os.path.splitext(filename)[1].lower() or ".xls"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tf:
        tf.write(data)
        path = tf.name
    try:
        return load_any(path, carrier, sheet)
    finally:
        os.unlink(path)


def _sample_file(folder: str, contains: str) -> str | None:
    if not os.path.isdir(folder):
        return None
    for f in sorted(os.listdir(folder)):
        if contains in f:
            return os.path.join(folder, f)
    return None


# --------------------------------------------------------------------------- #
# App
# --------------------------------------------------------------------------- #
inject_brand_css()

# ---- Sidebar: settings ---------------------------------------------------- #
with st.sidebar:
    st.header("Settings")
    st.caption("Pricing — both models run; pick which drives the customer report")
    target_savings = st.slider("Beat model: customer savings vs their price", 0, 40, 15, 1) / 100
    min_margin = st.slider("Beat model: minimum margin floor", 0, 40, 10, 1) / 100
    st.caption("Margin model: margin % per component (% of sell price)")
    from app.rating.accessorials import COMPONENTS, COMPONENT_LABELS  # noqa: E402
    margins = {}
    with st.expander("Per-component margins", expanded=True):
        for comp in COMPONENTS:
            margins[comp] = st.number_input(f"{COMPONENT_LABELS[comp]} margin %", 0, 90, 25, 1,
                                            key=f"mgn_{comp}") / 100
        margins["default"] = st.number_input("Other / default margin %", 0, 90, 25, 1,
                                             key="mgn_default") / 100
    pricing_basis = st.radio("Customer-report price basis", ["beat", "margin"],
                             format_func=lambda b: "Beat competitor" if b == "beat" else "Margin")

    st.caption("Fuel surcharges (current published — editable)")
    fuel_inputs = {}
    for (carrier, cls), fr in fuelmod.FUEL.items():
        label = f"{carrier} {cls}"
        fuel_inputs[(carrier, cls)] = st.number_input(
            f"{label} %", min_value=0.0, max_value=80.0,
            value=round(fr.pct * 100, 2), step=0.25, key=f"fuel_{carrier}_{cls}",
            help=f"eff {fr.effective} ({'verified' if fr.verified else 'estimated'})",
        ) / 100

    st.caption("Carrier rate adjustment / extra discount (− lowers your cost)")
    adj = {
        "DHL": st.slider("DHL rate ±%", -50, 30, 0, 1,
                         help="Use a negative % to apply your DHL discount if the card is list-rate") / 100,
        "Canpar": st.slider("Canpar rate ±%", -50, 30, 0, 1) / 100,
        "Purolator": st.slider("Purolator rate ±%", -50, 30, 0, 1) / 100,
    }

    st.caption("UPS via your DAP (discount off the published charge on each invoice)")
    ups_on = st.checkbox("Quote UPS from published − DAP discount", value=False)
    ups_discount = (st.slider("UPS DAP discount % off published", 0, 80, 40, 1) / 100
                    if ups_on else None)
    report_mode = st.radio("Report mode", ["internal", "customer"], format_func=str.title)

# Apply fuel overrides to the module (single-user app; re-applied each run).
for key, pct in fuel_inputs.items():
    if key in fuelmod.FUEL:
        fr = fuelmod.FUEL[key]
        fuelmod.FUEL[key] = fuelmod.FuelRate(pct, fr.effective, fr.source, fr.verified)

tab_inv, tab_cards, tab_compare = st.tabs(["1 · Invoices", "2 · Rate cards", "3 · Comparison & margin"])

# ---- Tab 1: invoices ------------------------------------------------------ #
with tab_inv:
    st.subheader("Upload competitor invoices")
    uploads = st.file_uploader("UPS invoice PDFs", type=["pdf"], accept_multiple_files=True)
    use_samples = st.checkbox("Use the sample invoices included with the app", value=not uploads)

    files: list[tuple[str, bytes]] = []
    if uploads:
        files = [(u.name, u.getvalue()) for u in uploads]
    elif use_samples and os.path.isdir(SAMPLES_INV):
        files = [(f, open(os.path.join(SAMPLES_INV, f), "rb").read())
                 for f in sorted(os.listdir(SAMPLES_INV)) if f.lower().endswith(".pdf")]

    if files:
        invoices = parse_invoices(files)
        st.session_state["invoices"] = invoices
        rep: SpendReport = analyze(invoices)
        st.success(f"Parsed {len(files)} invoice(s) · {rep.shipment_count} shipments")
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Total spend", f"${rep.total_billed_cents/100:,.0f}")
        c2.metric("Shipments", rep.shipment_count)
        c3.metric("Fuel % of spend", f"{rep.fuel_pct:.1%}")
        c4.metric("Accessorials", f"${rep.accessorial_cents/100:,.0f}")
        c5.metric("Tax", f"${rep.tax_cents/100:,.0f}")

        # Accessorials & taxes broken out (totals across all invoices)
        with st.expander("Accessorials & taxes — totals broken out", expanded=True):
            acol, tcol = st.columns(2)
            acc_rows = [{"Accessorial": ACCESSORIAL_LABELS.get(k, k), "Amount $": v / 100}
                        for k, v in sorted(rep.by_accessorial.items(), key=lambda x: -x[1])]
            acol.caption("By accessorial type")
            acol.dataframe(pd.DataFrame(acc_rows), width="stretch", hide_index=True)
            tax_rows = [{"Tax": k, "Amount $": v / 100}
                        for k, v in sorted(rep.by_tax.items(), key=lambda x: -x[1])]
            tax_rows.append({"Tax": "Total tax", "Amount $": rep.tax_cents / 100})
            tcol.caption("By tax type")
            tcol.dataframe(pd.DataFrame(tax_rows), width="stretch", hide_index=True)

        # Per-shipment detail with every charge component as its own column
        st.caption("Shipment detail — base, each accessorial, and tax broken out")
        rows = []
        for inv in invoices:
            for s in inv.shipments:
                bd = shipment_breakdown(s)
                row = {
                    "Invoice": inv.invoice_number, "Tracking": s.tracking_number,
                    "Service": s.service, "Dest": s.dest_postal, "Country": s.dest_country,
                    "Billed wt": s.billed_weight, "Actual wt": s.actual_weight,
                    "Dims": (f"{s.length}x{s.width}x{s.height}" if s.length else ""),
                    "Base $": bd["base"],
                }
                for k in ACCESSORIAL_COLUMNS:
                    row[ACCESSORIAL_LABELS[k] + " $"] = bd[k]
                row["Tax $"] = bd["tax"]
                row["Total $"] = bd["total"]
                row["Review"] = "⚠️" if s.field_confidence.get("total_reconciled") != 1 else ""
                rows.append(row)
        df = pd.DataFrame(rows)
        # Drop accessorial columns that are zero across the whole dataset, to keep it tidy.
        for k in ACCESSORIAL_COLUMNS:
            col = ACCESSORIAL_LABELS[k] + " $"
            if col in df and df[col].abs().sum() == 0:
                df = df.drop(columns=[col])
        st.dataframe(df, width="stretch", height=340)
        st.download_button("⬇ Download shipment detail (CSV)", df.to_csv(index=False).encode(),
                           file_name="freight-iq-shipment-detail.csv", mime="text/csv")
    else:
        st.info("Upload UPS invoice PDFs, or tick the sample-invoices box to explore.")

# ---- Tab 2: rate cards ---------------------------------------------------- #
with tab_cards:
    st.subheader("Carrier rate cards")
    st.caption("Defaults are loaded from the app's sample cards. Re-upload to replace a carrier, "
               "or use the ± sliders in the sidebar to nudge base rates.")
    cards: dict = {}
    for carrier, glob_part, sheet in DEFAULT_CARDS:
        col1, col2 = st.columns([2, 3])
        up = col2.file_uploader(f"Replace {carrier} rates (Excel or PDF)",
                                type=["xls", "xlsx", "pdf"], key=f"up_{carrier}")
        card = None
        if up is not None:
            card = load_card_bytes(carrier, sheet, up.name, up.getvalue())
            col1.success(f"{carrier}: uploaded {up.name} ({len(card.products)} products)")
        else:
            path = _sample_file(SAMPLES_CARDS, glob_part)
            if path:
                card = load_card_bytes(carrier, sheet, os.path.basename(path),
                                       open(path, "rb").read())
                col1.info(f"{carrier}: sample card ({len(card.products)} products)")
            else:
                col1.warning(f"{carrier}: no card found")
        if card is not None:
            cards[carrier] = adjust_card(card, adj.get(carrier, 0.0))

    # ---- Add another carrier (e.g. FedEx, Freightcom) ---- #
    st.divider()
    st.markdown("**Add another carrier** — upload any domestic carrier's rate card")
    # Reset previously-registered extra carriers so removals take effect.
    for extra in [c for c in carriersmod.DOMESTIC_CARRIERS if c not in BUILTIN_DOMESTIC]:
        del carriersmod.DOMESTIC_CARRIERS[extra]
    n_extra = st.number_input("Number of additional carriers", 0, 6, 0, 1)
    for i in range(int(n_extra)):
        st.caption(f"Additional carrier #{i + 1}")
        cc = st.columns([2, 2, 1, 1, 1])
        name = cc[0].text_input("Name", key=f"xc_name_{i}", placeholder="e.g. FedEx")
        up = cc[1].file_uploader("Rate card (Excel/PDF)", type=["xls", "xlsx", "pdf"],
                                 key=f"xc_file_{i}")
        zprefix = cc[2].text_input("Zone prefix", key=f"xc_prefix_{i}", value="",
                                   help="Blank = numeric zones 1..n; 'D' = D01..Dnn")
        dimdiv = cc[3].number_input("DIM ÷", 100.0, 200.0, 139.0, 1.0, key=f"xc_dim_{i}")
        xfuel = cc[4].number_input("Fuel %", 0.0, 80.0, 0.0, 0.25, key=f"xc_fuel_{i}") / 100
        if name and up is not None:
            xcard = load_card_bytes(name, None, up.name, up.getvalue())
            prods = st.multiselect(f"{name}: products to quote (blank = all)",
                                   list(xcard.products), key=f"xc_prods_{i}")
            register_domestic_carrier(name, prods or None, zprefix.strip(), dimdiv, xfuel)
            cards[name] = adjust_card(xcard, adj.get(name, 0.0))
            st.success(f"{name}: registered ({len(xcard.products)} products in card)")

    st.session_state["cards"] = cards

    st.divider()
    st.markdown("**Domestic zone charts** (FSA → zone) — exact zones instead of estimates")
    zonesmod.ZONE_CHARTS.clear()
    for carrier in list(carriersmod.DOMESTIC_CARRIERS):
        zc = st.file_uploader(f"{carrier} FSA→zone chart (Excel or PDF)",
                              type=["xls", "xlsx", "pdf"], key=f"zone_{carrier}")
        if zc is not None:
            suffix = os.path.splitext(zc.name)[1].lower()
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tf:
                tf.write(zc.getvalue())
                zpath = tf.name
            try:
                mapping = zonesmod.load_zone_chart(zpath)
            finally:
                os.unlink(zpath)
            zonesmod.set_chart(carrier, mapping)
            st.success(f"{carrier}: loaded {len(mapping)} FSA→zone mappings (exact zones now used)")
        else:
            st.caption(f"{carrier}: no chart — zone estimated from destination province")
    st.caption("Carrier-specific dimensional weight is always applied.")

    st.divider()
    st.markdown("**Manual costs** — for carriers with no rate card / manual costing")
    st.caption("Upload a CSV or Excel with columns: **tracking**, **cost** (and optionally "
               "**carrier**, **service**). Matching shipments use your manual cost instead of a "
               "computed rate.")
    manual_costs = {}
    mc = st.file_uploader("Manual cost file (CSV or Excel)", type=["csv", "xlsx", "xls"],
                          key="manual_cost_file")
    if mc is not None:
        try:
            if mc.name.lower().endswith(".csv"):
                mdf = pd.read_csv(mc)
            else:
                mdf = pd.read_excel(mc)
            manual_costs = parse_manual_costs(mdf.to_dict("records"))
            st.success(f"Loaded {len(manual_costs)} manual cost overrides "
                       f"(matched by tracking number).")
        except Exception as e:
            st.error(f"Could not read manual cost file: {e}")
    # Template download
    tmpl = pd.DataFrame([{"tracking": "1ZE88F61...", "cost": 42.50,
                          "carrier": "FedEx", "service": "Ground"}])
    st.download_button("⬇ Manual cost template (CSV)", tmpl.to_csv(index=False).encode(),
                       file_name="manual-cost-template.csv", mime="text/csv")
    st.session_state["manual_costs"] = manual_costs

    st.divider()
    st.markdown("**Accessorial fees** ($ your carriers charge you) — pulled defaults, editable")
    st.caption("Applied to a shipment when the competitor invoice shows that accessorial. "
               "Each gets its own margin (sidebar). Edit to match your reseller fees.")
    from app.rating import accessorials as accmod  # noqa: E402
    fee_carriers = [c for c in (*cards.keys(),) if c in accmod.FEES] or list(accmod.FEES)
    fee_df = pd.DataFrame({
        accmod.COMPONENT_LABELS[comp]: {c: accmod.FEES.get(c, {}).get(comp, 0) / 100
                                        for c in fee_carriers}
        for comp in accmod.ACCESSORIAL_COMPONENTS
    })
    edited = st.data_editor(fee_df, width="stretch", key="acc_fees")
    # write edits back to the module (cents)
    for carrier in edited.index:
        for comp in accmod.ACCESSORIAL_COMPONENTS:
            label = accmod.COMPONENT_LABELS[comp]
            val = edited.loc[carrier, label]
            if pd.notna(val):
                accmod.FEES.setdefault(carrier, {})[comp] = int(round(float(val) * 100))

# ---- Tab 3: comparison ---------------------------------------------------- #
with tab_compare:
    st.subheader("Rate comparison & suggested margin")
    invoices = st.session_state.get("invoices")
    cards = st.session_state.get("cards")
    if not invoices:
        st.info("Add invoices in tab 1 first.")
    elif not cards:
        st.info("Load rate cards in tab 2 first.")
    else:
        manual_costs = st.session_state.get("manual_costs") or {}
        rows = build_rows(invoices, cards, manual_costs, ups_discount)
        records = rows_to_records(rows, target_savings, min_margin, margins)
        summary = summarize(records)
        if manual_costs:
            st.caption(f"Using {len(manual_costs)} manual cost override(s) from tab 2.")
        if summary.get("estimated_zone"):
            st.warning(
                f"⚠️ {summary['estimated_zone']} lane(s) use an ESTIMATED zone (guessed from "
                "destination province), so their domestic costs are NOT reliable — different "
                "carriers use different zone systems. Load the carrier FSA→zone charts in tab 2 "
                "to make these exact. The 'zone' column below flags each lane.",
                icon="⚠️",
            )

        k1, k2, k3, k4, k5 = st.columns(5)
        k1.metric("Winnable lanes", f"{summary['winnable']} / {summary['serviceable']}")
        k2.metric("Competitor spend", f"${summary['competitor_total']:,.0f}")
        k3.metric("Margin · beat model", f"${summary['total_margin']:,.0f}")
        k4.metric("Margin · margin model", f"${summary['margin_total_margin']:,.0f}")
        k5.metric("Customer savings · beat", f"${summary['total_customer_savings']:,.0f}")
        if summary["by_carrier_margin"]:
            st.caption("Winnable by carrier (beat model): " + "  ·  ".join(
                f"{c}: {summary['by_carrier_lanes'][c]} lanes (${summary['by_carrier_margin'][c]:,.0f})"
                for c in summary["by_carrier_margin"]))

        st.markdown("**Best carrier per lane — both pricing models**")
        df = pd.DataFrame(records)
        if report_mode == "customer":
            sell_k = "mgn_sell" if pricing_basis == "margin" else "beat_sell"
            save_k = "mgn_savings" if pricing_basis == "margin" else "beat_savings"
            view = df[(df[save_k].notna()) & (df[save_k] >= 0)][
                ["tracking", "competitor_service", "my_carrier", "competitor_pays", sell_k, save_k]
            ].rename(columns={"competitor_pays": "current_price", sell_k: "your_price",
                              save_k: "you_save", "my_carrier": "carrier"})
            st.dataframe(view, width="stretch", height=360, hide_index=True)
        else:
            cols = ["tracking", "scope", "pickup", "delivery", "actual_wt", "billable_wt",
                    "dims", "weight_basis", "my_carrier", "zone_basis", "competitor_pays",
                    "my_cost", "difference", "status", "beat_sell", "beat_margin",
                    "beat_margin_pct", "mgn_sell", "mgn_margin", "mgn_margin_pct"]

            def _style(r):
                if r.get("status") == "HIGH":
                    return [f"color: {brand.RED}"] * len(r)
                if r.get("status") == "LOW":
                    return [f"color: {brand.MIDNIGHT_BLUE}"] * len(r)
                return [""] * len(r)

            st.dataframe(df[cols].style.apply(_style, axis=1), width="stretch", height=360)
            st.caption("RED = my cost is HIGH (can't beat their price).  Dark = competitive.  "
                       "beat_* = beat-competitor pricing · mgn_* = target-margin pricing.")

        # ---- All carriers side by side ---- #
        st.markdown("**All carriers side by side** — every carrier's cost per lane (cheapest wins)")
        carrier_cols = [c for c in df.columns if c.endswith("_cost") and c != "my_cost"]
        side = df[["tracking", "pickup", "delivery", "actual_wt", "billable_wt", "dims",
                   "weight_basis", "scope", "zone_basis", "competitor_pays", *carrier_cols,
                   "my_carrier", "my_cost"]].rename(columns={"competitor_pays": "UPS_pays",
                                                            "my_carrier": "cheapest"})

        def _hl_best(r):
            out = [""] * len(r)
            best = r["cheapest"]
            for i, col in enumerate(side.columns):
                if col == f"{best}_cost":
                    out[i] = f"background-color: {brand.SPRING_GREEN}33; font-weight:600"
            return out

        st.dataframe(side.style.apply(_hl_best, axis=1), width="stretch", height=300,
                     hide_index=True)
        st.caption("Green = cheapest carrier for that lane. Blank cost = carrier doesn't serve "
                   "that lane (e.g. DHL has no domestic product; Canpar/Purolator are domestic only).")

        settings = {"target_customer_savings": target_savings, "min_margin_pct": min_margin,
                    "pricing_basis": pricing_basis}
        xlsx = build_workbook(records, summary, report_mode, settings)
        st.download_button(
            f"⬇ Download {report_mode} report (Excel) — "
            f"{'target-margin' if pricing_basis == 'margin' else 'beat'} pricing",
            data=xlsx, file_name=f"freight-iq-{report_mode}-report.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
