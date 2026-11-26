"""Core domain schemas for the ForgeAI 7-stage iterative agent-factory pipeline, runtime, and regression."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

RunStatus = Literal[
    "queued",
    "running",
    "awaiting_approval",
    "done",
    "failed",
    "budget_exhausted",
]


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


class SourceDocument(BaseModel):
    """Raw input document submitted to a Forge run."""

    filename: str
    content: bytes = Field(exclude=True, repr=False)
    media_type: str = "application/octet-stream"


class DocumentChunk(BaseModel):
    """Deterministic indexed text chunk with source provenance."""

    chunk_id: str
    source: str
    section: str = ""
    text: str
    char_start: int = 0
    char_end: int = 0
    injection_flagged: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class StructuredTable(BaseModel):
    """Parsed tabular data (e.g. CSV or JSON arrays) queryable via `table_lookup`."""

    source: str
    columns: list[str]
    rows: list[dict[str, Any]] = Field(default_factory=list)
    truncated: bool = False
    total_rows_seen: int = 0


class KnowledgeIndexData(BaseModel):
    """Serializable corpus representation produced during Intake."""

    sources: list[str] = Field(default_factory=list)
    chunks: list[DocumentChunk] = Field(default_factory=list)
    tables: list[StructuredTable] = Field(default_factory=list)
    total_chars: int = 0
    flagged_chunks: int = 0
    detected_scripts: list[str] = Field(default_factory=lambda: ["latin"])
    ingest_warnings: list[str] = Field(default_factory=list)


class ClarifyingQuestion(BaseModel):
    """Single clarifying question asked by the Intake Interviewer (at most 5 per run)."""

    id: str
    question: str
    rationale: str = ""
    default_answer: str = ""


class ClarifyingQuestionsList(BaseModel):
    """Container for up to 5 Intake Interviewer clarifying questions."""

    questions: list[ClarifyingQuestion] = Field(default_factory=list, max_length=5)


class ClarifyingQA(BaseModel):
    """Recorded question/answer pair from interactive or unattended intake."""

    id: str
    question: str
    answer: str
    answered_by: Literal["human", "default_unattended"] = "default_unattended"


class RequirementsDoc(BaseModel):
    """Structured requirements document produced by Core #1 (Intake Interviewer)."""

    domain: str
    scope: str
    audience: str
    tone: str = "Clear, precise, and citation-first"
    must_answer_topics: list[str] = Field(default_factory=list)
    must_refuse_topics: list[str] = Field(default_factory=list)
    clarifying_qa: list[ClarifyingQA] = Field(default_factory=list)
    recommended_tools: list[str] = Field(default_factory=list)
    risk_controls: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    approved_by_human: bool = False


class DomainAnalysis(BaseModel):
    """Structured brief & corpus summary (kept in sync with RequirementsDoc)."""

    domain: str
    summary: str
    target_users: list[str] = Field(default_factory=list)
    key_topics: list[str] = Field(default_factory=list)
    recommended_tools: list[str] = Field(default_factory=list)
    risk_controls: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


class GroundabilityReport(BaseModel):
    """Corpus coverage and groundability check produced by Core #2 (`groundability`)."""

    is_groundable: bool = True
    coverage_score: float = Field(default=0.8, ge=0.0, le=1.0)
    covered_topics: list[str] = Field(default_factory=list)
    missing_gaps: list[str] = Field(default_factory=list)
    unsupported_requests: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    detected_scripts: list[str] = Field(default_factory=lambda: ["latin"])
    recommendation: str = "Proceed to generation."


class RetrievalConfig(BaseModel):
    """Declarative chunking and retrieval parameters attached to an AgentSpec (Core #3)."""

    chunk_size: int = Field(default=900, ge=100, le=8000)
    chunk_overlap: int = Field(default=150, ge=0, le=2000)
    top_k: int = Field(default=4, ge=1, le=15)
    bm25_weight: float = Field(default=0.7, ge=0.0, le=1.0)
    cosine_weight: float = Field(default=0.3, ge=0.0, le=1.0)
    min_score_threshold: float = Field(default=0.05, ge=0.0, le=5.0)


class OutputContract(BaseModel):
    """Declarative output contract enforced by runtime and Judge (Core #3)."""

    format: Literal["markdown", "plain", "json"] = "markdown"
    require_citations: bool = True
    citation_style: str = "[source#chunk_id]"
    max_words: int = Field(default=350, ge=30, le=2000)
    required_sections: list[str] = Field(default_factory=list)
    abstain_phrase: str = "I cannot verify this from the provided documents."


class AgentSpec(BaseModel):
    """Complete declarative agent definition synthesized by Core #3 (`generator`)."""

    name: str
    slug: str
    domain: str
    description: str
    persona: str
    system_prompt: str
    enabled_tools: list[str] = Field(default_factory=lambda: ["kb_search", "calc", "policy_cite"])
    retrieval_config: RetrievalConfig = Field(default_factory=RetrievalConfig)
    output_contract: OutputContract = Field(default_factory=OutputContract)
    guardrails: list[str] = Field(default_factory=list)
    citation_style: str = "[source#chunk_id]"
    refusal_policy: str = "State clearly when the knowledge base lacks supporting evidence."
    key_topics: list[str] = Field(default_factory=list)
    must_refuse_topics: list[str] = Field(default_factory=list)
    max_tool_steps: int = Field(default=4, ge=1, le=10)
    iteration: int = Field(default=1, ge=1)


class EvalCase(BaseModel):
    """Single golden, refusal, or security test case produced by `evalgen`."""

    id: str
    category: str = "grounded_lookup"
    question: str
    expected_keywords: list[str] = Field(default_factory=list)
    expected_tools: list[str] = Field(default_factory=list)
    expect_refusal: bool = False
    injected_doc_payload: str = ""
    forbidden_keywords: list[str] = Field(default_factory=list)


class EvalCaseList(BaseModel):
    """Container schema for LLM-generated evaluation suites."""

    cases: list[EvalCase] = Field(default_factory=list)


class ToolTrace(BaseModel):
    """Record of one tool invocation during agent execution."""

    tool_name: str
    args: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] = Field(default_factory=dict)
    ok: bool = True
    latency_ms: float = 0.0


class EvalCaseResult(BaseModel):
    """Scored outcome of running one EvalCase through the `judge` stage."""

    case_id: str
    category: str
    question: str
    answer: str
    citations: list[str] = Field(default_factory=list)
    tools_used: list[str] = Field(default_factory=list)
    passed: bool
    score: float = Field(ge=0.0, le=1.0)
    checks: dict[str, bool] = Field(default_factory=dict)
    notes: str = ""
    latency_ms: float = 0.0


class EvalReport(BaseModel):
    """Aggregated evaluation scorecard for one generator->judge iteration."""

    iteration: int = 1
    total_cases: int
    passed_cases: int
    pass_rate: float = Field(ge=0.0, le=1.0)
    average_score: float = Field(ge=0.0, le=1.0)
    safety_passed: bool = True
    doc_injection_passed: bool = True
    results: list[EvalCaseResult] = Field(default_factory=list)
    summary: str = ""


class CriticFeedback(BaseModel):
    """Actionable critique produced by the `critic` stage to guide the next loop."""

    should_stop: bool = False
    summary: str = ""
    root_causes: list[str] = Field(default_factory=list)
    prompt_patch: str = ""
    recommended_tool_changes: list[str] = Field(default_factory=list)


class LoopRecord(BaseModel):
    """Recorded telemetry for one generator -> judge -> critic iteration."""

    iteration: int
    pass_rate: float
    average_score: float
    safety_passed: bool
    doc_injection_passed: bool
    critic_summary: str = ""


class DegradedStageRecord(BaseModel):
    """Audit record when a stage failed twice and degraded gracefully."""

    stage: str
    attempts: int
    reason: str
    fallback_used: str


class Scorecard(BaseModel):
    """Final run scorecard combining eval scores, stage tokens/latency, and reproducibility."""

    run_id: str
    status: RunStatus = "done"
    provider: str
    model: str
    model_version: str = "latest"
    seed: int | None = 42
    config_hash: str = ""
    prompt_versions: dict[str, str] = Field(default_factory=dict)
    loops_executed: int = 1
    best_iteration: int = 1
    pass_rate: float = 0.0
    average_score: float = 0.0
    safety_passed: bool = True
    doc_injection_passed: bool = True
    groundability_score: float = 0.0
    degraded_stages: list[DegradedStageRecord] = Field(default_factory=list)
    loop_history: list[LoopRecord] = Field(default_factory=list)
    stage_metrics: dict[str, Any] = Field(default_factory=dict)
    total_tokens: int = 0
    total_latency_ms: float = 0.0
    total_cost_usd: float = 0.0
    budget_exhausted_reason: str = ""


class RegressionCaseDiff(BaseModel):
    """Comparison of one historical EvalCase before and after document changes."""

    case_id: str
    category: str
    question: str
    status: Literal["regressed", "fixed", "still_passing", "still_failing"]
    old_passed: bool
    new_passed: bool
    old_score: float
    new_score: float
    old_notes: str = ""
    new_notes: str = ""


class RegressionReport(BaseModel):
    """Full diff report produced by Regression Mode when documents change."""

    baseline_run_id: str
    regenerated_run_id: str
    created_at: str = Field(default_factory=utc_now_iso)
    total_cases: int
    regressed_count: int
    fixed_count: int
    still_passing_count: int
    still_failing_count: int
    old_pass_rate: float
    new_pass_rate: float
    has_regressions: bool
    diffs: list[RegressionCaseDiff] = Field(default_factory=list)
    summary: str = ""
    report_markdown: str = ""


class BaselineComparisonCase(BaseModel):
    """Per-case head-to-head comparison between Factory agent and hand-written Human baseline."""

    case_id: str
    category: str
    question: str
    factory_passed: bool
    baseline_passed: bool
    factory_score: float
    baseline_score: float
    winner: Literal["factory", "baseline", "tie"]
    factory_notes: str = ""
    baseline_notes: str = ""


class BaselineComparisonReport(BaseModel):
    """Head-to-head scorecard comparing Factory-forged vs Human-baseline agent."""

    factory_agent_name: str
    baseline_agent_name: str
    created_at: str = Field(default_factory=utc_now_iso)
    total_cases: int
    factory_pass_rate: float
    baseline_pass_rate: float
    factory_avg_score: float
    baseline_avg_score: float
    factory_wins: int
    baseline_wins: int
    ties: int
    cases: list[BaselineComparisonCase] = Field(default_factory=list)
    where_factory_wins: list[str] = Field(default_factory=list)
    where_baseline_wins: list[str] = Field(default_factory=list)
    report_markdown: str = ""


class RuntimeAction(BaseModel):
    """Structured step returned by the LLM inside the ForgedAgentRuntime loop."""

    action: Literal["tool_call", "final"]
    thought: str = ""
    tool_name: str | None = None
    tool_args: dict[str, Any] = Field(default_factory=dict)
    answer: str = ""
    citations: list[str] = Field(default_factory=list)


class ChatMessage(BaseModel):
    """Single turn in a user <-> forged-agent conversation."""

    role: Literal["user", "assistant"]
    content: str
    citations: list[str] = Field(default_factory=list)
    tool_traces: list[ToolTrace] = Field(default_factory=list)
    timestamp: str = Field(default_factory=utc_now_iso)


class ChatResponse(BaseModel):
    """Response returned by ForgedAgentRuntime.answer()."""

    answer: str
    citations: list[str] = Field(default_factory=list)
    tool_traces: list[ToolTrace] = Field(default_factory=list)
    blocked_injection: bool = False
    latency_ms: float = 0.0
    usage: dict[str, int] = Field(default_factory=dict)


class PipelineEvent(BaseModel):
    """Typed lifecycle event emitted by ForgePipeline for CLI, logs, and SSE."""

    run_id: str
    stage: str
    event_type: Literal[
        "run_started",
        "stage_started",
        "stage_progress",
        "stage_degraded",
        "stage_completed",
        "awaiting_approval",
        "budget_exhausted",
        "run_completed",
        "run_failed",
    ]
    message: str
    progress: float = Field(default=0.0, ge=0.0, le=1.0)
    timestamp: str = Field(default_factory=utc_now_iso)
    data: dict[str, Any] = Field(default_factory=dict)


class ForgeRequest(BaseModel):
    """Input payload for forging a new domain-specific agent."""

    goal: str = Field(min_length=5, max_length=2000)
    agent_name_hint: str | None = None
    sources: list[SourceDocument] = Field(default_factory=list)
    urls: list[str] = Field(default_factory=list)
    clarifying_answers: dict[str, str] = Field(default_factory=dict)
    require_human_approval: bool = False
    chunk_size: int = Field(default=900, ge=100, le=8000)
    chunk_overlap: int = Field(default=150, ge=0, le=2000)
    max_csv_rows: int = Field(default=5000, ge=10, le=100_000)
    max_loops: int = Field(default=3, ge=1, le=5)
    target_score: float = Field(default=0.9, ge=0.1, le=1.0)
    redact_pii: bool = True
    allow_private_urls: bool = False
    allowed_http_hosts: list[str] = Field(default_factory=list)


def render_requirements_markdown(req: RequirementsDoc, run_id: str) -> str:
    """Render a clean human-readable requirements.md for Core #1 & Human Checkpoint review."""
    lines = [
        f"# Structured Requirements Document ({run_id})",
        "",
        f"- **Domain**: {req.domain}",
        f"- **Scope**: {req.scope}",
        f"- **Audience**: {req.audience}",
        f"- **Tone**: {req.tone}",
        f"- **Approved by Human**: {'Yes' if req.approved_by_human else 'No (Unattended / Pending)'}",
        f"- **Recommended Tools**: `{', '.join(req.recommended_tools)}`",
        "",
        "## Must-Answer Topics",
        "",
        *[f"- {t}" for t in req.must_answer_topics],
        "",
        "## Must-Refuse Topics (Out-of-Scope & Groundability Gaps)",
        "",
        *[f"- {t}" for t in req.must_refuse_topics],
        "",
        "## Clarifying Interview Q&A (≤ 5 Questions)",
        "",
    ]
    for qa in req.clarifying_qa:
        lines.append(f"- **{qa.id}**: {qa.question}")
        lines.append(f"  - *Answer ({qa.answered_by})*: {qa.answer}")
    if req.risk_controls:
        lines.extend(["", "## Risk Controls", "", *[f"- {c}" for c in req.risk_controls]])
    if req.assumptions:
        lines.extend(["", "## Assumptions", "", *[f"- {a}" for a in req.assumptions]])
    lines.append("")
    return "\n".join(lines)


class ForgeArtifact(BaseModel):
    """Complete, self-contained, reproducible bundle for a forged agent."""

    schema_version: str = "2.1.0"
    run_id: str
    status: RunStatus = "done"
    created_at: str = Field(default_factory=utc_now_iso)
    goal: str
    provider: str
    model: str
    model_version: str = "latest"
    seed: int | None = 42
    config_hash: str = ""
    prompt_versions: dict[str, str] = Field(default_factory=dict)
    requirements: RequirementsDoc = Field(
        default_factory=lambda: RequirementsDoc(
            domain="General",
            scope="Answer questions grounded in provided documents.",
            audience="Operators",
        )
    )
    analysis: DomainAnalysis
    groundability: GroundabilityReport = Field(default_factory=GroundabilityReport)
    agent_spec: AgentSpec
    knowledge_index: KnowledgeIndexData
    eval_cases: list[EvalCase] = Field(default_factory=list)
    eval_report: EvalReport
    scorecard: Scorecard
    degraded_stages: list[DegradedStageRecord] = Field(default_factory=list)
    loop_history: list[LoopRecord] = Field(default_factory=list)
    report_markdown: str = ""
    metrics: dict[str, Any] = Field(default_factory=dict)

    def save_to_dir(self, target_dir: Path) -> Path:
        """Persist reproducible JSON & Markdown artifacts to `target_dir` (manifest written last atomically)."""
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / "requirements.json").write_text(
            self.requirements.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
        (target_dir / "requirements.md").write_text(
            render_requirements_markdown(self.requirements, self.run_id), encoding="utf-8"
        )
        (target_dir / "analysis.json").write_text(
            self.analysis.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
        (target_dir / "groundability.json").write_text(
            self.groundability.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
        (target_dir / "agent_spec.json").write_text(
            self.agent_spec.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
        (target_dir / "knowledge_index.json").write_text(
            self.knowledge_index.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
        (target_dir / "eval_cases.json").write_text(
            json.dumps([c.model_dump() for c in self.eval_cases], indent=2, ensure_ascii=False)
            + "\n",
            encoding="utf-8",
        )
        (target_dir / "eval_report.json").write_text(
            self.eval_report.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
        (target_dir / "scorecard.json").write_text(
            self.scorecard.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
        (target_dir / "report.md").write_text(
            self.report_markdown or f"# ForgeAI Report ({self.run_id})\n",
            encoding="utf-8",
        )
        (target_dir / "metrics.json").write_text(
            json.dumps(self.metrics, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        (target_dir / "system_prompt.md").write_text(
            f"# {self.agent_spec.name}\n\n"
            f"- **Run ID**: `{self.run_id}`\n"
            f"- **Domain**: {self.agent_spec.domain}\n"
            f"- **Model**: `{self.provider}:{self.model}` (`{self.model_version}`)\n"
            f"- **Enabled Tools**: {', '.join(self.agent_spec.enabled_tools)}\n"
            f"- **Retrieval Config**: `top_k={self.agent_spec.retrieval_config.top_k}`, "
            f"`bm25={self.agent_spec.retrieval_config.bm25_weight}`, "
            f"`cosine={self.agent_spec.retrieval_config.cosine_weight}`\n"
            f"- **Output Contract**: `format={self.agent_spec.output_contract.format}`, "
            f"`require_citations={self.agent_spec.output_contract.require_citations}`, "
            f"`max_words={self.agent_spec.output_contract.max_words}`\n\n"
            f"## System Prompt\n\n{self.agent_spec.system_prompt}\n",
            encoding="utf-8",
        )

        manifest = {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "status": self.status,
            "created_at": self.created_at,
            "goal": self.goal,
            "provider": self.provider,
            "model": self.model,
            "model_version": self.model_version,
            "seed": self.seed,
            "config_hash": self.config_hash,
            "prompt_versions": self.prompt_versions,
            "agent_name": self.agent_spec.name,
            "agent_slug": self.agent_spec.slug,
            "domain": self.agent_spec.domain,
            "sources": self.knowledge_index.sources,
            "chunk_count": len(self.knowledge_index.chunks),
            "table_count": len(self.knowledge_index.tables),
            "eval_pass_rate": self.eval_report.pass_rate,
            "eval_average_score": self.eval_report.average_score,
            "degraded_stages": [d.stage for d in self.degraded_stages],
            "files": [
                "manifest.json",
                "requirements.json",
                "requirements.md",
                "analysis.json",
                "groundability.json",
                "agent_spec.json",
                "knowledge_index.json",
                "eval_cases.json",
                "eval_report.json",
                "scorecard.json",
                "report.md",
                "metrics.json",
                "system_prompt.md",
            ],
        }
        tmp_manifest = target_dir / "manifest.json.tmp"
        tmp_manifest.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        tmp_manifest.replace(target_dir / "manifest.json")
        return target_dir

    @classmethod
    def load_from_dir(cls, source_dir: Path) -> ForgeArtifact:
        """Load a previously packaged ForgeArtifact from disk."""
        manifest = json.loads((source_dir / "manifest.json").read_text(encoding="utf-8"))
        analysis = DomainAnalysis.model_validate_json(
            (source_dir / "analysis.json").read_text(encoding="utf-8")
        )
        req_path = source_dir / "requirements.json"
        if req_path.exists():
            requirements = RequirementsDoc.model_validate_json(req_path.read_text(encoding="utf-8"))
        else:
            requirements = RequirementsDoc(
                domain=analysis.domain,
                scope=analysis.summary,
                audience=", ".join(analysis.target_users) or "Operators",
                must_answer_topics=analysis.key_topics,
                recommended_tools=analysis.recommended_tools,
                risk_controls=analysis.risk_controls,
                assumptions=analysis.assumptions,
            )

        ground_path = source_dir / "groundability.json"
        groundability = (
            GroundabilityReport.model_validate_json(ground_path.read_text(encoding="utf-8"))
            if ground_path.exists()
            else GroundabilityReport()
        )
        agent_spec = AgentSpec.model_validate_json(
            (source_dir / "agent_spec.json").read_text(encoding="utf-8")
        )
        knowledge_index = KnowledgeIndexData.model_validate_json(
            (source_dir / "knowledge_index.json").read_text(encoding="utf-8")
        )
        eval_cases_path = source_dir / "eval_cases.json"
        eval_cases = (
            [EvalCase.model_validate(c) for c in json.loads(eval_cases_path.read_text(encoding="utf-8"))]
            if eval_cases_path.exists()
            else []
        )
        eval_report = EvalReport.model_validate_json(
            (source_dir / "eval_report.json").read_text(encoding="utf-8")
        )
        metrics_path = source_dir / "metrics.json"
        metrics = (
            json.loads(metrics_path.read_text(encoding="utf-8")) if metrics_path.exists() else {}
        )
        scorecard_path = source_dir / "scorecard.json"
        if scorecard_path.exists():
            scorecard = Scorecard.model_validate_json(scorecard_path.read_text(encoding="utf-8"))
        else:
            scorecard = Scorecard(
                run_id=manifest["run_id"],
                status=manifest.get("status", "done"),
                provider=manifest.get("provider", "stub"),
                model=manifest.get("model", "stub-forgeai-v1"),
                model_version=manifest.get("model_version", "latest"),
                pass_rate=eval_report.pass_rate,
                average_score=eval_report.average_score,
                safety_passed=eval_report.safety_passed,
                doc_injection_passed=eval_report.doc_injection_passed,
            )
        report_path = source_dir / "report.md"
        report_md = report_path.read_text(encoding="utf-8") if report_path.exists() else ""

        return cls(
            schema_version=manifest.get("schema_version", "2.1.0"),
            run_id=manifest["run_id"],
            status=manifest.get("status", scorecard.status),
            created_at=manifest.get("created_at", utc_now_iso()),
            goal=manifest["goal"],
            provider=manifest.get("provider", "stub"),
            model=manifest.get("model", "stub-forgeai-v1"),
            model_version=manifest.get("model_version", "latest"),
            seed=manifest.get("seed", 42),
            config_hash=manifest.get("config_hash", ""),
            prompt_versions=manifest.get("prompt_versions", {}),
            requirements=requirements,
            analysis=analysis,
            groundability=groundability,
            agent_spec=agent_spec,
            knowledge_index=knowledge_index,
            eval_cases=eval_cases,
            eval_report=eval_report,
            scorecard=scorecard,
            degraded_stages=scorecard.degraded_stages,
            loop_history=scorecard.loop_history,
            report_markdown=report_md,
            metrics=metrics,
        )
