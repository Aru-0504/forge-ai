"""Stage 2 (groundability): verify corpus coverage, warnings, and script readiness."""

from __future__ import annotations

from forgeai.core.prompts.templates import build_groundability_messages
from forgeai.core.schemas import GroundabilityReport
from forgeai.core.stages.base import BaseStage, StageContext


class GroundabilityStage(BaseStage):
    """Audits whether the ingested corpus can ground the user's requested agent."""

    name = "groundability"
    start_progress = 0.22
    end_progress = 0.34

    def run(self, ctx: StageContext) -> None:
        if ctx.knowledge_index is None or ctx.analysis is None:
            raise RuntimeError("GroundabilityStage requires intake outputs.")

        messages = build_groundability_messages(
            ctx.request.goal, ctx.analysis, ctx.knowledge_index
        )
        report = ctx.llm.generate_json(
            messages,
            GroundabilityReport,
            stage=self.name,
            metrics=ctx.metrics,
        )
        report.detected_scripts = list(ctx.knowledge_index.detected_scripts)
        for w in ctx.knowledge_index.ingest_warnings:
            if w not in report.warnings:
                report.warnings.append(w)

        ctx.groundability = report
        ctx.emit(
            stage=self.name,
            event_type="stage_progress",
            message=(
                f"Groundability audit: groundable={report.is_groundable}, "
                f"coverage={report.coverage_score:.0%}, warnings={len(report.warnings)}."
            ),
            progress=self.end_progress,
            data={
                "is_groundable": report.is_groundable,
                "coverage_score": report.coverage_score,
                "warnings": report.warnings,
            },
        )

    def fallback(self, ctx: StageContext, reason: str) -> str:
        idx = ctx.knowledge_index
        chunk_count = len(idx.chunks) if idx else 0
        coverage = 0.8 if chunk_count >= 3 else (0.5 if chunk_count >= 1 else 0.0)
        warnings = list(idx.ingest_warnings) if idx else []
        warnings.append(f"Groundability LLM audit degraded: {reason}")
        ctx.groundability = GroundabilityReport(
            is_groundable=chunk_count > 0,
            coverage_score=coverage,
            covered_topics=ctx.analysis.key_topics if ctx.analysis else [],
            missing_gaps=[],
            warnings=warnings,
            detected_scripts=idx.detected_scripts if idx else ["latin"],
            recommendation="Proceeded using heuristic chunk-count groundability check.",
        )
        return "Heuristic chunk-count GroundabilityReport fallback"
