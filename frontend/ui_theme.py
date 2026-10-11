"""Dark SaaS theme (CSS variables) and small HTML helpers for the Streamlit frontend.

Every helper that renders dynamic text escapes it. Dollar signs are escaped too, because
Streamlit's markdown would otherwise treat "$1 ... $2" as LaTeX.
"""

import html

import streamlit as st

THEME_CSS = """
<style>
:root {
  --bg: #0a0c12;
  --surface: #141824;
  --surface-2: #1a2030;
  --border: rgba(255, 255, 255, 0.08);
  --border-strong: rgba(255, 255, 255, 0.18);
  --text: #eceef4;
  --text-soft: #c4c9db;
  --muted: #8c94a8;
  --accent: #7c5cff;
  --accent-2: #4d8dff;
  --accent-grad: linear-gradient(135deg, #7c5cff 0%, #4d8dff 100%);
  --good: #34d399;
  --warn: #fbbf24;
  --bad: #f87171;
  --info: #60a5fa;
  --radius: 16px;
  --radius-sm: 12px;
  --shadow: 0 12px 32px rgba(0, 0, 0, 0.35);
  --font: Inter, "Segoe UI", system-ui, -apple-system, Roboto, sans-serif;
}

.stApp {
  background:
    radial-gradient(1100px 560px at 10% -10%, rgba(124, 92, 255, 0.16), transparent 60%),
    radial-gradient(900px 480px at 100% 0%, rgba(77, 141, 255, 0.11), transparent 55%),
    var(--bg);
  color: var(--text);
  font-family: var(--font);
}
#MainMenu, footer, [data-testid="stToolbar"], [data-testid="stDecoration"] { display: none !important; }
div[data-testid="stElementContainer"]:has(style) { display: none; }
header[data-testid="stHeader"] { background: transparent; }
.block-container { max-width: 1120px; padding-top: 2.2rem; padding-bottom: 4rem; }
h1, h2, h3, h4 { font-family: var(--font); letter-spacing: -0.01em; }
hr { border-color: var(--border) !important; }

/* ---- Buttons ---- */
.stButton > button, .stFormSubmitButton > button {
  border-radius: var(--radius-sm);
  border: 1px solid var(--border-strong);
  background: var(--surface-2);
  color: var(--text);
  font-weight: 600;
  padding: 0.62rem 1.15rem;
  transition: border-color .15s ease, transform .15s ease, box-shadow .15s ease;
}
.stButton > button:hover, .stFormSubmitButton > button:hover {
  border-color: var(--accent);
  color: #fff;
  transform: translateY(-1px);
}
.stButton > button[kind="primary"], .stFormSubmitButton > button[kind="primaryFormSubmit"],
[data-testid="stBaseButton-primary"], [data-testid="stBaseButton-primaryFormSubmit"] {
  background: var(--accent-grad);
  border: 0;
  color: #fff;
  box-shadow: 0 8px 22px rgba(124, 92, 255, 0.35);
}
.stButton > button:disabled { opacity: 0.45; transform: none; box-shadow: none; }
[class*="st-key-back-"] button {
  background: transparent; border: 0; color: var(--muted); padding-left: 0; box-shadow: none; font-weight: 500;
}
[class*="st-key-back-"] button:hover { color: var(--text); transform: none; }

/* ---- Inputs ---- */
.stTextArea textarea, .stTextInput input, div[data-baseweb="select"] > div {
  background: var(--surface) !important;
  border: 1px solid var(--border) !important;
  border-radius: var(--radius-sm) !important;
  color: var(--text) !important;
}
.stTextArea textarea { font-size: 1.02rem; line-height: 1.6; }
.stTextArea textarea:focus, .stTextInput input:focus { border-color: var(--accent) !important; box-shadow: 0 0 0 1px var(--accent) !important; }
label, [data-testid="stWidgetLabel"] p { color: var(--text-soft) !important; font-weight: 500; }
[data-testid="stExpander"] { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); margin-top: 1rem; }
[class*="st-key-card-interview-"] .stButton { margin-top: .8rem; }
[data-testid="stExpander"] summary { font-weight: 600; }
.stProgress > div > div > div > div { background: var(--accent-grad) !important; }
[data-testid="stFileUploader"] section { background: var(--surface); border: 1px dashed var(--border-strong); border-radius: var(--radius-sm); }

/* ---- Cards (st.container(key="card-...")) ---- */
[class*="st-key-card-"] {
  background: linear-gradient(160deg, rgba(255,255,255,0.045), rgba(255,255,255,0.015));
  border: 1px solid var(--border);
  border-radius: 20px;
  padding: 1.6rem 1.7rem;
  box-shadow: var(--shadow);
  backdrop-filter: blur(10px);
}
[class*="st-key-row-"] {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 1rem 1.2rem;
  margin-bottom: .6rem;
}

/* ---- Typography ---- */
.ti-eyebrow { font-size: .72rem; letter-spacing: .18em; text-transform: uppercase; color: var(--muted); font-weight: 650; }
.ti-eyebrow.accent { background: var(--accent-grad); -webkit-background-clip: text; background-clip: text; color: transparent; }
.ti-hero { padding: 2.4rem 0 1.6rem; text-align: center; }
.ti-hero-title { font-size: 3.1rem; font-weight: 800; letter-spacing: -.03em; line-height: 1.05; margin: .5rem 0 .6rem; }
.ti-hero-sub { font-size: 1.3rem; color: var(--text-soft); font-weight: 500; margin-bottom: .8rem; }
.ti-hero-copy { color: var(--muted); max-width: 640px; margin: 0 auto; line-height: 1.6; }
.ti-page-title { font-size: 2rem; font-weight: 750; letter-spacing: -.02em; margin: .2rem 0 .3rem; }
.ti-page-sub { color: var(--muted); margin-bottom: 1rem; }
.ti-card-title { font-size: 1.35rem; font-weight: 700; margin: .3rem 0 .2rem; }
.ti-card-copy { color: var(--muted); font-size: .95rem; }
.ti-checklist { list-style: none; padding: 0; margin: 1rem 0 1.5rem; }
.ti-checklist li { color: var(--text-soft); padding: .28rem 0 .28rem 1.5rem; position: relative; }
.ti-checklist li::before { content: ""; position: absolute; left: 0; top: .72rem; width: .55rem; height: .55rem; border-radius: 50%; background: var(--accent-grad); }
.ti-section { margin: 2.2rem 0 .9rem; display: flex; align-items: baseline; justify-content: space-between; }
.ti-section-title { font-size: .8rem; letter-spacing: .16em; text-transform: uppercase; color: var(--text-soft); font-weight: 700; }
.ti-section-note { color: var(--muted); font-size: .85rem; }

/* ---- Stat / dimension cards ---- */
.ti-stat { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 1rem 1.15rem; height: 100%; }
.ti-stat-label { font-size: .72rem; letter-spacing: .12em; text-transform: uppercase; color: var(--muted); font-weight: 650; }
.ti-stat-value { font-size: 1.85rem; font-weight: 750; letter-spacing: -.02em; margin-top: .25rem; }
.ti-stat-value small { font-size: 1rem; color: var(--muted); font-weight: 600; }
.ti-stat-sub { color: var(--muted); font-size: .8rem; margin-top: .2rem; }
.ti-bar { height: 8px; background: rgba(255,255,255,.08); border-radius: 999px; overflow: hidden; margin-top: .7rem; }
.ti-bar > span { display: block; height: 100%; background: var(--accent-grad); border-radius: 999px; }

/* ---- Badges ---- */
.ti-badge { display: inline-block; padding: .22rem .7rem; border-radius: 999px; font-size: .75rem; font-weight: 650; border: 1px solid var(--border-strong); color: var(--text-soft); }
.ti-badge.scheduled { color: var(--info); border-color: rgba(96,165,250,.45); background: rgba(96,165,250,.1); }
.ti-badge.progress { color: var(--warn); border-color: rgba(251,191,36,.45); background: rgba(251,191,36,.1); }
.ti-badge.completed { color: #b9a6ff; border-color: rgba(124,92,255,.5); background: rgba(124,92,255,.12); }
.ti-badge.reviewed, .ti-badge.good { color: var(--good); border-color: rgba(52,211,153,.45); background: rgba(52,211,153,.1); }
.ti-badge.warn { color: var(--warn); border-color: rgba(251,191,36,.45); background: rgba(251,191,36,.1); }
.ti-badge.bad { color: var(--bad); border-color: rgba(248,113,113,.45); background: rgba(248,113,113,.1); }
.ti-badge.large { font-size: 1rem; padding: .35rem 1rem; }

/* ---- Interview + review content ---- */
.ti-fields { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: .9rem 1.4rem; }
.ti-field-label { font-size: .7rem; letter-spacing: .12em; text-transform: uppercase; color: var(--muted); font-weight: 650; }
.ti-field-value { font-size: 1.02rem; font-weight: 600; margin-top: .15rem; }
.ti-question { background: linear-gradient(160deg, rgba(124,92,255,.14), rgba(77,141,255,.05)); border: 1px solid rgba(124,92,255,.3); border-radius: 20px; padding: 1.5rem 1.8rem; margin: 1rem 0 1.4rem; box-shadow: var(--shadow); }
.ti-question-text { font-size: 1.32rem; line-height: 1.55; font-weight: 600; margin-top: .6rem; }
.ti-prose { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius-sm); padding: .9rem 1.1rem; color: var(--text-soft); line-height: 1.6; }
.ti-quote { border-left: 3px solid var(--accent); padding-left: .8rem; color: var(--muted); font-style: italic; margin: .35rem 0 .8rem; }
.ti-note { color: var(--muted); font-size: .85rem; margin-top: .5rem; }
.ti-final { background: linear-gradient(160deg, rgba(52,211,153,.12), rgba(52,211,153,.03)); border: 1px solid rgba(52,211,153,.35); border-radius: 20px; padding: 1.4rem 1.7rem; }
.ti-final-decision { font-size: 2rem; font-weight: 800; letter-spacing: -.02em; margin: .3rem 0; }
.ti-center { text-align: center; padding: 2.5rem 1rem; }
.ti-check { width: 64px; height: 64px; border-radius: 50%; margin: 0 auto 1.2rem; background: var(--accent-grad); display: flex; align-items: center; justify-content: center; font-size: 2rem; box-shadow: 0 10px 30px rgba(124,92,255,.4); }

/* ---- Consent notice: one readable column of titled sections ---- */
.ti-consent { background: var(--surface); border: 1px solid var(--border); border-radius: 20px; padding: 1.6rem 1.8rem; margin: .6rem 0 1rem; }
.ti-consent-title { font-size: 1.6rem; font-weight: 800; letter-spacing: -.02em; }
.ti-consent-intro { color: var(--text-soft); font-size: 1rem; line-height: 1.65; margin: .4rem 0 1.1rem; }
.ti-consent-section { border-left: 3px solid var(--accent); background: var(--surface-2); border-radius: 0 var(--radius-sm) var(--radius-sm) 0; padding: .85rem 1.1rem; margin: .75rem 0; }
.ti-consent-section h4 { font-size: 1.02rem; font-weight: 700; margin: 0 0 .35rem; color: var(--text); }
.ti-consent-section p, .ti-consent-section li { font-size: .98rem; line-height: 1.7; color: var(--text-soft); margin: 0; }
.ti-consent-section ul { margin: .1rem 0 0; padding-left: 1.2rem; }
.ti-consent-decline { border: 1px solid rgba(251,191,36,.45); background: rgba(251,191,36,.08); color: var(--text-soft); border-radius: var(--radius-sm); padding: .8rem 1.1rem; margin-top: 1rem; line-height: 1.6; }
.ti-partial-banner { border: 1px solid rgba(251,191,36,.5); background: rgba(251,191,36,.1); border-radius: 16px; padding: 1rem 1.3rem; margin: .8rem 0 1rem; }
.ti-partial-banner b { display: block; font-size: 1.15rem; color: var(--warn); margin-bottom: .2rem; }

@media (max-width: 768px) {
  .ti-consent { padding: 1.1rem 1rem; }
  .ti-consent-title { font-size: 1.35rem; }
  .ti-hero-title { font-size: 2.1rem; }
  .ti-page-title { font-size: 1.6rem; }
  .block-container { padding-left: 1rem; padding-right: 1rem; }
}
</style>
"""

NARROW_CSS = "<style>.block-container { max-width: 860px; }</style>"

_STATUS_KIND = {"Scheduled": "scheduled", "In Progress": "progress", "Completed": "completed", "Reviewed": "reviewed", "Abandoned": "bad"}
_RECOMMENDATION_KIND = {"Strong Hire": "good", "Hire": "good", "Hold": "warn", "No Hire": "bad"}


def esc(value) -> str:
    """Escape for embedding in HTML inside st.markdown (also neutralises $ math and blank lines)."""
    text = "" if value is None else str(value)
    return html.escape(text).replace("$", "&#36;").replace("\r\n", "\n").replace("\n", "<br>")


def inject_theme(narrow: bool = False) -> None:
    st.markdown(THEME_CSS, unsafe_allow_html=True)
    if narrow:
        st.markdown(NARROW_CSS, unsafe_allow_html=True)


def render(html_text: str) -> None:
    st.markdown(html_text, unsafe_allow_html=True)


def eyebrow(text: str, accent: bool = False) -> str:
    return f'<div class="ti-eyebrow{" accent" if accent else ""}">{esc(text)}</div>'


def section_title(title: str, note: str = "") -> str:
    note_html = f'<span class="ti-section-note">{esc(note)}</span>' if note else ""
    return f'<div class="ti-section"><span class="ti-section-title">{esc(title)}</span>{note_html}</div>'


def badge(text: str, kind: str = "", large: bool = False) -> str:
    return f'<span class="ti-badge {kind}{" large" if large else ""}">{esc(text)}</span>'


def status_badge(display_status, large: bool = False) -> str:
    return badge(display_status or "N/A", _STATUS_KIND.get(display_status, ""), large)


def recommendation_badge(recommendation, large: bool = False) -> str:
    return badge(recommendation or "N/A", _RECOMMENDATION_KIND.get(recommendation, ""), large)


def stat_card(label: str, value, sub: str = "", unit: str = "", min_height: int = 0) -> str:
    """The sub-line is always rendered (as a blank when empty) so cards in a row share one height."""
    unit_html = f" <small>{esc(unit)}</small>" if unit else ""
    style = f' style="min-height:{min_height}px"' if min_height else ""
    return (
        f'<div class="ti-stat"{style}><div class="ti-stat-label">{esc(label)}</div>'
        f'<div class="ti-stat-value">{esc(value)}{unit_html}</div>'
        f'<div class="ti-stat-sub">{esc(sub) if sub else "&nbsp;"}</div></div>'
    )


def dimension_card(label: str, value, scale: int = 5, sub: str = "", decimals: int = 1) -> str:
    """Score out of `scale` with a progress bar; value=None renders N/A with an empty bar."""
    if value is None:
        shown, width = "N/A", 0
    else:
        shown, width = f"{float(value):.{decimals}f}", max(0.0, min(100.0, float(value) / scale * 100))
    sub_html = f'<div class="ti-stat-sub">{esc(sub)}</div>' if sub else ""
    return (
        f'<div class="ti-stat"><div class="ti-stat-label">{esc(label)}</div>'
        f'<div class="ti-stat-value">{esc(shown)} <small>/ {scale}</small></div>'
        f'<div class="ti-bar"><span style="width:{width:.0f}%"></span></div>{sub_html}</div>'
    )


def fields_grid(fields) -> str:
    cells = "".join(
        f'<div><div class="ti-field-label">{esc(label)}</div><div class="ti-field-value">{value_html}</div></div>'
        for label, value_html in fields
    )
    return f'<div class="ti-fields">{cells}</div>'


def prose(text) -> str:
    return f'<div class="ti-prose">{esc(text)}</div>'
