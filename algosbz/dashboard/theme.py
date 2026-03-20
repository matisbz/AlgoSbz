"""
AlgoSbz Dashboard Theme — dark trading terminal aesthetic.
Custom CSS + Plotly template for a cohesive professional look.
"""
import plotly.graph_objects as go
import plotly.io as pio

# ── Color Palette ──────────────────────────────────────────
COLORS = {
    "bg": "#0a0e14",
    "surface": "#12161e",
    "card": "#171c26",
    "border": "#1e2533",
    "border_accent": "#2a3142",
    "text": "#c8d1dc",
    "text_muted": "#6b7a90",
    "text_bright": "#e8edf4",
    "accent": "#00d4aa",       # teal — primary action, positive
    "accent_dim": "#00a884",
    "negative": "#ff4757",     # red — loss, fail
    "negative_dim": "#cc3a47",
    "blue": "#3b82f6",
    "purple": "#a855f7",
    "orange": "#f59e0b",
    "cyan": "#06b6d4",
    "green_candle": "#00d4aa",
    "red_candle": "#ff4757",
}

SERIES_COLORS = [
    COLORS["blue"],
    COLORS["accent"],
    COLORS["orange"],
    COLORS["purple"],
    COLORS["cyan"],
    COLORS["negative"],
]

# ── Custom Plotly Template ─────────────────────────────────
_template = go.layout.Template()
_template.layout = go.Layout(
    paper_bgcolor=COLORS["card"],
    plot_bgcolor=COLORS["surface"],
    font=dict(family="Inter, -apple-system, sans-serif", color=COLORS["text"], size=12),
    title=dict(font=dict(size=14, color=COLORS["text_bright"]), x=0, xanchor="left", pad=dict(l=12, t=8)),
    xaxis=dict(
        gridcolor=COLORS["border"],
        gridwidth=1,
        zerolinecolor=COLORS["border"],
        linecolor=COLORS["border"],
        tickfont=dict(size=10, color=COLORS["text_muted"]),
        title=dict(font=dict(size=11, color=COLORS["text_muted"])),
    ),
    yaxis=dict(
        gridcolor=COLORS["border"],
        gridwidth=1,
        zerolinecolor=COLORS["border"],
        linecolor=COLORS["border"],
        tickfont=dict(size=10, color=COLORS["text_muted"]),
        title=dict(font=dict(size=11, color=COLORS["text_muted"])),
        side="right",
    ),
    margin=dict(l=16, r=16, t=48, b=32),
    legend=dict(
        bgcolor="rgba(0,0,0,0)",
        borderwidth=0,
        font=dict(size=11, color=COLORS["text_muted"]),
        orientation="h",
        yanchor="bottom",
        y=1.02,
        xanchor="left",
        x=0,
    ),
    hoverlabel=dict(
        bgcolor=COLORS["card"],
        bordercolor=COLORS["border_accent"],
        font=dict(size=12, color=COLORS["text"]),
    ),
    colorway=SERIES_COLORS,
)

pio.templates["algosbz"] = _template
pio.templates.default = "algosbz"


# ── Streamlit CSS ──────────────────────────────────────────
CUSTOM_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap');

/* ── Root overrides ── */
:root {
    --bg-primary: #0a0e14;
    --bg-surface: #12161e;
    --bg-card: #171c26;
    --border: #1e2533;
    --accent: #00d4aa;
    --negative: #ff4757;
    --text: #c8d1dc;
    --text-muted: #6b7a90;
    --text-bright: #e8edf4;
}

/* Global background */
.stApp, [data-testid="stAppViewContainer"] {
    background-color: var(--bg-primary) !important;
}
header[data-testid="stHeader"] {
    background-color: var(--bg-primary) !important;
}

/* Sidebar */
section[data-testid="stSidebar"] {
    background-color: var(--bg-surface) !important;
    border-right: 1px solid var(--border) !important;
}
section[data-testid="stSidebar"] .stRadio label {
    font-family: 'Inter', sans-serif !important;
    font-weight: 500 !important;
    font-size: 0.9rem !important;
}

/* Typography */
.stApp, .stApp p, .stApp span, .stApp label, .stApp div {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif !important;
}
h1, h2, h3, .stApp h1, .stApp h2, .stApp h3 {
    font-family: 'Inter', sans-serif !important;
    font-weight: 600 !important;
    color: var(--text-bright) !important;
    letter-spacing: -0.02em;
}

/* ── Metric cards ── */
[data-testid="stMetric"] {
    background-color: var(--bg-card) !important;
    border: 1px solid var(--border) !important;
    border-radius: 10px !important;
    padding: 16px 20px !important;
}
[data-testid="stMetric"] label {
    color: var(--text-muted) !important;
    font-size: 0.72rem !important;
    font-weight: 500 !important;
    text-transform: uppercase !important;
    letter-spacing: 0.06em !important;
}
[data-testid="stMetric"] [data-testid="stMetricValue"] {
    font-family: 'JetBrains Mono', monospace !important;
    font-size: 1.4rem !important;
    font-weight: 600 !important;
    color: var(--text-bright) !important;
}
[data-testid="stMetric"] [data-testid="stMetricDelta"] {
    font-family: 'JetBrains Mono', monospace !important;
}

/* ── Buttons ── */
.stButton > button[kind="primary"],
button[data-testid="stBaseButton-primary"] {
    background: linear-gradient(135deg, var(--accent), #00a884) !important;
    color: #0a0e14 !important;
    border: none !important;
    font-weight: 600 !important;
    font-size: 0.9rem !important;
    border-radius: 8px !important;
    padding: 0.6rem 1.5rem !important;
    transition: all 0.2s ease !important;
    letter-spacing: 0.02em;
}
.stButton > button[kind="primary"]:hover,
button[data-testid="stBaseButton-primary"]:hover {
    filter: brightness(1.1) !important;
    box-shadow: 0 4px 20px rgba(0, 212, 170, 0.25) !important;
}

/* ── Inputs ── */
[data-testid="stSelectbox"],
[data-testid="stDateInput"],
[data-testid="stNumberInput"],
[data-testid="stMultiSelect"] {
    font-family: 'Inter', sans-serif !important;
}
input, [data-baseweb="select"] {
    background-color: var(--bg-card) !important;
    border-color: var(--border) !important;
    border-radius: 8px !important;
    color: var(--text) !important;
}

/* ── Expander ── */
[data-testid="stExpander"] {
    background-color: var(--bg-card) !important;
    border: 1px solid var(--border) !important;
    border-radius: 10px !important;
}

/* ── Dataframe ── */
[data-testid="stDataFrame"] {
    border: 1px solid var(--border) !important;
    border-radius: 10px !important;
    overflow: hidden !important;
}

/* ── Dividers ── */
hr {
    border-color: var(--border) !important;
    opacity: 0.5 !important;
    margin: 1.5rem 0 !important;
}

/* ── Progress bar ── */
[data-testid="stProgress"] > div > div {
    background-color: var(--accent) !important;
}

/* ── Plotly chart container ── */
[data-testid="stPlotlyChart"] {
    border: 1px solid var(--border) !important;
    border-radius: 10px !important;
    overflow: hidden !important;
    padding: 4px !important;
    background-color: var(--bg-card) !important;
}

/* ── Custom card class (injected via markdown) ── */
.kpi-row {
    display: flex;
    gap: 12px;
    flex-wrap: wrap;
    margin: 12px 0 20px 0;
}
.kpi-card {
    flex: 1;
    min-width: 140px;
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 16px 20px;
    text-align: left;
}
.kpi-card .kpi-label {
    font-size: 0.7rem;
    font-weight: 500;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--text-muted);
    margin-bottom: 4px;
}
.kpi-card .kpi-value {
    font-family: 'JetBrains Mono', monospace;
    font-size: 1.35rem;
    font-weight: 600;
    color: var(--text-bright);
}
.kpi-card .kpi-value.positive { color: var(--accent); }
.kpi-card .kpi-value.negative { color: var(--negative); }

/* ── Phase result badge ── */
.phase-badge {
    display: inline-block;
    padding: 6px 18px;
    border-radius: 20px;
    font-family: 'Inter', sans-serif;
    font-weight: 700;
    font-size: 0.85rem;
    letter-spacing: 0.04em;
    text-transform: uppercase;
}
.phase-badge.pass {
    background: rgba(0, 212, 170, 0.15);
    color: var(--accent);
    border: 1px solid rgba(0, 212, 170, 0.3);
}
.phase-badge.fail {
    background: rgba(255, 71, 87, 0.15);
    color: var(--negative);
    border: 1px solid rgba(255, 71, 87, 0.3);
}

/* ── Section header ── */
.section-title {
    font-family: 'Inter', sans-serif;
    font-weight: 600;
    font-size: 1.1rem;
    color: var(--text-bright);
    margin: 28px 0 12px 0;
    padding-bottom: 8px;
    border-bottom: 1px solid var(--border);
}

/* ── Sidebar branding ── */
.sidebar-brand {
    padding: 8px 0 20px 0;
}
.sidebar-brand h1 {
    font-size: 1.6rem !important;
    font-weight: 700 !important;
    background: linear-gradient(135deg, var(--accent), #3b82f6);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
    margin-bottom: 2px !important;
}
.sidebar-brand p {
    font-size: 0.75rem !important;
    color: var(--text-muted) !important;
    letter-spacing: 0.04em;
}

/* ── Nav pills ── */
.nav-item {
    display: block;
    padding: 10px 16px;
    margin: 2px 0;
    border-radius: 8px;
    font-size: 0.88rem;
    font-weight: 500;
    color: var(--text-muted);
    text-decoration: none;
    transition: all 0.15s ease;
    cursor: pointer;
}
.nav-item:hover { background: rgba(255,255,255,0.04); color: var(--text); }
.nav-item.active {
    background: rgba(0, 212, 170, 0.1);
    color: var(--accent);
    font-weight: 600;
}

/* Hide default streamlit elements for cleaner look */
#MainMenu, footer, header[data-testid="stHeader"] { visibility: hidden; }
.stDeployButton { display: none; }
</style>
"""


def inject_theme():
    """Call at the top of app.py to apply custom theme."""
    import streamlit as st
    st.markdown(CUSTOM_CSS, unsafe_allow_html=True)


def section_title(text: str):
    """Render a styled section title."""
    import streamlit as st
    st.markdown(f'<div class="section-title">{text}</div>', unsafe_allow_html=True)


def kpi_row(metrics: list[tuple[str, str, str]]):
    """
    Render a row of KPI cards.
    metrics: list of (label, value, color_class) where color_class is '', 'positive', or 'negative'.
    """
    import streamlit as st
    cards = ""
    for label, value, color_class in metrics:
        cards += f'''
        <div class="kpi-card">
            <div class="kpi-label">{label}</div>
            <div class="kpi-value {color_class}">{value}</div>
        </div>'''
    st.markdown(f'<div class="kpi-row">{cards}</div>', unsafe_allow_html=True)


def phase_badge(passed: bool, text: str):
    """Render a pass/fail badge."""
    import streamlit as st
    cls = "pass" if passed else "fail"
    st.markdown(f'<span class="phase-badge {cls}">{text}</span>', unsafe_allow_html=True)
