"""Stage 2 (groundability): Core #2 guard flagging unsupported gaps & wiring them to must_refuse."""

from __future__ import annotations

from forgeai.core.ingest.index import HybridKnowledgeIndex
from forgeai.core.prompts.templates import build_groundability_messages
from forgeai.core.schemas import GroundabilityReport
from forgeai.core.stages.base import BaseStage, StageContext


class GroundabilityStage(BaseStage):
    """Audits corpus coverage and prevents hallucination by promoting gaps to must_refuse_topics."""

    name = "groundability"
    start_progress = 0.22
    end_progress = 0.34

    def run(self, ctx: StageContext) -> None:
        if ctx.knowledge_index is None or ctx.requirements is None:
            raise RuntimeError("GroundabilityStage requires intake outputs.")

        messages = build_groundability_messages(
            ctx.request.goal, ctx.requirements, ctx.knowledge_index
        )
        report = ctx.llm.generate_json(
            messages,
            GroundabilityReport,
            stage=self.name,
            metrics=ctx.metrics,
        )

        # Deterministic verification of every must_answer_topic against HybridKnowledgeIndex
        index = HybridKnowledgeIndex(ctx.knowledge_index)
        for topic in ctx.requirements.must_answer_topics:
            query = topic.replace("_", " ")
            hits = index.search(query, top_k=1, min_score=0.05)
            if not hits:
                if topic not in report.missing_gaps:
                    report.missing_gaps.append(topic)
                if topic not in report.unsupported_requests:
                    report.unsupported_requests.append(topic)

        report.detected_scripts = list(ctx.knowledge_index.detected_scripts)
        for w in ctx.knowledge_index.ingest_warnings:
            if w not in report.warnings:
                report.warnings.append(w)

        # Core #2: promote unsupported gaps directly into requirements.must_refuse_topics
        for gap in [*report.missing_gaps, *report.unsupported_requests]:
            clean_gap = gap.strip()
            if clean_gap and clean_gap not in ctx.requirements.must_refuse_topics:
                ctx.requirements.must_refuse_topics.append(clean_gap)
                msg = f"Groundability guard flagged unsupported topic '{clean_gap}' -> added to must_refuse_topics."
                if msg not in report.warnings:
                    report.warnings.append(msg)

        if report.unsupported_requests and report.coverage_score > 0.85:
            report.coverage_score = round(
                max(0.2, 1.0 - len(report.unsupported_requests) / max(1, len(ctx.requirements.must_answer_topics) + 1)),
                2,
            )

        ctx.groundability = report
        ctx.emit(
            stage=self.name,
            event_type="stage_progress",
            message=(
                f"Groundability audit: groundable={report.is_groundable}, "
                f"coverage={report.coverage_score:.0%}, gaps={len(report.missing_gaps)}."
            ),
            progress=self.end_progress,
            data={
                "is_groundable": report.is_groundable,
                "coverage_score": report.coverage_score,
                "missing_gaps": report.missing_gaps,
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
            covered_topics=ctx.requirements.must_answer_topics if ctx.requirements else [],
            missing_gaps=[],
            unsupported_requests=[],
            warnings=warnings,
            detected_scripts=idx.detected_scripts if idx else ["latin"],
            recommendation="Proceeded using heuristic chunk-count groundability check.",
        )
        return "Heuristic chunk-count GroundabilityReport fallback"
