"""Base stage interface, StageContext, checkpoints, and graceful degradation hooks."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from forgeai.core.schemas import (
    AgentSpec,
    CriticFeedback,
    DegradedStageRecord,
    DomainAnalysis,
    EvalCase,
    EvalReport,
    ForgeArtifact,
    ForgeRequest,
    GroundabilityReport,
    KnowledgeIndexData,
    LoopRecord,
    PipelineEvent,
    RunStatus,
    Scorecard,
)
from forgeai.llm.base import BaseLLMClient
from forgeai.observability.metrics import MetricsTracker

EventCallback = Callable[[PipelineEvent], Any]


@dataclass
class StageContext:
    """Shared mutable state threaded through the 7 ForgeAI pipeline stages."""

    run_id: str
    request: ForgeRequest
    llm: BaseLLMClient
    metrics: MetricsTracker
    emit_event: EventCallback
    config_hash: str = ""
    prompt_versions: dict[str, str] = field(default_factory=dict)
    max_upload_bytes: int = 10 * 1024 * 1024
    max_files: int = 20
    stage_timeout_seconds: float = 120.0
    checkpoint_dir: Path | None = None

    # Stage outputs
    knowledge_index: KnowledgeIndexData | None = None
    analysis: DomainAnalysis | None = None
    groundability: GroundabilityReport | None = None
    eval_cases: list[EvalCase] = field(default_factory=list)

    # Iterative loop state (up to 3 loops)
    iteration: int = 1
    agent_spec: AgentSpec | None = None
    eval_report: EvalReport | None = None
    critic_feedback: CriticFeedback | None = None
    best_agent_spec: AgentSpec | None = None
    best_eval_report: EvalReport | None = None
    best_iteration: int = 1
    loop_history: list[LoopRecord] = field(default_factory=list)

    # Reliability & status tracking
    status: RunStatus = "running"
    budget_exhausted_reason: str = ""
    degraded_stages: list[DegradedStageRecord] = field(default_factory=list)

    # Final outputs
    scorecard: Scorecard | None = None
    report_markdown: str = ""
    artifact: ForgeArtifact | None = None

    def emit(
        self,
        *,
        stage: str,
        event_type: str,
        message: str,
        progress: float,
        data: dict[str, Any] | None = None,
    ) -> None:
        self.emit_event(
            PipelineEvent(
                run_id=self.run_id,
                stage=stage,
                event_type=event_type,  # type: ignore[arg-type]
                message=message,
                progress=progress,
                data=data or {},
            )
        )

    def save_checkpoint(self, key: str, payload: dict[str, Any]) -> None:
        if self.checkpoint_dir is None:
            return
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        path = self.checkpoint_dir / f"{key}.json"
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    def load_checkpoint(self, key: str) -> dict[str, Any] | None:
        if self.checkpoint_dir is None:
            return None
        path = self.checkpoint_dir / f"{key}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))


class BaseStage(ABC):
    """Abstract interface for a ForgeAI pipeline stage with optional graceful fallback."""

    name: str = "base"
    start_progress: float = 0.0
    end_progress: float = 1.0

    @abstractmethod
    def run(self, ctx: StageContext) -> None:
        """Execute this stage, mutating `ctx` with its typed outputs."""

    def fallback(self, ctx: StageContext, reason: str) -> str:
        """Provide a deterministic degraded output if the stage fails twice."""
        raise RuntimeError(f"Stage {self.name!r} has no fallback: {reason}")
