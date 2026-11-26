"""Custom CSS styling and reusable HTML components (badges, 7-stage stepper, citation chips)."""

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
/* Tighten layout rhythm & hero styling */
.block-container {
    padding-top: 1.5rem;
    padding-bottom: 2.5rem;
    max-width: 1240px;
}

.forge-hero {
    background: linear-gradient(135deg, rgba(15, 23, 42, 0.96) 0%, rgba(30, 41, 59, 0.92) 60%, rgba(124, 45, 18, 0.35) 100%);
    border: 1px solid rgba(249, 115, 22, 0.32);
    border-radius: 14px;
    padding: 1.25rem 1.5rem;
    margin-bottom: 1.25rem;
}
.forge-hero h1 {
    margin: 0;
    font-size: 1.7rem;
    letter-spacing: -0.02em;
    color: #f8fafc;
}
.forge-hero p {
    margin: 0.35rem 0 0 0;
    color: #cbd5e1;
    font-size: 0.95rem;
}

/* Status & gate pills */
.forge-pill {
    display: inline-block;
    padding: 0.2rem 0.65rem;
    border-radius: 999px;
    font-size: 0.78rem;
    font-weight: 600;
    letter-spacing: 0.02em;
    margin-right: 0.35rem;
    margin-bottom: 0.25rem;
}
.pill-green { background: rgba(16, 185, 129, 0.16); color: #34d399; border: 1px solid rgba(16, 185, 129, 0.4); }
.pill-amber { background: rgba(245, 158, 11, 0.16); color: #fbbf24; border: 1px solid rgba(245, 158, 11, 0.4); }
.pill-red { background: rgba(239, 68, 68, 0.16); color: #f87171; border: 1px solid rgba(239, 68, 68, 0.4); }
.pill-blue { background: rgba(59, 130, 246, 0.16); color: #60a5fa; border: 1px solid rgba(59, 130, 246, 0.4); }
.pill-slate { background: rgba(148, 163, 184, 0.14); color: #cbd5e1; border: 1px solid rgba(148, 163, 184, 0.32); }

/* 7-stage horizontal pipeline stepper */
.forge-stepper {
    display: grid;
    grid-template-columns: repeat(7, minmax(0, 1fr));
    gap: 0.5rem;
    margin: 0.75rem 0 1rem 0;
}
.forge-step {
    border-radius: 10px;
    padding: 0.6rem 0.5rem;
    text-align: center;
    font-size: 0.78rem;
    font-weight: 600;
    border: 1px solid rgba(148, 163, 184, 0.25);
    background: rgba(15, 23, 42, 0.55);
    color: #94a3b8;
    transition: all 0.2s ease;
}
.forge-step .step-sub {
    display: block;
    font-size: 0.68rem;
    font-weight: 500;
    margin-top: 0.2rem;
    opacity: 0.85;
}
.step-running {
    border-color: #f97316;
    background: rgba(249, 115, 22, 0.16);
    color: #fdba74;
    box-shadow: 0 0 0 1px rgba(249, 115, 22, 0.25);
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
    font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
    font-size: 0.75rem;
    padding: 0.15rem 0.5rem;
    border-radius: 6px;
    background: rgba(99, 102, 241, 0.15);
    border: 1px solid rgba(99, 102, 241, 0.35);
    color: #c7d2fe;
    margin-right: 0.35rem;
    margin-top: 0.25rem;
}
.forge-agent-card {
    background: rgba(15, 23, 42, 0.5);
    border: 1px solid rgba(148, 163, 184, 0.24);
    border-radius: 12px;
    padding: 0.9rem 1.1rem;
    margin-bottom: 1rem;
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
