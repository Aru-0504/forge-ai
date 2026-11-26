"""Unit tests for the ForgeAI Streamlit UI helpers, API client, ZIP bundler, and headless render."""

from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

import httpx
import pytest

UI_DIR = Path(__file__).resolve().parents[2] / "ui"
if str(UI_DIR) not in sys.path:
    sys.path.insert(0, str(UI_DIR))

from api_client import (  # noqa: E402
    ForgeAPIClient,
    build_artifact_zip_bytes,
    discover_local_demos,
    load_demo_as_run_detail,
    suggest_sample_prompts,
)
from styles import (  # noqa: E402
    PIPELINE_STAGES,
    gate_pill_html,
    render_chips_html,
    render_stepper_html,
    status_pill_html,
)


def test_styles_render_stepper_pills_and_chips_safely() -> None:
    stepper = render_stepper_html(
        {"intake": "done", "groundability": "degraded", "generator": "running"},
        {"intake": "12 ms", "generator": "loop 1"},
    )
    assert "step-done" in stepper
    assert "step-degraded" in stepper
    assert "step-running" in stepper
    assert len(PIPELINE_STAGES) == 7

    # HTML escaping prevents XSS in badges/chips
    pill = status_pill_html("<script>alert(1)</script>")
    assert "<script>" not in pill
    assert "&lt;SCRIPT&gt;" in pill

    gate = gate_pill_html("Doc Injection", True)
    assert "PASS" in gate

    chips = render_chips_html(["hr_policy.md#c001", "calc"], prefix="cite: ")
    assert "cite: hr_policy.md#c001" in chips


def test_api_client_healthz_runs_and_chat_with_mock_transport() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/healthz":
            return httpx.Response(200, json={"status": "ok", "provider": "stub", "model": "m1"})
        if req.url.path == "/runs" and req.method == "GET":
            return httpx.Response(200, json={"runs": [{"run_id": "r1", "status": "done"}]})
        if req.url.path == "/agents/r1/chat" and req.method == "POST":
            assert req.headers.get("X-API-Key") == "secret"
            return httpx.Response(
                200,
                json={"session_id": "s1", "answer": "20 PTO days", "citations": ["hr.md#c001"], "tool_traces": []},
            )
        return httpx.Response(404, json={"detail": "not found"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as mock_http:
        client = ForgeAPIClient("http://mock-api", api_key="secret", http_client=mock_http)
        assert client.healthz()["status"] == "ok"
        assert client.list_runs()[0]["run_id"] == "r1"
        chat_res = client.chat("r1", "PTO days?")
        assert chat_res["answer"] == "20 PTO days"


def test_demo_loader_zip_bundle_and_sample_prompts() -> None:
    demos = discover_local_demos()
    assert "run1_hr_policy" in demos
    assert "run2_ship_spares" in demos

    detail = load_demo_as_run_detail(demos["run2_ship_spares"])
    assert detail["status"] == "done"
    assert detail["artifacts"]["scorecard"]["pass_rate"] == 1.0

    zip_bytes = build_artifact_zip_bytes(detail["run_id"], detail["artifacts"])
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = set(zf.namelist())
        assert f"{detail['run_id']}/report.md" in names
        assert f"{detail['run_id']}/scorecard.json" in names
        assert f"{detail['run_id']}/agent_spec.json" in names
        assert f"{detail['run_id']}/system_prompt.md" in names

    hr_prompts = suggest_sample_prompts("Human Resources & Workplace Policy", ["kb_search", "calc"])
    assert any("PTO" in p for p in hr_prompts)
    assert any("ignore" in p.lower() for p in hr_prompts)


def test_streamlit_app_renders_headlessly_without_exceptions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    st_testing = pytest.importorskip("streamlit.testing.v1")
    monkeypatch.setattr(ForgeAPIClient, "healthz", lambda _self: None)
    app_path = str(UI_DIR / "app.py")
    at = st_testing.AppTest.from_file(app_path, default_timeout=45)
    at.run()
    assert not at.exception
    assert len(at.sidebar.radio) == 1

    # Switch to Scorecard Inspector and verify clean render of committed demos
    at.sidebar.radio[0].set_value("📊 Scorecard Inspector").run()
    assert not at.exception

    # Switch to Regression & Baseline and verify clean render
    at.sidebar.radio[0].set_value("🔁 Regression & Baseline").run()
    assert not at.exception

    # Switch to Chat Runner and verify clean render
    at.sidebar.radio[0].set_value("💬 Chat Runner").run()
    assert not at.exception
