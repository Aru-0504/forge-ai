"""Stage 2 (groundability): Core #2 guard flagging unsupported gaps & wiring them to must_refuse."""

from __future__ import annotations

import re

from forgeai.core.ingest.index import HybridKnowledgeIndex, tokenize
from forgeai.core.prompts.templates import build_groundability_messages
from forgeai.core.schemas import GroundabilityReport, KnowledgeIndexData
from forgeai.core.stages.base import BaseStage, StageContext

_CAPABILITY_SUFFIX_TOKENS: frozenset[str] = frozenset(
    {
        "lookup",
        "calc",
        "calculate",
        "calculation",
        "calculations",
        "sop",
        "sops",
        "guide",
        "guidance",
        "policy",
        "policies",
        "check",
        "query",
        "support",
        "info",
        "information",
        "retrieval",
        "search",
        "entitlement",
        "management",
        "questions",
    }
)


def _is_topic_supported_by_corpus(
    topic: str,
    index: HybridKnowledgeIndex,
    knowledge: KnowledgeIndexData,
    recommended_tools: list[str],
) -> bool:
    """Verify whether a must_answer_topic is supported by chunks, structured tables, or enabled tools.

    Prevents false positives on shorthand topic slugs like `part_lookup` or `lead_time_calc`
    whose substantive nouns (`part`, `lead`, `time`) exist in CSV headers/chunks, while still
    reliably flagging genuinely absent topics (e.g., `cryptocurrency_payroll` or `stock_options`).
    """
    query = topic.replace("_", " ").strip()
    if not query:
        return True

    # 1. Direct hybrid search on full topic text
    if index.search(query, top_k=1, min_score=0.05):
        return True

    # 2. Extract substantive content tokens (strip generic capability verbs like lookup/calc/sop)
    raw_tokens = tokenize(query)
    content_tokens = [t for t in raw_tokens if t not in _CAPABILITY_SUFFIX_TOKENS and len(t) >= 3]
    if not content_tokens:
        # Pure capability slug like "calculations" -> supported if calc/kb_search tool is enabled
        return bool(recommended_tools)

    # 3. Check structured table schemas (split snake_case/kebab-case headers & sample cells)
    table_vocab: set[str] = set()

    def _add_words(text: str) -> None:
        table_vocab.update(tokenize(text))
        table_vocab.update(re.findall(r"[a-z0-9]{2,}", text.lower()))

    for tbl in knowledge.tables:
        _add_words(tbl.source)
        for col in tbl.columns:
            _add_words(col)
        for row in tbl.rows[:30]:
            for val in row.values():
                _add_words(str(val))

    if any(tok in table_vocab for tok in content_tokens):
        return True

    # 4. Check substantive tokens directly against HybridKnowledgeIndex
    cleaned_query = " ".join(content_tokens)
    if index.search(cleaned_query, top_k=1, min_score=0.05):
        return True

    return False


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

        # Deterministic verification of every must_answer_topic against chunks + structured tables
        index = HybridKnowledgeIndex(ctx.knowledge_index)
        for topic in ctx.requirements.must_answer_topics:
            supported = _is_topic_supported_by_corpus(
                topic,
                index,
                ctx.knowledge_index,
                ctx.requirements.recommended_tools,
            )
            if not supported:
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
                max(
                    0.2,
                    1.0
                    - len(report.unsupported_requests)
                    / max(1, len(ctx.requirements.must_answer_topics) + 1),
                ),
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
