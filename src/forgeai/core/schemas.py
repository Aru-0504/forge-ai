"""Core domain schemas for the ForgeAI 7-stage iterative agent-factory pipeline and runtime."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

RunStatus = Literal["queued", "running", "done", "failed", "budget_exhausted"]


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


class DomainAnalysis(BaseModel):
    """Structured brief & corpus analysis produced by the `intake` stage."""

    domain: str
    summary: str
    target_users: list[str] = Field(default_factory=list)
    key_topics: list[str] = Field(default_factory=list)
    recommended_tools: list[str] = Field(default_factory=list)
    risk_controls: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


class GroundabilityReport(BaseModel):
    """Corpus coverage and groundability check produced by the `groundability` stage."""

    is_groundable: bool = True
    coverage_score: float = Field(default=0.8, ge=0.0, le=1.0)
    covered_topics: list[str] = Field(default_factory=list)
    missing_gaps: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    detected_scripts: list[str] = Field(default_factory=lambda: ["latin"])
    recommendation: str = "Proceed to generation."


class AgentSpec(BaseModel):
    """Compiled agent specification synthesized by the `generator` stage."""

    name: str
    slug: str
    domain: str
    description: str
    persona: str
    system_prompt: str
    enabled_tools: list[str] = Field(default_factory=lambda: ["kb_search", "calc", "policy_cite"])
    guardrails: list[str] = Field(default_factory=list)
    citation_style: str = "[source#chunk_id]"
    refusal_policy: str = "State clearly when the knowledge base lacks supporting evidence."
    key_topics: list[str] = Field(default_factory=list)
    max_tool_steps: int = Field(default=4, ge=1, le=10)
    iteration: int = Field(default=1, ge=1)


class EvalCase(BaseModel):
    """Single golden or security test case produced by `evalgen`."""

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
    chunk_size: int = Field(default=900, ge=100, le=8000)
    chunk_overlap: int = Field(default=150, ge=0, le=2000)
    max_csv_rows: int = Field(default=5000, ge=10, le=100_000)
    max_loops: int = Field(default=3, ge=1, le=5)
    target_score: float = Field(default=0.9, ge=0.1, le=1.0)
    redact_pii: bool = True
    allow_private_urls: bool = False
    allowed_http_hosts: list[str] = Field(default_factory=list)


class ForgeArtifact(BaseModel):
    """Complete, self-contained, reproducible bundle for a forged agent."""

    schema_version: str = "2.0.0"
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
    analysis: DomainAnalysis
    groundability: GroundabilityReport = Field(default_factory=GroundabilityReport)
    agent_spec: AgentSpec
    knowledge_index: KnowledgeIndexData
    eval_report: EvalReport
    scorecard: Scorecard
    degraded_stages: list[DegradedStageRecord] = Field(default_factory=list)
    loop_history: list[LoopRecord] = Field(default_factory=list)
    report_markdown: str = ""
    metrics: dict[str, Any] = Field(default_factory=dict)

    def save_to_dir(self, target_dir: Path) -> Path:
        """Persist reproducible JSON & Markdown artifacts to `target_dir` (manifest written last atomically)."""
        target_dir.mkdir(parents=True, exist_ok=True)
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
            f"- **Enabled Tools**: {', '.join(self.agent_spec.enabled_tools)}\n\n"
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
                "analysis.json",
                "groundability.json",
                "agent_spec.json",
                "knowledge_index.json",
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
            schema_version=manifest.get("schema_version", "2.0.0"),
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
            analysis=analysis,
            groundability=groundability,
            agent_spec=agent_spec,
            knowledge_index=knowledge_index,
            eval_report=eval_report,
            scorecard=scorecard,
            degraded_stages=scorecard.degraded_stages,
            loop_history=scorecard.loop_history,
            report_markdown=report_md,
            metrics=metrics,
        )
