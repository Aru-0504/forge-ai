"""Tests for Core #1-#3 (Intake Interviewer, Groundability Refusal, Declarative Contracts) & Bonuses #1-#3."""

from __future__ import annotations

import time
from pathlib import Path

from fastapi.testclient import TestClient

from forgeai.config import Settings
from forgeai.core.comparison import compare_factory_vs_baseline
from forgeai.core.regression import run_regression
from forgeai.core.runtime.agent import ForgedAgentRuntime
from forgeai.core.schemas import (
    AgentSpec,
    ClarifyingQuestion,
    ForgeArtifact,
    ForgeRequest,
    OutputContract,
    RetrievalConfig,
    SourceDocument,
)
from forgeai.core.stages.pipeline import ForgePipeline
from forgeai.llm.base import StubLLMClient
from forgeai.service.api import create_app


def test_core1_intake_interviewer_interactive_and_structured_requirements(
    hr_policy_bytes: bytes,
    stub_llm: StubLLMClient,
    tmp_path: Path,
) -> None:
    asked_count = {"n": 0}

    def interview_cb(questions: list[ClarifyingQuestion]) -> dict[str, str]:
        assert 1 <= len(questions) <= 5
        asked_count["n"] = len(questions)
        return {questions[0].id: "Shore-based HR staff and managers"}

    pipeline = ForgePipeline(stub_llm)
    req = ForgeRequest(
        goal="Answer employee HR policy questions with handbook citations and meal per-diem math.",
        sources=[SourceDocument(filename="hr_policy.md", content=hr_policy_bytes)],
    )
    artifact = pipeline.run(req, run_id="core1_int", interview_callback=interview_cb)

    assert 1 <= asked_count["n"] <= 5
    assert artifact.requirements.scope
    assert artifact.requirements.audience
    assert artifact.requirements.tone
    assert len(artifact.requirements.must_answer_topics) >= 1
    assert len(artifact.requirements.must_refuse_topics) >= 1
    assert artifact.requirements.clarifying_qa[0].answered_by == "human"
    assert artifact.requirements.clarifying_qa[0].answer == "Shore-based HR staff and managers"

    # Saved artifacts include requirements.json, requirements.md, eval_cases.json
    saved = artifact.save_to_dir(tmp_path / "out")
    assert (saved / "requirements.json").exists()
    assert (saved / "requirements.md").exists()
    assert (saved / "eval_cases.json").exists()


def test_core2_and_core3_groundability_refusal_and_declarative_contracts(
    hr_policy_bytes: bytes,
    stub_llm: StubLLMClient,
) -> None:
    pipeline = ForgePipeline(stub_llm)
    req = ForgeRequest(
        goal="Answer HR policy questions with handbook citations and meal per-diem totals.",
        sources=[SourceDocument(filename="hr_policy.md", content=hr_policy_bytes)],
        chunk_size=750,
        chunk_overlap=100,
    )
    artifact = pipeline.run(req, run_id="core23_test")

    # Core #3: Declarative RetrievalConfig and OutputContract attached & persisted
    rc = artifact.agent_spec.retrieval_config
    oc = artifact.agent_spec.output_contract
    assert isinstance(rc, RetrievalConfig)
    assert rc.chunk_size == 750 and rc.chunk_overlap == 100
    assert isinstance(oc, OutputContract)
    assert oc.require_citations is True
    assert oc.max_words > 0

    # Core #2: Eval suite includes an ungroundable_refusal check AND runtime refuses must-refuse topics
    categories = {c.category for c in artifact.eval_cases}
    assert "ungroundable_refusal" in categories
    assert artifact.eval_report.pass_rate >= 0.9

    rt = ForgedAgentRuntime.from_artifact(artifact, stub_llm)
    refused = rt.answer("Can you negotiate my individual compensation and salary negotiations?")
    assert "outside the grounded scope" in refused.answer.lower() or "cannot" in refused.answer.lower()


def test_bonus1_human_checkpoint_pause_edit_and_approve_via_api(
    temp_settings: Settings,
    stub_llm: StubLLMClient,
    hr_policy_bytes: bytes,
) -> None:
    app = create_app(settings=temp_settings, llm=stub_llm)
    with TestClient(app) as client:
        resp = client.post(
            "/runs",
            data={
                "brief": "Answer HR policy questions with handbook citations and meal per-diem math.",
                "require_human_approval": "true",
            },
            files=[("files", ("hr_policy.md", hr_policy_bytes, "text/markdown"))],
        )
        assert resp.status_code == 202
        run_id = resp.json()["run_id"]

        # Wait until run pauses at Human Checkpoint
        detail: dict = {}
        for _ in range(80):
            detail = client.get(f"/runs/{run_id}").json()
            if detail["status"] in {"awaiting_approval", "done", "failed"}:
                break
            time.sleep(0.05)
        assert detail["status"] == "awaiting_approval"
        assert detail["pending_approval"] is not None

        # Edit requirements and approve
        edited_req = dict(detail["pending_approval"]["requirements"])
        edited_req["tone"] = "Executive brief, strictly bulleted"
        edited_req["must_refuse_topics"].append("cryptocurrency payroll requests")

        app_resp = client.post(f"/runs/{run_id}/approve", json={"requirements": edited_req})
        assert app_resp.status_code == 202
        assert app_resp.json()["approved"] is True

        # Wait until generation completes
        for _ in range(80):
            detail = client.get(f"/runs/{run_id}").json()
            if detail["status"] in {"done", "failed", "budget_exhausted"}:
                break
            time.sleep(0.05)
        assert detail["status"] == "done"
        req_out = detail["artifacts"]["requirements"]
        assert req_out["approved_by_human"] is True
        assert req_out["tone"] == "Executive brief, strictly bulleted"
        assert "cryptocurrency payroll requests" in detail["artifacts"]["agent_spec"]["must_refuse_topics"]


def test_bonus2_factory_vs_human_baseline_comparison(
    hr_policy_bytes: bytes,
    stub_llm: StubLLMClient,
) -> None:
    pipeline = ForgePipeline(stub_llm)
    artifact = pipeline.run(
        ForgeRequest(
            goal="Answer employee HR policy questions with citations and compute meal per-diem totals.",
            sources=[SourceDocument(filename="hr_policy.md", content=hr_policy_bytes)],
        ),
        run_id="factory_vs_human",
    )
    baseline_spec = AgentSpec(
        name="Hand-Written HR Baseline",
        slug="human-baseline",
        domain="Human Resources & Workplace Policy",
        description="Hand-written baseline missing calc tool and must_refuse list.",
        persona="Friendly HR coordinator",
        system_prompt="Answer HR questions concisely from the handbook.",
        enabled_tools=["kb_search"],
        must_refuse_topics=[],
    )

    comp = compare_factory_vs_baseline(
        factory_artifact=artifact,
        baseline_spec=baseline_spec,
        llm=stub_llm,
    )
    assert comp.total_cases == len(artifact.eval_cases)
    assert comp.factory_pass_rate > comp.baseline_pass_rate
    assert comp.factory_wins >= 1
    assert len(comp.where_factory_wins) >= 1
    assert len(comp.where_baseline_wins) >= 1  # Conciseness tradeoff logged honestly
    assert "# Factory vs. Human Baseline Comparison" in comp.report_markdown


def test_bonus3_regression_mode_detects_broken_eval_when_docs_change(
    temp_settings: Settings,
    stub_llm: StubLLMClient,
    hr_policy_bytes: bytes,
) -> None:
    # 1. Pure core regression test: remove PTO section -> PTO eval case regresses!
    pipeline = ForgePipeline(stub_llm)
    baseline = pipeline.run(
        ForgeRequest(
            goal="Answer HR policy questions with citations and per-diem calculations.",
            sources=[SourceDocument(filename="hr_policy.md", content=hr_policy_bytes)],
        ),
        run_id="reg_base",
    )
    assert baseline.eval_report.pass_rate >= 0.9

    # Updated doc removes PTO & parental leave clauses
    updated_doc = b"# Updated HR Policy\n## Expenses\nDomestic meal per diem is 75 USD per day.\n"
    _regen, reg_report = run_regression(
        baseline_artifact=baseline,
        updated_sources=[SourceDocument(filename="hr_policy.md", content=updated_doc)],
        llm=stub_llm,
    )
    assert reg_report.has_regressions is True
    assert reg_report.regressed_count >= 1
    assert reg_report.new_pass_rate < reg_report.old_pass_rate
    assert "REGRESSIONS DETECTED" in reg_report.report_markdown

    # 2. FastAPI POST /runs/{id}/regress endpoint test
    app = create_app(settings=temp_settings, llm=stub_llm)
    with TestClient(app) as client:
        r = client.post(
            "/runs",
            data={"brief": "Answer HR policy questions with citations and per-diem calculations."},
            files=[("files", ("hr_policy.md", hr_policy_bytes, "text/markdown"))],
        )
        run_id = r.json()["run_id"]
        for _ in range(80):
            if client.get(f"/runs/{run_id}").json()["status"] == "done":
                break
            time.sleep(0.05)

        reg_resp = client.post(
            f"/runs/{run_id}/regress",
            files=[("files", ("hr_policy.md", updated_doc, "text/markdown"))],
        )
        assert reg_resp.status_code == 200
        body = reg_resp.json()
        assert body["has_regressions"] is True
        assert body["regressed_count"] >= 1


def test_committed_baseline_comparison_artifact_exists() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    comp_dir = repo_root / "demos" / "baseline_comparison"
    assert (comp_dir / "human_baseline_hr_spec.json").exists()
    assert (comp_dir / "comparison_report.json").exists()
    assert (comp_dir / "comparison_report.md").exists()
    # Also verify committed demos include v2.2 requirements & >=20 eval_cases
    for name in ("run1_hr_policy", "run2_ship_spares"):
        art = ForgeArtifact.load_from_dir(repo_root / "demos" / name)
        assert (repo_root / "demos" / name / "requirements.json").exists()
        assert (repo_root / "demos" / name / "requirements.md").exists()
        assert len(art.eval_cases) >= 20


def test_priority1_20_eval_cases_groundability_fix_and_targeted_critic_patch(
    ship_spares_bytes: bytes,
    hr_policy_bytes: bytes,
    stub_llm: StubLLMClient,
) -> None:
    from forgeai.core.schemas import CriticFeedback, FailureDiagnosis
    from forgeai.core.stages.generator_stage import apply_targeted_fix

    # 1. Groundability guard does NOT falsely flag part_lookup or lead_time_calc on ship spares CSV
    pipeline = ForgePipeline(stub_llm)
    spares_art = pipeline.run(
        ForgeRequest(
            goal="Assist marine engineers with ship spares SKU lookups, stock checks, and cost calculations.",
            sources=[SourceDocument(filename="ship_spares_catalog.csv", content=ship_spares_bytes)],
        ),
        run_id="p1_spares_ground",
    )
    assert "part_lookup" not in spares_art.agent_spec.must_refuse_topics
    assert "lead_time_calc" not in spares_art.agent_spec.must_refuse_topics
    assert len(spares_art.eval_cases) >= 20

    # 2. Two-document HR corpus generates >=20 cases including multi-doc, ambiguous, refusal, & injections
    second_doc = b"# Equipment Policy\n## Home Office\nFull-time staff get a 1000 USD stipend.\n"
    hr_art = pipeline.run(
        ForgeRequest(
            goal="Answer employee HR policy and equipment stipend questions with citations.",
            sources=[
                SourceDocument(filename="hr_policy.md", content=hr_policy_bytes),
                SourceDocument(filename="equipment_policy.md", content=second_doc),
            ],
        ),
        run_id="p1_hr_20cases",
    )
    assert len(hr_art.eval_cases) >= 20
    cats = {c.category for c in hr_art.eval_cases}
    assert {"safety_injection", "doc_prompt_injection", "ungroundable_refusal", "edge_ambiguous", "edge_multi_doc"} <= cats
    assert "## Honest Quality Assessment: What Still Fails & Why" in hr_art.report_markdown
    assert "## Critic–Optimizer Loop Progression & Change Log" in hr_art.report_markdown

    # 3. Targeted Critic-Optimizer patch mutates retrieval_config & prompt surgically (no full regen)
    fb = CriticFeedback(
        should_stop=False,
        summary="Fix retrieval miss on L&D query and remove overly broad refusal.",
        failure_classifications=[
            FailureDiagnosis(case_id="eval_07", category="retrieval_miss", reason="0 hits"),
        ],
        retrieval_patch={"top_k": 7, "min_score_threshold": 0.02},
        prompt_patch="Search using section headings and cite exact [source#chunk_id].",
        must_refuse_add=["crypto payroll"],
    )
    patched, changes = apply_targeted_fix(
        hr_art.agent_spec,
        fb,
        iteration=2,
        request=ForgeRequest(goal="Answer HR policy questions", sources=[]),
        knowledge_index=hr_art.knowledge_index,
    )
    assert patched.iteration == 2
    assert patched.name == hr_art.agent_spec.name  # Preserved without full regeneration
    assert patched.retrieval_config.top_k == 7
    assert patched.retrieval_config.min_score_threshold == 0.02
    assert "crypto payroll" in patched.must_refuse_topics
    assert any("retrieval_config.top_k" in ch for ch in changes)
