"""ForgeAI thin Streamlit UI: Factory Page (POST /runs + live SSE + scorecard) + Chat Page (POST /agents/{id}/chat)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx
import streamlit as st

API_BASE_URL = os.getenv("FORGEAI_API_BASE_URL", "http://localhost:8000").rstrip("/")
API_KEY = os.getenv("FORGEAI_API_KEY", "")
DEMOS_DIR = Path(__file__).resolve().parent.parent / "demos"


def _headers() -> dict[str, str]:
    return {"X-API-Key": API_KEY} if API_KEY else {}


def _api_health() -> dict[str, Any] | None:
    try:
        resp = httpx.get(f"{API_BASE_URL}/healthz", timeout=2.0)
        return resp.json() if resp.status_code == 200 else None
    except Exception:
        return None


def _discover_local_demos() -> dict[str, Path]:
    found: dict[str, Path] = {}
    if DEMOS_DIR.exists():
        for child in sorted(DEMOS_DIR.iterdir()):
            if child.is_dir() and (child / "manifest.json").exists():
                found[child.name] = child
    return found


def render_factory_page(api_online: bool) -> None:
    st.header("Agent Factory")
    st.caption(
        "Submit a brief and domain documents to run the 7-stage iterative factory "
        "(intake → groundability → evalgen → generator/judge/critic → report)."
    )

    with st.form("forge_form", clear_on_submit=False):
        brief = st.text_area(
            "Agent Brief",
            placeholder="e.g. Answer employee HR policy questions with exact handbook citations and compute meal per-diem totals.",
            height=90,
        )
        col1, col2, col3 = st.columns(3)
        with col1:
            agent_name_hint = st.text_input("Agent Name Hint (optional)", placeholder="HR Policy Advisor")
        with col2:
            max_loops = st.slider("Max Improvement Loops", min_value=1, max_value=3, value=3)
        with col3:
            redact_pii = st.checkbox("Redact PII on ingest", value=True)

        uploaded_files = st.file_uploader(
            "Domain Knowledge Sources (.md, .txt, .csv, .json, .yaml, .pdf, .docx, .html)",
            accept_multiple_files=True,
        )
        submitted = st.form_submit_button("Start Forge Run", type="primary")

    if submitted:
        if len(brief.strip()) < 5:
            st.error("Please enter an agent brief (at least 5 characters).")
            return
        if not uploaded_files:
            st.error("Please upload at least one source document.")
            return
        if not api_online:
            st.error(
                f"ForgeAI API at `{API_BASE_URL}` is offline. Start it with `make run` or `forgeai serve`."
            )
            return

        _run_forge_via_api(brief, agent_name_hint, max_loops, redact_pii, uploaded_files)

    st.divider()
    st.subheader("Runs & Committed Demo Artifacts")

    if api_online:
        try:
            resp = httpx.get(f"{API_BASE_URL}/runs", headers=_headers(), timeout=5.0)
            runs = resp.json().get("runs", [])
            if runs:
                st.dataframe(
                    [
                        {
                            "run_id": r["run_id"],
                            "status": r["status"],
                            "agent_name": r.get("agent_name", ""),
                            "domain": r.get("domain", ""),
                            "pass_rate": r.get("eval_pass_rate"),
                            "model": f"{r.get('provider', '')}:{r.get('model', '')}",
                            "created_at": r.get("created_at", ""),
                        }
                        for r in runs
                    ],
                    use_container_width=True,
                )
                run_ids = [r["run_id"] for r in runs]
                selected_run = st.selectbox("Inspect API Run", run_ids)
                if selected_run:
                    detail = httpx.get(
                        f"{API_BASE_URL}/runs/{selected_run}", headers=_headers(), timeout=5.0
                    ).json()
                    if detail.get("artifacts"):
                        _render_artifacts_bundle(detail["artifacts"])
                    elif detail.get("error"):
                        st.error(f"Run failed: {detail['error']}")
            else:
                st.info("No API runs yet. Submit a brief above to queue a background forge job.")
        except Exception as exc:
            st.warning(f"Could not fetch runs from API: {exc}")

    demos = _discover_local_demos()
    if demos:
        selected_demo = st.selectbox("Inspect committed demo artifact", list(demos.keys()))
        if selected_demo:
            from forgeai.core.schemas import ForgeArtifact

            art = ForgeArtifact.load_from_dir(demos[selected_demo])
            _render_artifacts_bundle(
                {
                    "agent_spec": art.agent_spec.model_dump(),
                    "eval_report": art.eval_report.model_dump(),
                    "scorecard": art.scorecard.model_dump(),
                    "report_markdown": art.report_markdown,
                }
            )


def _run_forge_via_api(
    brief: str,
    agent_name_hint: str,
    max_loops: int,
    redact_pii: bool,
    uploaded_files: list[Any],
) -> None:
    files_payload = [
        ("files", (f.name, f.getvalue(), f.type or "application/octet-stream"))
        for f in uploaded_files
    ]
    data = {
        "brief": brief.strip(),
        "agent_name_hint": agent_name_hint.strip(),
        "max_loops": str(max_loops),
        "redact_pii": str(redact_pii).lower(),
    }
    resp = httpx.post(
        f"{API_BASE_URL}/runs",
        data=data,
        files=files_payload,
        headers=_headers(),
        timeout=30.0,
    )
    if resp.status_code >= 400:
        st.error(f"API rejected forge job ({resp.status_code}): {resp.text}")
        return

    run_id = resp.json()["run_id"]
    st.info(f"Queued run `{run_id}`. Streaming live SSE progress...")
    progress_bar = st.progress(0.0)
    log_box = st.empty()
    events_log: list[str] = []

    with httpx.stream(
        "GET",
        f"{API_BASE_URL}/runs/{run_id}/events",
        headers=_headers(),
        timeout=300.0,
    ) as stream:
        for line in stream.iter_lines():
            if line.startswith("data:"):
                payload = json.loads(line[len("data:") :].strip())
                progress_bar.progress(min(1.0, float(payload.get("progress", 0.0))))
                events_log.append(f"[{payload['stage']}] {payload['message']}")
                log_box.code("\n".join(events_log))
                if payload.get("event_type") in {"run_completed", "run_failed"}:
                    break

    detail = httpx.get(f"{API_BASE_URL}/runs/{run_id}", headers=_headers(), timeout=15.0).json()
    if detail.get("artifacts"):
        st.success(f"Run `{run_id}` finished with status `{detail['status']}`!")
        _render_artifacts_bundle(detail["artifacts"])
    elif detail.get("error"):
        st.error(f"Run `{run_id}` failed: {detail['error']}")


def _render_artifacts_bundle(bundle: dict[str, Any]) -> None:
    spec = bundle["agent_spec"]
    scorecard = bundle["scorecard"]
    report = bundle["eval_report"]

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Agent", spec["name"])
    c2.metric("Status", scorecard.get("status", "done"))
    c3.metric("Pass Rate", f"{scorecard['pass_rate']:.0%}")
    c4.metric("Loops", scorecard.get("loops_executed", 1))
    c5.metric("Total Tokens", scorecard.get("total_tokens", 0))

    with st.expander("Run Report (report.md)", expanded=False):
        st.markdown(bundle.get("report_markdown") or "(no report markdown)")

    with st.expander("Agent Spec & System Prompt", expanded=False):
        st.markdown(f"**Domain:** {spec['domain']}")
        st.markdown(f"**Tools:** `{', '.join(spec['enabled_tools'])}`")
        st.code(spec["system_prompt"], language="markdown")

    with st.expander("Evaluation Scorecard & Per-Stage Metrics", expanded=False):
        st.dataframe(
            [
                {
                    "case_id": r["case_id"],
                    "category": r["category"],
                    "passed": r["passed"],
                    "score": r["score"],
                    "tools_used": ", ".join(r.get("tools_used", [])),
                    "notes": r.get("notes", ""),
                }
                for r in report.get("results", [])
            ],
            use_container_width=True,
        )
        if scorecard.get("stage_metrics"):
            st.json(scorecard["stage_metrics"])


def render_chat_page(api_online: bool) -> None:
    st.header("Chat Runner")

    choices: dict[str, tuple[str, str]] = {}
    if api_online:
        try:
            resp = httpx.get(f"{API_BASE_URL}/runs", headers=_headers(), timeout=5.0)
            for run in resp.json().get("runs", []):
                if run.get("status") in {"done", "budget_exhausted"}:
                    label = f"[API] {run.get('agent_name') or run['run_id']} ({run['run_id']})"
                    choices[label] = ("api", run["run_id"])
        except Exception:
            pass

    for name, path in _discover_local_demos().items():
        choices[f"[Demo] {name}"] = ("local", str(path))

    if not choices:
        st.info("No forged agents available yet. Run the Factory page first.")
        return

    selected_label = st.selectbox("Select Forged Agent", list(choices.keys()))
    mode, target = choices[selected_label]

    history_key = f"chat_history::{selected_label}"
    if history_key not in st.session_state:
        st.session_state[history_key] = []

    for msg in st.session_state[history_key]:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            if msg.get("citations"):
                st.caption("Citations: " + ", ".join(f"`{c}`" for c in msg["citations"]))
            if msg.get("tool_traces"):
                with st.expander("Tool Calls"):
                    st.json(msg["tool_traces"])

    prompt = st.chat_input("Ask your forged agent a question...")
    if not prompt:
        return

    st.session_state[history_key].append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        if mode == "api":
            session_key = f"session_id::{target}"
            resp = httpx.post(
                f"{API_BASE_URL}/agents/{target}/chat",
                json={"message": prompt, "session_id": st.session_state.get(session_key)},
                headers=_headers(),
                timeout=60.0,
            )
            payload = resp.json()
            st.session_state[session_key] = payload.get("session_id")
            answer = payload.get("answer", "")
            citations = payload.get("citations", [])
            traces = payload.get("tool_traces", [])
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

        st.markdown(answer)
        if citations:
            st.caption("Citations: " + ", ".join(f"`{c}`" for c in citations))
        if traces:
            with st.expander("Tool Calls"):
                st.json(traces)

    st.session_state[history_key].append(
        {"role": "assistant", "content": answer, "citations": citations, "tool_traces": traces}
    )


def main() -> None:
    st.set_page_config(page_title="ForgeAI — AI Agent Factory", page_icon="⚒️", layout="wide")
    health = _api_health()
    api_online = health is not None

    with st.sidebar:
        st.title("⚒️ ForgeAI")
        st.caption("AI Agent Factory & Chat Runner")
        if health:
            st.success(f"API Online ({health.get('provider')}:{health.get('model')})")
        else:
            st.warning("API Offline — Demo Inspection Mode")
        page = st.radio("Navigation", ["Factory", "Chat Runner"])

    if page == "Factory":
        render_factory_page(api_online)
    else:
        render_chat_page(api_online)


if __name__ == "__main__":
    main()
