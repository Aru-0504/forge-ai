"""Integration tests for FastAPI routes (/runs, /runs/{id}, /runs/{id}/events, /agents/{id}/chat, /healthz)."""

from __future__ import annotations

import time
from pathlib import Path

from fastapi.testclient import TestClient

from forgeai.config import Settings
from forgeai.core.schemas import ForgeArtifact
from forgeai.llm.base import StubLLMClient
from forgeai.service.api import create_app


def test_api_runs_multipart_sse_artifacts_and_chat_lifecycle(
    temp_settings: Settings,
    stub_llm: StubLLMClient,
    hr_policy_bytes: bytes,
) -> None:
    app = create_app(settings=temp_settings, llm=stub_llm)

    with TestClient(app) as client:
        # Healthz endpoint
        health = client.get("/healthz")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"
        assert "config_hash" in health.json()

        # Submit multipart POST /runs (brief + uploaded docs)
        create_resp = client.post(
            "/runs",
            data={
                "brief": "Answer HR policy questions with handbook citations and per-diem math.",
                "agent_name_hint": "HR Policy Advisor",
                "max_loops": "3",
            },
            files=[("files", ("hr_policy.md", hr_policy_bytes, "text/markdown"))],
        )
        assert create_resp.status_code == 202
        run_id = create_resp.json()["run_id"]

        # Poll GET /runs/{id} until status == done
        detail: dict = {}
        for _ in range(80):
            r = client.get(f"/runs/{run_id}")
            assert r.status_code == 200
            detail = r.json()
            if detail["status"] in {"done", "failed", "budget_exhausted"}:
                break
            time.sleep(0.05)
        assert detail["status"] == "done"
        assert detail["has_logs"] is True
        assert detail["artifacts"]["agent_spec"]["name"] == "HR Policy Advisor"
        assert detail["artifacts"]["scorecard"]["pass_rate"] >= 0.9
        assert detail["artifacts"]["scorecard"]["doc_injection_passed"] is True

        # SSE stream replay on GET /runs/{id}/events
        sse_resp = client.get(f"/runs/{run_id}/events")
        assert sse_resp.status_code == 200
        assert "event: run_started" in sse_resp.text
        assert "event: run_completed" in sse_resp.text

        # Chat on POST /agents/{id}/chat + session persistence
        chat1 = client.post(
            f"/agents/{run_id}/chat",
            json={"message": "How many days of annual PTO do employees receive?"},
        )
        assert chat1.status_code == 200
        chat_data = chat1.json()
        assert "20" in chat_data["answer"]
        session_id = chat_data["session_id"]

        hist = client.get(f"/agents/{run_id}/sessions/{session_id}")
        assert hist.status_code == 200
        assert len(hist.json()["messages"]) == 2

        # Prometheus metrics
        prom = client.get("/metrics?format=prometheus")
        assert prom.status_code == 200
        assert "forgeai_forge_runs_done 1" in prom.text


def test_api_handles_empty_input_and_auth_cleanly(
    temp_settings: Settings,
    stub_llm: StubLLMClient,
) -> None:
    secured = temp_settings.model_copy(update={"api_key": "top-secret-key"})
    app = create_app(settings=secured, llm=stub_llm)

    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/runs").status_code == 401
        assert client.get("/runs", headers={"X-API-Key": "wrong"}).status_code == 401
        assert client.get("/runs", headers={"X-API-Key": "top-secret-key"}).status_code == 200

        # Empty submission returns clear 400 error message (no stack trace)
        bad = client.post(
            "/runs",
            json={"brief": "Answer HR policy questions", "sources": []},
            headers={"X-API-Key": "top-secret-key"},
        )
        assert bad.status_code == 400
        assert "Empty submission" in bad.json()["detail"]


def test_api_resume_and_committed_service_demos(
    temp_settings: Settings,
    stub_llm: StubLLMClient,
    ship_spares_bytes: bytes,
) -> None:
    app = create_app(settings=temp_settings, llm=stub_llm)
    with TestClient(app) as client:
        resp = client.post(
            "/runs",
            json={
                "brief": "Query ship spares catalog and calculate order totals.",
                "sources": [
                    {"filename": "ship_spares.csv", "content": ship_spares_bytes.decode("utf-8")}
                ],
            },
        )
        assert resp.status_code == 202
        run_id = resp.json()["run_id"]
        for _ in range(80):
            st = client.get(f"/runs/{run_id}").json()
            if st["status"] in {"done", "failed", "budget_exhausted"}:
                break
            time.sleep(0.05)
        assert st["status"] == "done"

        # Resume endpoint works cleanly on an existing run
        res = client.post(f"/runs/{run_id}/resume")
        assert res.status_code == 202
        assert res.json()["resumed"] is True
        for _ in range(80):
            st2 = client.get(f"/runs/{run_id}").json()
            if st2["status"] in {"done", "failed", "budget_exhausted"}:
                break
            time.sleep(0.05)
        assert st2["status"] == "done"

    # Verify both committed demos produced via the real service load & pass all gates
    repo_root = Path(__file__).resolve().parents[2]
    for demo_name in ("run1_hr_policy", "run2_ship_spares"):
        demo_dir = repo_root / "demos" / demo_name
        art = ForgeArtifact.load_from_dir(demo_dir)
        assert art.status == "done"
        assert art.eval_report.pass_rate >= 0.9
        assert len(art.eval_cases) >= 20
        assert art.eval_report.safety_passed is True
        assert art.eval_report.doc_injection_passed is True
        assert (demo_dir / "scorecard.json").exists()
        assert (demo_dir / "report.md").exists()
        assert (demo_dir / "run_logs.jsonl").exists()
        assert (demo_dir / "sse_events.txt").exists()
        assert (demo_dir / "sample_transcript.md").exists()
