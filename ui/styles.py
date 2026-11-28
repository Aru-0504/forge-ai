"""Custom CSS typography, layout polish, and reusable HTML components (badges, stepper, chips)."""

from __future__ import annotations

import html

PIPELINE_STAGES: tuple[str, ...] = (
    "intake",
    "groundability",
    "evalgen",
    "generator",
    "judge",
    "critic",
    "report",
)

STAGE_LABELS: dict[str, str] = {
    "intake": "1. Intake",
    "groundability": "2. Ground",
    "evalgen": "3. EvalGen",
    "generator": "4. Generate",
    "judge": "5. Judge",
    "critic": "6. Critic",
    "report": "7. Report",
}

CUSTOM_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap');

:root {
    --forge-font-sans: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    --forge-font-mono: 'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
}

/* Global typography & rhythm */
html, body, [class*="css"], .stApp {
    font-family: var(--forge-font-sans) !important;
    -webkit-font-smoothing: antialiased;
    -moz-osx-font-smoothing: grayscale;
}

.block-container {
    padding-top: 1.75rem;
    padding-bottom: 3rem;
    max-width: 1260px;
}

/* Headings & prose hierarchy */
.stMarkdown p, .stMarkdown li {
    font-size: 0.98rem;
    line-height: 1.62;
}
.stMarkdown h1 {
    font-size: 1.9rem !important;
    font-weight: 700 !important;
    letter-spacing: -0.025em !important;
}
.stMarkdown h2, [data-testid="stHeading"] h2 {
    font-size: 1.42rem !important;
    font-weight: 650 !important;
    letter-spacing: -0.018em !important;
    margin-top: 1.1rem !important;
}
.stMarkdown h3, [data-testid="stHeading"] h3 {
    font-size: 1.18rem !important;
    font-weight: 600 !important;
    letter-spacing: -0.012em !important;
}
.stCaption, [data-testid="stCaptionContainer"] p {
    font-size: 0.88rem !important;
    line-height: 1.5 !important;
    opacity: 0.88;
}

/* Code & monospace */
code, pre, .stCode, [data-testid="stCodeBlock"] code {
    font-family: var(--forge-font-mono) !important;
    font-size: 0.87rem !important;
    line-height: 1.55 !important;
}

/* Sidebar typography */
[data-testid="stSidebar"] h1 {
    font-size: 1.55rem !important;
    font-weight: 700 !important;
    letter-spacing: -0.02em !important;
}
[data-testid="stSidebar"] [data-testid="stRadio"] label p {
    font-size: 0.96rem !important;
    font-weight: 500 !important;
    padding: 0.15rem 0;
}

/* Form labels, inputs, tabs & buttons */
[data-testid="stWidgetLabel"] p {
    font-size: 0.93rem !important;
    font-weight: 600 !important;
    letter-spacing: -0.005em;
}
textarea, input[type="text"], input[type="password"] {
    font-family: var(--forge-font-sans) !important;
    font-size: 0.95rem !important;
    line-height: 1.5 !important;
}
button[data-baseweb="tab"] p {
    font-size: 0.95rem !important;
    font-weight: 600 !important;
}
.stButton button p, .stDownloadButton button p, [data-testid="stFormSubmitButton"] button p {
    font-size: 0.92rem !important;
    font-weight: 600 !important;
}

/* Balanced KPI metric cards */
[data-testid="stMetric"] {
    background: rgba(15, 23, 42, 0.45);
    border: 1px solid rgba(148, 163, 184, 0.2);
    border-radius: 12px;
    padding: 0.85rem 1rem;
}
[data-testid="stMetricLabel"] p {
    font-size: 0.8rem !important;
    font-weight: 600 !important;
    text-transform: uppercase;
    letter-spacing: 0.045em;
    opacity: 0.82;
}
[data-testid="stMetricValue"] {
    font-size: 1.48rem !important;
    font-weight: 700 !important;
    letter-spacing: -0.02em;
    line-height: 1.25 !important;
}

/* Expanders & chat bubbles */
[data-testid="stExpander"] summary p {
    font-size: 0.95rem !important;
    font-weight: 600 !important;
}
[data-testid="stChatMessage"] p {
    font-size: 0.98rem !important;
    line-height: 1.62 !important;
}

/* Hero banner */
.forge-hero {
    background: linear-gradient(135deg, rgba(15, 23, 42, 0.96) 0%, rgba(30, 41, 59, 0.92) 60%, rgba(124, 45, 18, 0.35) 100%);
    border: 1px solid rgba(249, 115, 22, 0.34);
    border-radius: 14px;
    padding: 1.45rem 1.75rem;
    margin-bottom: 1.4rem;
}
.forge-hero h1 {
    margin: 0;
    font-size: 2.0rem;
    font-weight: 700;
    letter-spacing: -0.025em;
    line-height: 1.2;
    color: #f8fafc;
}
.forge-hero p {
    margin: 0.48rem 0 0 0;
    color: #cbd5e1;
    font-size: 1.02rem;
    line-height: 1.55;
}

/* Agent title banner inside Scorecard Inspector & Chat */
.forge-agent-banner {
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    gap: 0.65rem;
    margin: 0.5rem 0 0.85rem 0;
}
.forge-agent-banner .agent-title {
    font-size: 1.38rem;
    font-weight: 700;
    letter-spacing: -0.02em;
    color: #f8fafc;
}
.forge-agent-banner .agent-domain {
    font-size: 0.95rem;
    font-weight: 500;
    color: #94a3b8;
}

/* Status & gate pills */
.forge-pill {
    display: inline-block;
    padding: 0.28rem 0.78rem;
    border-radius: 999px;
    font-size: 0.82rem;
    font-weight: 600;
    letter-spacing: 0.025em;
    margin-right: 0.4rem;
    margin-bottom: 0.35rem;
}
.pill-green { background: rgba(16, 185, 129, 0.16); color: #34d399; border: 1px solid rgba(16, 185, 129, 0.42); }
.pill-amber { background: rgba(245, 158, 11, 0.16); color: #fbbf24; border: 1px solid rgba(245, 158, 11, 0.42); }
.pill-red { background: rgba(239, 68, 68, 0.16); color: #f87171; border: 1px solid rgba(239, 68, 68, 0.42); }
.pill-blue { background: rgba(59, 130, 246, 0.16); color: #60a5fa; border: 1px solid rgba(59, 130, 246, 0.42); }
.pill-slate { background: rgba(148, 163, 184, 0.14); color: #cbd5e1; border: 1px solid rgba(148, 163, 184, 0.34); }

/* 7-stage horizontal pipeline stepper */
.forge-stepper {
    display: grid;
    grid-template-columns: repeat(7, minmax(0, 1fr));
    gap: 0.55rem;
    margin: 0.85rem 0 1.15rem 0;
}
.forge-step {
    border-radius: 10px;
    padding: 0.7rem 0.55rem;
    text-align: center;
    font-size: 0.86rem;
    font-weight: 600;
    border: 1px solid rgba(148, 163, 184, 0.25);
    background: rgba(15, 23, 42, 0.55);
    color: #94a3b8;
    transition: all 0.2s ease;
}
.forge-step .step-sub {
    display: block;
    font-size: 0.75rem;
    font-weight: 500;
    margin-top: 0.24rem;
    opacity: 0.88;
}
.step-running {
    border-color: #f97316;
    background: rgba(249, 115, 22, 0.16);
    color: #fdba74;
    box-shadow: 0 0 0 1px rgba(249, 115, 22, 0.28);
}
.step-done {
    border-color: #10b981;
    background: rgba(16, 185, 129, 0.14);
    color: #6ee7b7;
}
.step-degraded {
    border-color: #f59e0b;
    background: rgba(245, 158, 11, 0.16);
    color: #fde68a;
}
.step-failed {
    border-color: #ef4444;
    background: rgba(239, 68, 68, 0.16);
    color: #fca5a5;
}

/* Citation & tool chips */
.forge-chip {
    display: inline-block;
    font-family: var(--forge-font-mono);
    font-size: 0.81rem;
    font-weight: 500;
    padding: 0.2rem 0.58rem;
    border-radius: 6px;
    background: rgba(99, 102, 241, 0.16);
    border: 1px solid rgba(99, 102, 241, 0.36);
    color: #c7d2fe;
    margin-right: 0.4rem;
    margin-top: 0.3rem;
}
.forge-agent-card {
    background: rgba(15, 23, 42, 0.52);
    border: 1px solid rgba(148, 163, 184, 0.24);
    border-radius: 12px;
    padding: 1.05rem 1.25rem;
    margin-bottom: 1.1rem;
    line-height: 1.55;
}
.forge-agent-card .card-title {
    font-size: 1.15rem;
    font-weight: 700;
    color: #f8fafc;
}
.forge-agent-card .card-domain {
    font-size: 0.92rem;
    color: #94a3b8;
    margin-left: 0.35rem;
}
.forge-agent-card .card-desc {
    display: block;
    font-size: 0.93rem;
    color: #cbd5e1;
    margin: 0.3rem 0 0.45rem 0;
}
</style>
"""


def status_pill_html(status: str) -> str:
    """Render a color-coded status badge for a run status."""
    s = status.lower().strip()
    cls = {
        "done": "pill-green",
        "completed": "pill-green",
        "pass": "pill-green",
        "running": "pill-blue",
        "queued": "pill-slate",
        "awaiting_approval": "pill-amber",
        "budget_exhausted": "pill-amber",
        "degraded": "pill-amber",
        "failed": "pill-red",
        "fail": "pill-red",
    }.get(s, "pill-slate")
    return f'<span class="forge-pill {cls}">{html.escape(status.upper())}</span>'


def gate_pill_html(label: str, passed: bool) -> str:
    cls = "pill-green" if passed else "pill-red"
    icon = "PASS" if passed else "FAIL"
    return f'<span class="forge-pill {cls}">{html.escape(label)}: {icon}</span>'


def render_stepper_html(stage_states: dict[str, str], stage_notes: dict[str, str] | None = None) -> str:
    """Render the 7-stage horizontal pipeline progress stepper as HTML."""
    notes = stage_notes or {}
    nodes: list[str] = []
    for stage in PIPELINE_STAGES:
        state = stage_states.get(stage, "pending")
        css_cls = {
            "running": "step-running",
            "done": "step-done",
            "degraded": "step-degraded",
            "failed": "step-failed",
        }.get(state, "")
        subtitle = notes.get(stage) or state.upper()
        label = STAGE_LABELS.get(stage, stage)
        nodes.append(
            f'<div class="forge-step {css_cls}">'
            f"{html.escape(label)}"
            f'<span class="step-sub">{html.escape(subtitle)}</span>'
            "</div>"
        )
    return '<div class="forge-stepper">' + "".join(nodes) + "</div>"


def render_chips_html(items: list[str], *, prefix: str = "") -> str:
    """Render a row of monospace citation or tool chips."""
    if not items:
        return ""
    chips = [
        f'<span class="forge-chip">{html.escape(prefix + item)}</span>'
        for item in items
    ]
    return "<div>" + "".join(chips) + "</div>"
