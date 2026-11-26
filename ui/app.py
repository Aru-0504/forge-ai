"""ForgeAI Control Room — Factory, Human Checkpoint, Scorecard Inspector, Regression & Baseline, Chat Runner."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import streamlit as st

UI_DIR = Path(__file__).resolve().parent
if str(UI_DIR) not in sys.path:
    sys.path.insert(0, str(UI_DIR))

from api_client import (  # noqa: E402
    PRESET_BRIEFS,
    ForgeAPIClient,
    build_artifact_zip_bytes,
    discover_local_demos,
    load_baseline_comparison,
    load_demo_as_run_detail,
    suggest_sample_prompts,
)
from styles import (  # noqa: E402
    CUSTOM_CSS,
    PIPELINE_STAGES,
    gate_pill_html,
    render_chips_html,
    render_stepper_html,
    status_pill_html,
)

DEFAULT_API_URL = os.getenv("FORGEAI_API_BASE_URL", "http://localhost:8000").rstrip("/")
DEFAULT_API_KEY = os.getenv("FORGEAI_API_KEY", "")


def _get_client() -> ForgeAPIClient:
    api_url = st.session_state.get("api_base_url", DEFAULT_API_URL)
    api_key = st.session_state.get("api_key", DEFAULT_API_KEY)
    return ForgeAPIClient(api_url, api_key=api_key)


def _render_hero(title: str, subtitle: str) -> None:
    st.markdown(
        f'<div class="forge-hero"><h1>{title}</h1><p>{subtitle}</p></div>',
        unsafe_allow_html=True,
    )


def _render_approval_editor(client: ForgeAPIClient, detail: dict[str, Any]) -> None:
    """Human Checkpoint editor: review/edit RequirementsDoc and approve to continue generation."""
    run_id = detail["run_id"]
    pending = detail.get("pending_approval") or {}
    req = pending.get("requirements") or {}
    ground = pending.get("groundability") or {}

    st.warning(f"Run `{run_id}` is paused at the **Human Checkpoint** (`awaiting_approval`).")
    if ground.get("missing_gaps"):
        st.info(
            "Groundability guard flagged unsupported gaps (automatically added to must-refuse): "
            + ", ".join(f"`{g}`" for g in ground["missing_gaps"])
        )

    with st.form(f"approve_form_{run_id}"):
        scope = st.text_area("Scope", value=req.get("scope", ""), height=80)
        c1, c2 = st.columns(2)
        with c1:
            audience = st.text_input("Audience", value=req.get("audience", ""))
        with c2:
            tone = st.text_input("Tone", value=req.get("tone", ""))

        must_ans = st.text_area(
            "Must-Answer Topics (one per line)",
            value="\n".join(req.get("must_answer_topics", [])),
            height=90,
        )
        must_ref = st.text_area(
            "Must-Refuse Topics (one per line)",
            value="\n".join(req.get("must_refuse_topics", [])),
            height=90,
        )
        approve_btn = st.form_submit_button(
            "Approve Requirements & Continue Generation",
            type="primary",
            use_container_width=True,
        )

    if approve_btn:
        updated_req = {
            **req,
            "scope": scope.strip(),
            "audience": audience.strip(),
            "tone": tone.strip(),
            "must_answer_topics": [ln.strip() for ln in must_ans.splitlines() if ln.strip()],
            "must_refuse_topics": [ln.strip() for ln in must_ref.splitlines() if ln.strip()],
            "approved_by_human": True,
        }
        code, body = client.approve_run(run_id, updated_req)
        if code >= 400:
            st.error(f"Approval failed ({code}): {body}")
            return
        _stream_run_progress(client, run_id)


def _render_scorecard_view(detail: dict[str, Any]) -> None:
    artifacts = detail.get("artifacts")
    if not artifacts:
        st.info("Artifacts will appear once this run reaches `done` or `budget_exhausted`.")
        return

    run_id = detail["run_id"]
    spec = artifacts["agent_spec"]
    scorecard = artifacts["scorecard"]
    eval_report = artifacts["eval_report"]
    requirements = artifacts.get("requirements", {})
    groundability = artifacts.get("groundability", {})
    degraded = artifacts.get("degraded_stages", [])
    manifest = artifacts.get("manifest", {})

    badges = (
        status_pill_html(detail.get("status", scorecard.get("status", "done")))
        + gate_pill_html("Direct Injection Defense", bool(scorecard.get("safety_passed", True)))
        + gate_pill_html("Doc Injection Defense", bool(scorecard.get("doc_injection_passed", True)))
    )
    st.markdown(badges, unsafe_allow_html=True)

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Agent", spec["name"])
    c2.metric("Pass Rate", f"{scorecard['pass_rate']:.0%}")
    c3.metric("Avg Score", f"{scorecard['average_score']:.2f}")
    c4.metric(
        "Loops",
        f"{scorecard.get('loops_executed', 1)} (best #{scorecard.get('best_iteration', 1)})",
    )
    c5.metric("Total Tokens", f"{scorecard.get('total_tokens', 0):,}")
    c6.metric("Latency", f"{scorecard.get('total_latency_ms', 0.0):.0f} ms")

    if scorecard.get("budget_exhausted_reason"):
        st.warning(f"Budget early stop triggered: {scorecard['budget_exhausted_reason']}")

    if degraded:
        with st.container(border=True):
            st.warning(f"{len(degraded)} stage(s) failed twice and degraded gracefully:")
            for d in degraded:
                st.markdown(
                    f"- **`{d['stage']}`** ({d['attempts']} attempts): `{d['reason']}` → *{d['fallback_used']}*"
                )

    dl1, dl2, dl3, dl4 = st.columns(4)
    zip_bytes = build_artifact_zip_bytes(run_id, artifacts)
    dl1.download_button(
        "Download Full Bundle (.zip)",
        data=zip_bytes,
        file_name=f"{run_id}_artifacts.zip",
        mime="application/zip",
        use_container_width=True,
    )
    dl2.download_button(
        "Download report.md",
        data=artifacts.get("report_markdown") or "",
        file_name=f"{run_id}_report.md",
        mime="text/markdown",
        use_container_width=True,
    )
    dl3.download_button(
        "Download scorecard.json",
        data=json.dumps(scorecard, indent=2),
        file_name=f"{run_id}_scorecard.json",
        mime="application/json",
        use_container_width=True,
    )
    dl4.download_button(
        "Download agent_spec.json",
        data=json.dumps(spec, indent=2),
        file_name=f"{run_id}_agent_spec.json",
        mime="application/json",
        use_container_width=True,
    )

    tab_overview, tab_req, tab_evals, tab_spec, tab_report, tab_repro = st.tabs(
        [
            "Telemetry & Loops",
            "Requirements & Interview",
            "Evaluation Cases",
            "Agent Spec & Contracts",
            "Report (report.md)",
            "Reproducibility",
        ]
    )

    with tab_overview:
        stage_metrics = scorecard.get("stage_metrics", {})
        if stage_metrics:
            rows = [
                {
                    "stage": s,
                    "prompt_tokens": m.get("prompt_tokens", 0),
                    "completion_tokens": m.get("completion_tokens", 0),
                    "total_tokens": m.get("total_tokens", 0),
                    "latency_ms": round(float(m.get("latency_ms", 0.0)), 2),
                    "calls": m.get("calls", 0),
                    "errors": m.get("errors", 0),
                }
                for s, m in stage_metrics.items()
            ]
            ch1, ch2 = st.columns(2)
            with ch1:
                st.caption("Tokens per Stage (Prompt vs Completion)")
                st.bar_chart(rows, x="stage", y=["prompt_tokens", "completion_tokens"])
            with ch2:
                st.caption("Latency per Stage (ms)")
                st.bar_chart(rows, x="stage", y="latency_ms")
            st.dataframe(rows, use_container_width=True, hide_index=True)

        loop_history = scorecard.get("loop_history", [])
        if loop_history:
            st.subheader("Iterative Loop Progression")
            st.dataframe(loop_history, use_container_width=True)

        if groundability:
            st.subheader("Groundability Guard Audit")
            g1, g2, g3 = st.columns(3)
            g1.metric("Groundable", "Yes" if groundability.get("is_groundable") else "No")
            g2.metric("Coverage", f"{groundability.get('coverage_score', 0.0):.0%}")
            g3.metric("Detected Scripts", ", ".join(groundability.get("detected_scripts", ["latin"])))
            for w in groundability.get("warnings", []):
                st.info(w)

    with tab_req:
        if requirements:
            st.markdown(f"**Scope:** {requirements.get('scope', '')}")
            st.markdown(f"**Audience:** {requirements.get('audience', '')}")
            st.markdown(f"**Tone:** {requirements.get('tone', '')}")
            st.markdown(
                f"**Human Approved:** {'Yes' if requirements.get('approved_by_human') else 'Unattended defaults'}"
            )
            r1, r2 = st.columns(2)
            with r1:
                st.markdown("**Must-Answer Topics**")
                for t in requirements.get("must_answer_topics", []):
                    st.markdown(f"- {t}")
            with r2:
                st.markdown("**Must-Refuse Topics (incl. Groundability Gaps)**")
                for t in requirements.get("must_refuse_topics", []):
                    st.markdown(f"- {t}")
            qa = requirements.get("clarifying_qa", [])
            if qa:
                st.subheader("Intake Interviewer Q&A (≤ 5 Questions)")
                st.dataframe(qa, use_container_width=True, hide_index=True)
        else:
            st.info("No structured requirements document stored for this run.")

    with tab_evals:
        st.caption(eval_report.get("summary", ""))
        for res in eval_report.get("results", []):
            icon = "✅" if res["passed"] else "❌"
            header = (
                f"{icon} `{res['case_id']}` · **{res['category']}** · "
                f"score `{res['score']:.2f}` · tools: `{', '.join(res.get('tools_used', [])) or 'none'}`"
            )
            with st.expander(header, expanded=not res["passed"]):
                st.markdown(f"**Question:** {res['question']}")
                st.markdown(f"**Answer:** {res['answer']}")
                if res.get("citations"):
                    st.markdown(
                        render_chips_html(res["citations"], prefix="cite: "),
                        unsafe_allow_html=True,
                    )
                st.json(
                    {
                        "checks": res.get("checks", {}),
                        "notes": res.get("notes", ""),
                        "latency_ms": res.get("latency_ms", 0.0),
                    }
                )

    with tab_spec:
        st.markdown(f"**Domain:** {spec['domain']}")
        st.markdown(f"**Persona:** {spec['persona']}")
        st.markdown("**Enabled Tools:**")
        st.markdown(render_chips_html(spec.get("enabled_tools", [])), unsafe_allow_html=True)
        sc1, sc2 = st.columns(2)
        with sc1:
            st.markdown("**Declarative Retrieval Config**")
            st.json(spec.get("retrieval_config", {}))
        with sc2:
            st.markdown("**Declarative Output Contract**")
            st.json(spec.get("output_contract", {}))
        st.markdown("**Compiled System Prompt:**")
        st.code(spec["system_prompt"], language="markdown")

    with tab_report:
        st.markdown(artifacts.get("report_markdown") or "_No report generated._")

    with tab_repro:
        st.json(
            {
                "run_id": run_id,
                "schema_version": manifest.get("schema_version"),
                "provider": manifest.get("provider"),
                "model": manifest.get("model"),
                "model_version": manifest.get("model_version"),
                "seed": manifest.get("seed"),
                "config_hash": manifest.get("config_hash"),
                "prompt_versions": manifest.get("prompt_versions", {}),
            }
        )


def _stream_run_progress(client: ForgeAPIClient, run_id: str) -> None:
    st.info(f"Streaming live SSE events for run `{run_id}`...")
    stepper_slot = st.empty()
    progress_bar = st.progress(0.0)
    log_slot = st.empty()

    stage_states: dict[str, str] = {s: "pending" for s in PIPELINE_STAGES}
    stage_notes: dict[str, str] = {}
    events_log: list[str] = []

    stepper_slot.markdown(render_stepper_html(stage_states, stage_notes), unsafe_allow_html=True)

    for payload in client.stream_events(run_id):
        stage = str(payload.get("stage", "pipeline"))
        etype = str(payload.get("event_type", ""))
        msg = str(payload.get("message", ""))
        prog = min(1.0, max(0.0, float(payload.get("progress", 0.0))))

        if stage in stage_states:
            if etype == "stage_started":
                stage_states[stage] = "running"
                loop_num = payload.get("data", {}).get("iteration")
                stage_notes[stage] = f"loop {loop_num}" if loop_num else "running"
            elif etype == "stage_completed":
                stage_states[stage] = "done"
                lat = payload.get("data", {}).get("latency_ms")
                stage_notes[stage] = f"{lat:.0f} ms" if isinstance(lat, (int, float)) else "done"
            elif etype == "stage_degraded":
                stage_states[stage] = "degraded"
                stage_notes[stage] = "degraded"
            elif etype == "awaiting_approval":
                stage_states[stage] = "degraded"
                stage_notes[stage] = "approval"

        if etype == "run_failed" and stage in stage_states:
            stage_states[stage] = "failed"

        progress_bar.progress(prog)
        stepper_slot.markdown(render_stepper_html(stage_states, stage_notes), unsafe_allow_html=True)
        events_log.append(f"[{prog:4.0%}] ({stage}) {msg}")
        log_slot.code("\n".join(events_log[-18:]), language="text")

        if etype in {"run_completed", "run_failed", "awaiting_approval"}:
            break

    detail = client.get_run(run_id)
    if detail.get("status") == "awaiting_approval":
        _render_approval_editor(client, detail)
    elif detail.get("status") in {"done", "budget_exhausted"}:
        st.success(f"Run `{run_id}` finished with status `{detail['status']}`!")
        _render_scorecard_view(detail)
    else:
        st.error(f"Run `{run_id}` failed: {detail.get('error') or 'Unknown error'}")


def render_factory_page(client: ForgeAPIClient, api_online: bool) -> None:
    _render_hero(
        "⚒️ ForgeAI Agent Factory",
        "Upload domain documents, define your brief, optionally pause at the Human Checkpoint to edit requirements, and watch the 7-stage iterative pipeline run live.",
    )

    pcol1, pcol2 = st.columns([3, 1])
    with pcol1:
        preset_choice = st.selectbox(
            "Quick-Load Demo Preset (optional)",
            ["Custom Upload", *PRESET_BRIEFS.keys()],
        )
    with pcol2:
        st.write("")
        st.write("")
        load_preset = st.button("Apply Preset", use_container_width=True)

    if load_preset and preset_choice in PRESET_BRIEFS:
        preset = PRESET_BRIEFS[preset_choice]
        st.session_state["factory_brief"] = preset["brief"]
        st.session_state["factory_name"] = preset["agent_name_hint"]
        st.session_state["factory_preset_files"] = [
            str(p) for p in preset["source_paths"] if p.exists()
        ]
        st.toast(f"Loaded preset: {preset_choice}")
    elif preset_choice == "Custom Upload" and load_preset:
        st.session_state["factory_preset_files"] = []

    preset_paths = [
        Path(p) for p in st.session_state.get("factory_preset_files", []) if Path(p).exists()
    ]

    with st.form("forge_form", clear_on_submit=False):
        brief = st.text_area(
            "Agent Brief",
            value=st.session_state.get("factory_brief", ""),
            placeholder="Describe what the forged agent should do, what calculations/lookups it needs, and its escalation rules...",
            height=95,
        )
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            agent_name_hint = st.text_input(
                "Agent Name Hint (optional)",
                value=st.session_state.get("factory_name", ""),
                placeholder="e.g. HR Policy Advisor",
            )
        with c2:
            max_loops = st.slider("Max Improvement Loops", min_value=1, max_value=3, value=3)
        with c3:
            redact_pii = st.checkbox("Redact PII on ingest", value=True)
        with c4:
            require_human_approval = st.checkbox(
                "Human Approval Checkpoint",
                value=False,
                help="Pause after intake & groundability so you can review/edit requirements before generation.",
            )

        uploaded_files = st.file_uploader(
            "Domain Documents (.md, .txt, .csv, .json, .yaml, .pdf, .docx, .html)",
            accept_multiple_files=True,
        )
        if preset_paths and not uploaded_files:
            st.caption(
                "Using preset source files: " + ", ".join(f"`{p.name}`" for p in preset_paths)
            )

        submitted = st.form_submit_button(
            "Launch Forge Run", type="primary", use_container_width=True
        )

    if submitted:
        if len(brief.strip()) < 5:
            st.error("Please enter a brief of at least 5 characters.")
            return
        if not api_online:
            st.error(
                f"API at `{client.base_url}` is offline. Start it with `make run` or `docker compose up -d`."
            )
            return

        files_to_send: list[tuple[str, bytes, str]] = []
        if uploaded_files:
            files_to_send = [
                (f.name, f.getvalue(), f.type or "application/octet-stream")
                for f in uploaded_files
            ]
        elif preset_paths:
            files_to_send = [(p.name, p.read_bytes(), "text/plain") for p in preset_paths]

        if not files_to_send:
            st.error("Upload at least one document or apply a demo preset first.")
            return

        code, body = client.create_run_upload(
            brief=brief,
            files=files_to_send,
            agent_name_hint=agent_name_hint,
            max_loops=max_loops,
            redact_pii=redact_pii,
            require_human_approval=require_human_approval,
        )
        if code >= 400:
            st.error(f"Run rejected ({code}): {body.get('detail', body)}")
            return

        _stream_run_progress(client, body["run_id"])

    st.divider()
    st.subheader("Recent Background Runs")
    if not api_online:
        st.info(
            "API is offline — switch to **Scorecard Inspector**, **Regression & Baseline**, or **Chat Runner** to explore committed demos."
        )
        return

    try:
        runs = client.list_runs(limit=30)
    except Exception as exc:
        st.warning(f"Failed to list runs: {exc}")
        return

    if not runs:
        st.info("No API runs yet. Apply a demo preset above and click **Launch Forge Run**.")
        return

    # Show inline Human Checkpoint editors for any paused runs
    awaiting = [r for r in runs if r["status"] == "awaiting_approval"]
    for paused in awaiting:
        detail = client.get_run(paused["run_id"])
        _render_approval_editor(client, detail)

    st.dataframe(
        [
            {
                "run_id": r["run_id"],
                "status": r["status"],
                "agent_name": r.get("agent_name") or "(pending)",
                "domain": r.get("domain", ""),
                "pass_rate": f"{r['eval_pass_rate']:.0%}"
                if r.get("eval_pass_rate") is not None
                else "—",
                "model": f"{r.get('provider', '')}:{r.get('model', '')}",
                "created_at": r.get("created_at", "")[:19].replace("T", " "),
            }
            for r in runs
        ],
        use_container_width=True,
        hide_index=True,
    )

    resumable = [
        r["run_id"] for r in runs if r["status"] in {"failed", "budget_exhausted", "running"}
    ]
    if resumable:
        rcol1, rcol2 = st.columns([3, 1])
        with rcol1:
            to_resume = st.selectbox(
                "Resume interrupted or failed run from checkpoints", resumable
            )
        with rcol2:
            st.write("")
            st.write("")
            if st.button("Resume Run", use_container_width=True):
                code, body = client.resume_run(to_resume)
                if code < 400:
                    _stream_run_progress(client, to_resume)
                else:
                    st.error(f"Resume failed ({code}): {body}")


def render_inspector_page(client: ForgeAPIClient, api_online: bool) -> None:
    _render_hero(
        "📊 Scorecard & Artifact Inspector",
        "Inspect per-stage telemetry, structured requirements & interview Q&A, declarative retrieval/output contracts, and downloadable artifact bundles.",
    )

    choices: dict[str, tuple[str, Any]] = {}
    if api_online:
        try:
            for r in client.list_runs(limit=50):
                label = f"[API] {r.get('agent_name') or r['run_id']} · {r['status']} ({r['run_id']})"
                choices[label] = ("api", r["run_id"])
        except Exception:
            pass

    for name, path in discover_local_demos().items():
        choices[f"[Demo] {name}"] = ("demo", path)

    if not choices:
        st.info("No runs or committed demos found.")
        return

    selected = st.selectbox("Select Run or Demo to Inspect", list(choices.keys()))
    kind, target = choices[selected]

    if kind == "api":
        detail = client.get_run(str(target))
    else:
        detail = load_demo_as_run_detail(Path(target))

    if detail.get("status") == "awaiting_approval" and kind == "api":
        _render_approval_editor(client, detail)
    else:
        _render_scorecard_view(detail)


def render_regression_and_baseline_page(client: ForgeAPIClient, api_online: bool) -> None:
    _render_hero(
        "🔁 Regression Mode & Factory vs. Human Baseline",
        "Re-run historical eval sets when documents change to catch regressions, and compare the factory-forged agent head-to-head against a hand-written human baseline.",
    )

    tab_reg, tab_base = st.tabs(["Regression Mode (Docs Changed)", "Factory vs. Human Baseline"])

    with tab_reg:
        st.caption(
            "Select a baseline forged agent, upload updated/edited documents, regenerate the agent, "
            "and re-run the **old** eval set to see exactly what broke or improved."
        )
        choices: dict[str, tuple[str, Any]] = {}
        if api_online:
            try:
                for r in client.list_runs(limit=50):
                    if r.get("status") in {"done", "budget_exhausted"}:
                        choices[f"[API] {r.get('agent_name') or r['run_id']} ({r['run_id']})"] = (
                            "api",
                            r["run_id"],
                        )
            except Exception:
                pass
        for name, path in discover_local_demos().items():
            choices[f"[Demo] {name}"] = ("demo", path)

        if not choices:
            st.info("No baseline agents found.")
        else:
            sel = st.selectbox("Baseline Agent", list(choices.keys()))
            mode, target = choices[sel]
            updated_files = st.file_uploader(
                "Upload Updated Document(s)",
                accept_multiple_files=True,
                key="regression_uploader",
            )
            if st.button("Run Regression Check", type="primary"):
                if not updated_files:
                    st.error("Upload at least one updated document.")
                else:
                    files_payload = [
                        (f.name, f.getvalue(), f.type or "text/plain") for f in updated_files
                    ]
                    if mode == "api":
                        code, report = client.regress_run(str(target), files_payload)
                        if code >= 400:
                            st.error(f"Regression failed ({code}): {report}")
                            report = None
                    else:
                        from forgeai.config import get_settings
                        from forgeai.core.regression import run_regression
                        from forgeai.core.schemas import ForgeArtifact, SourceDocument
                        from forgeai.llm.base import create_llm_client

                        cfg = get_settings()
                        base_art = ForgeArtifact.load_from_dir(Path(target))
                        docs = [SourceDocument(filename=n, content=b) for n, b, _ in files_payload]
                        _, rep = run_regression(
                            baseline_artifact=base_art,
                            updated_sources=docs,
                            llm=create_llm_client(cfg),
                            config_hash=cfg.config_hash(),
                        )
                        report = rep.model_dump()

                    if report:
                        if report["has_regressions"]:
                            st.error(report["summary"])
                        else:
                            st.success(report["summary"])
                        r1, r2, r3, r4 = st.columns(4)
                        r1.metric("Old Pass Rate", f"{report['old_pass_rate']:.0%}")
                        r2.metric("New Pass Rate", f"{report['new_pass_rate']:.0%}")
                        r3.metric("Regressed", report["regressed_count"])
                        r4.metric("Fixed", report["fixed_count"])
                        st.dataframe(report["diffs"], use_container_width=True, hide_index=True)
                        st.download_button(
                            "Download regression_report.md",
                            data=report.get("report_markdown", ""),
                            file_name="regression_report.md",
                            mime="text/markdown",
                        )

    with tab_base:
        comp = load_baseline_comparison()
        if not comp:
            st.info(
                "No committed comparison report found yet. Run `python demos/run_demos.py` to generate `demos/baseline_comparison/`."
            )
        else:
            b1, b2, b3, b4 = st.columns(4)
            b1.metric("Factory Pass Rate", f"{comp['factory_pass_rate']:.0%}")
            b2.metric("Human Baseline Pass Rate", f"{comp['baseline_pass_rate']:.0%}")
            b3.metric("Factory Avg Score", f"{comp['factory_avg_score']:.2f}")
            b4.metric("Baseline Avg Score", f"{comp['baseline_avg_score']:.2f}")

            w1, w2 = st.columns(2)
            with w1:
                st.subheader("Where Factory Wins")
                for line in comp.get("where_factory_wins", []):
                    st.markdown(f"- {line}")
            with w2:
                st.subheader("Where Human Baseline Wins")
                for line in comp.get("where_baseline_wins", []):
                    st.markdown(f"- {line}")

            st.subheader("Head-to-Head Case Table")
            st.dataframe(comp.get("cases", []), use_container_width=True, hide_index=True)
            with st.expander("Full comparison_report.md"):
                st.markdown(comp.get("report_markdown", ""))


def render_chat_page(client: ForgeAPIClient, api_online: bool) -> None:
    _render_hero(
        "💬 Forged Agent Chat Runner",
        "Test forged agents interactively with live tool-call traces, inline source citations, and prompt-injection defense checks.",
    )

    choices: dict[str, tuple[str, Any]] = {}
    if api_online:
        try:
            for r in client.list_runs(limit=50):
                if r.get("status") in {"done", "budget_exhausted"}:
                    label = f"[API] {r.get('agent_name') or r['run_id']} ({r['run_id']})"
                    choices[label] = ("api", r["run_id"])
        except Exception:
            pass

    for name, path in discover_local_demos().items():
        choices[f"[Demo] {name}"] = ("demo", path)

    if not choices:
        st.info("No forged agents available yet. Run the Agent Factory first.")
        return

    top1, top2 = st.columns([4, 1])
    with top1:
        selected_label = st.selectbox("Active Forged Agent", list(choices.keys()))
    mode, target = choices[selected_label]

    history_key = f"chat_history::{selected_label}"
    session_key = f"session_id::{selected_label}"
    if history_key not in st.session_state:
        st.session_state[history_key] = []

    with top2:
        st.write("")
        st.write("")
        if st.button("New Session", use_container_width=True):
            st.session_state[history_key] = []
            st.session_state.pop(session_key, None)
            st.rerun()

    if mode == "api":
        detail = client.get_run(str(target))
    else:
        detail = load_demo_as_run_detail(Path(target))

    spec = (detail.get("artifacts") or {}).get("agent_spec", {})
    domain = spec.get("domain", detail.get("domain", ""))
    tools = spec.get("enabled_tools", [])
    st.markdown(
        f'<div class="forge-agent-card">'
        f"<strong>{spec.get('name', selected_label)}</strong> &nbsp;·&nbsp; {domain}<br/>"
        f"<small>{spec.get('description', '')}</small>"
        f"{render_chips_html(tools, prefix='tool: ')}"
        f"</div>",
        unsafe_allow_html=True,
    )

    samples = suggest_sample_prompts(domain, tools)
    st.caption("Quick-try prompts (click to send):")
    btn_cols = st.columns(len(samples))
    queued_prompt: str | None = None
    for idx, (col, sample_q) in enumerate(zip(btn_cols, samples, strict=False)):
        short_label = "🛡️ Test Injection" if "ignore" in sample_q.lower() else f"Try Q{idx + 1}"
        if col.button(
            short_label,
            key=f"sample_{selected_label}_{idx}",
            help=sample_q,
            use_container_width=True,
        ):
            queued_prompt = sample_q

    for msg in st.session_state[history_key]:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            if msg.get("blocked_injection"):
                st.markdown(status_pill_html("INJECTION BLOCKED"), unsafe_allow_html=True)
            if msg.get("citations"):
                st.markdown(
                    render_chips_html(msg["citations"], prefix="cite: "),
                    unsafe_allow_html=True,
                )
            if msg.get("tool_traces"):
                with st.expander(
                    f"Tool Calls ({len(msg['tool_traces'])}) · {msg.get('latency_ms', 0.0):.0f} ms"
                ):
                    st.json(msg["tool_traces"])

    prompt = st.chat_input("Ask your forged agent a question...") or queued_prompt
    if not prompt:
        return

    st.session_state[history_key].append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        if mode == "api":
            payload = client.chat(
                str(target),
                prompt,
                session_id=st.session_state.get(session_key),
            )
            st.session_state[session_key] = payload.get("session_id")
            answer = payload.get("answer", "")
            citations = payload.get("citations", [])
            traces = payload.get("tool_traces", [])
            blocked = bool(payload.get("blocked_injection", False))
            latency_ms = float(payload.get("latency_ms", 0.0))
        else:
            from forgeai.config import get_settings
            from forgeai.core.runtime.agent import ForgedAgentRuntime
            from forgeai.core.schemas import ChatMessage, ForgeArtifact
            from forgeai.llm.base import create_llm_client

            cfg = get_settings()
            artifact = ForgeArtifact.load_from_dir(Path(target))
            runtime = ForgedAgentRuntime.from_artifact(
                artifact,
                create_llm_client(cfg),
                allowed_http_hosts=cfg.allowed_http_hosts_list,
            )
            prior = [
                ChatMessage(role=m["role"], content=m["content"])
                for m in st.session_state[history_key][:-1]
            ]
            res = runtime.answer(prompt, history=prior)
            answer = res.answer
            citations = res.citations
            traces = [t.model_dump() for t in res.tool_traces]
            blocked = res.blocked_injection
            latency_ms = res.latency_ms

        st.markdown(answer)
        if blocked:
            st.markdown(status_pill_html("INJECTION BLOCKED"), unsafe_allow_html=True)
        if citations:
            st.markdown(render_chips_html(citations, prefix="cite: "), unsafe_allow_html=True)
        if traces:
            with st.expander(f"Tool Calls ({len(traces)}) · {latency_ms:.0f} ms"):
                st.json(traces)

    st.session_state[history_key].append(
        {
            "role": "assistant",
            "content": answer,
            "citations": citations,
            "tool_traces": traces,
            "blocked_injection": blocked,
            "latency_ms": latency_ms,
        }
    )


def main() -> None:
    st.set_page_config(
        page_title="ForgeAI — AI Agent Factory",
        page_icon="⚒️",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    st.markdown(CUSTOM_CSS, unsafe_allow_html=True)

    with st.sidebar:
        st.title("⚒️ ForgeAI")
        st.caption("AI Agent Factory & Chat Runner")

        page = st.radio(
            "Workspace",
            [
                "⚒️ Agent Factory",
                "📊 Scorecard Inspector",
                "🔁 Regression & Baseline",
                "💬 Chat Runner",
            ],
            label_visibility="collapsed",
        )

        st.divider()
        with st.expander("Connection Settings", expanded=False):
            st.text_input(
                "FastAPI Base URL",
                value=st.session_state.get("api_base_url", DEFAULT_API_URL),
                key="api_base_url",
            )
            st.text_input(
                "X-API-Key (optional)",
                value=st.session_state.get("api_key", DEFAULT_API_KEY),
                type="password",
                key="api_key",
            )

        client = _get_client()
        health = client.healthz()
        api_online = health is not None

        if health:
            st.success(f"API Online · v{health.get('version', '0.1.0')}")
            st.caption(
                f"Backend: `{health.get('provider')}:{health.get('model')}`\n\n"
                f"Config: `{health.get('config_hash', '')}`"
            )
            metrics = client.metrics().get("counters", {})
            if metrics:
                m1, m2 = st.columns(2)
                m1.metric("Runs Done", metrics.get("forge_runs_done", 0))
                m2.metric("Chat Turns", metrics.get("agent_chat_turns", 0))
        else:
            st.warning("API Offline — Demo Mode")
            st.caption("Start API with `make run` or `docker compose up -d`.")

    if page == "⚒️ Agent Factory":
        render_factory_page(client, api_online)
    elif page == "📊 Scorecard Inspector":
        render_inspector_page(client, api_online)
    elif page == "🔁 Regression & Baseline":
        render_regression_and_baseline_page(client, api_online)
    else:
        render_chat_page(client, api_online)


if __name__ == "__main__":
    main()
