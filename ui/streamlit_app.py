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
from app.analysis.spend import analyze, SpendReport  # noqa: E402
from app.parsers.ups import UPSParser  # noqa: E402
from app.rating import fuel as fuelmod  # noqa: E402
from app.rating import zones as zonesmod  # noqa: E402
from app.rating.cards import adjust_card, load_any, load_card  # noqa: E402
from app.rating.comparison import build_rows, rows_to_records, summarize  # noqa: E402
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
    st.caption("Pricing")
    target_savings = st.slider("Customer savings offered (vs their price)", 0, 40, 15, 1) / 100
    min_margin = st.slider("Minimum margin floor", 0, 40, 10, 1) / 100

    st.caption("Fuel surcharges (current published — editable)")
    fuel_inputs = {}
    for (carrier, cls), fr in fuelmod.FUEL.items():
        label = f"{carrier} {cls}"
        fuel_inputs[(carrier, cls)] = st.number_input(
            f"{label} %", min_value=0.0, max_value=80.0,
            value=round(fr.pct * 100, 2), step=0.25, key=f"fuel_{carrier}_{cls}",
            help=f"eff {fr.effective} ({'verified' if fr.verified else 'estimated'})",
        ) / 100

    st.caption("Base-rate adjustment per carrier")
    adj = {
        "DHL": st.slider("DHL base ±%", -30, 30, 0, 1) / 100,
        "Canpar": st.slider("Canpar base ±%", -30, 30, 0, 1) / 100,
        "Purolator": st.slider("Purolator base ±%", -30, 30, 0, 1) / 100,
    }
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
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total spend", f"${rep.total_billed_cents/100:,.0f}")
        c2.metric("Shipments", rep.shipment_count)
        c3.metric("Fuel % of spend", f"{rep.fuel_pct:.1%}")
        c4.metric("Avg / shipment", f"${rep.avg_cost_per_shipment:,.2f}")

        rows = []
        for inv in invoices:
            for s in inv.shipments:
                rows.append({
                    "Invoice": inv.invoice_number, "Tracking": s.tracking_number,
                    "Service": s.service, "Dest": s.dest_postal, "Country": s.dest_country,
                    "Billed wt": s.billed_weight, "Actual wt": s.actual_weight,
                    "Dims": (f"{s.length}x{s.width}x{s.height}" if s.length else ""),
                    "Total $": s.total_charge_cents / 100,
                    "Review": "⚠️" if s.field_confidence.get("total_reconciled") != 1 else "",
                })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, height=320)
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
    st.session_state["cards"] = cards

    st.divider()
    st.markdown("**Domestic zone charts** (FSA → zone) — exact zones instead of estimates")
    zonesmod.ZONE_CHARTS.clear()
    for carrier in ("Canpar", "Purolator"):
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
        rows = build_rows(invoices, cards)
        records = rows_to_records(rows, target_savings, min_margin)
        summary = summarize(records)

        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Winnable lanes", f"{summary['winnable']} / {summary['serviceable']}")
        k2.metric("Competitor spend", f"${summary['competitor_total']:,.0f}")
        k3.metric("Total margin (if won)", f"${summary['total_margin']:,.0f}")
        k4.metric("Customer savings", f"${summary['total_customer_savings']:,.0f}")
        if summary["by_carrier_margin"]:
            st.caption("Winnable by carrier: " + "  ·  ".join(
                f"{c}: {summary['by_carrier_lanes'][c]} lanes (${summary['by_carrier_margin'][c]:,.0f})"
                for c in summary["by_carrier_margin"]))

        df = pd.DataFrame(records)
        if report_mode == "customer":
            df = df[df["status"] == "LOW"][
                ["tracking", "competitor_service", "competitor_pays", "suggested_sell",
                 "customer_savings", "margin_pct"]
            ].rename(columns={"competitor_pays": "current_price", "suggested_sell": "your_price",
                              "customer_savings": "you_save", "margin_pct": "pct_saved"})

        def _style(row):
            if report_mode == "internal" and row.get("status") == "HIGH":
                return [f"color: {brand.RED}"] * len(row)
            if report_mode == "internal" and row.get("status") == "LOW":
                return [f"color: {brand.MIDNIGHT_BLUE}"] * len(row)
            return [""] * len(row)

        st.dataframe(df.style.apply(_style, axis=1), use_container_width=True, height=380)
        st.caption("RED = my cost is HIGH (can't beat their price).  Dark = competitive.")

        settings = {"target_customer_savings": target_savings, "min_margin_pct": min_margin}
        xlsx = build_workbook(records, summary, report_mode, settings)
        st.download_button(
            f"⬇ Download {report_mode} report (Excel)", data=xlsx,
            file_name=f"freight-iq-{report_mode}-report.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
