"""Stage 7 (report): compile honest Scorecard, report.md (including what still fails & change log), and ForgeArtifact."""

from __future__ import annotations

from forgeai.core.schemas import ForgeArtifact, RunStatus, Scorecard
from forgeai.core.stages.base import BaseStage, StageContext


def render_report_markdown(artifact: ForgeArtifact) -> str:
    """Render an honest, auditable Markdown run report stating what works, what changed, and what still fails."""
    sc = artifact.scorecard
    spec = artifact.agent_spec
    req = artifact.requirements
    rc = spec.retrieval_config
    oc = spec.output_contract

    failed_results = [r for r in artifact.eval_report.results if not r.passed]
    cat_counts: dict[str, int] = {}
    for c in artifact.eval_cases:
        cat_counts[c.category] = cat_counts.get(c.category, 0) + 1

    lines: list[str] = [
        f"# ForgeAI Run Report: {spec.name}",
        "",
        f"- **Run ID**: `{artifact.run_id}`",
        f"- **Status**: `{artifact.status}`",
        f"- **Created At**: `{artifact.created_at}`",
        f"- **Domain**: {spec.domain}",
        f"- **Goal**: {artifact.goal}",
        f"- **Model**: `{sc.provider}:{sc.model}` (version `{sc.model_version}`, seed `{sc.seed}`)",
        f"- **Config Hash**: `{sc.config_hash}`",
        f"- **Human Approved Requirements**: {'Yes' if req.approved_by_human else 'No (Unattended)'}",
        f"- **Tools Enabled**: `{', '.join(spec.enabled_tools)}`",
        f"- **Retrieval Config**: `chunk_size={rc.chunk_size}`, `overlap={rc.chunk_overlap}`, `top_k={rc.top_k}`, `bm25={rc.bm25_weight}`, `cosine={rc.cosine_weight}`, `min_score={rc.min_score_threshold}`",
        f"- **Output Contract**: `format={oc.format}`, `require_citations={oc.require_citations}`, `max_words={oc.max_words}`",
        "",
        "## Scorecard Summary",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| Pass Rate | {sc.pass_rate:.0%} ({artifact.eval_report.passed_cases}/{artifact.eval_report.total_cases}) |",
        f"| Average Score | {sc.average_score:.2f} |",
        f"| Direct Injection Defense | {'PASS' if sc.safety_passed else 'FAIL'} |",
        f"| Indirect Doc Injection Defense | {'PASS' if sc.doc_injection_passed else 'FAIL'} |",
        f"| Groundability Coverage | {sc.groundability_score:.0%} |",
        f"| Eval Suite Size | {len(artifact.eval_cases)} cases ({', '.join(f'{k}={v}' for k, v in sorted(cat_counts.items()))}) |",
        f"| Loops Executed | {sc.loops_executed} (best: loop {sc.best_iteration}) |",
        f"| Total Tokens | {sc.total_tokens} |",
        f"| Total Latency | {sc.total_latency_ms:.1f} ms |",
        f"| Estimated Cost | ${sc.total_cost_usd:.6f} |",
        "",
        "## Honest Quality Assessment: What Still Fails & Why",
        "",
    ]

    if failed_results:
        lines.append(
            f"**{len(failed_results)} of {artifact.eval_report.total_cases} evaluation case(s) still fail** "
            f"after {sc.loops_executed} iteration(s). Breakdown by failure taxonomy:"
        )
        lines.append("")
        lines.extend(
            [
                "| Failure Category | Count |",
                "| --- | --- |",
            ]
        )
        for fcat in ("retrieval_miss", "prompt_defect", "hallucination", "wrong_refusal"):
            lines.append(f"| `{fcat}` | {artifact.eval_report.failure_breakdown.get(fcat, 0)} |")
        lines.append("")
        for fr in failed_results:
            lines.append(
                f"- **`{fr.case_id}`** (`{fr.category}` → **`{fr.failure_category}`**, score `{fr.score:.2f}`): "
                f"{fr.notes}"
            )
        lines.append("")
    else:
        lines.append(
            f"All {artifact.eval_report.total_cases} automated evaluation cases passed "
            f"(avg score `{sc.average_score:.2f}`)."
        )
        if sc.provider == "stub":
            lines.append(
                "> **Caveat (Offline Stub Mode)**: This run executed against `StubLLMClient` for "
                "deterministic CI/smoke verification. Re-run with `FORGEAI_LLM_PROVIDER=ollama` or "
                "`openai_compat` to measure real small-model failure modes and LLM-as-judge variance."
            )
        else:
            lines.append(
                "> **Residual Risk Note**: Automated evals cover grounded lookups, calculations, "
                "refusals, edge cases, and injections, but cannot guarantee unseen paraphrase or "
                "downstream policy-drift edge cases without human spot-checking."
            )
        lines.append("")

    lines.extend(
        [
            "## Structured Requirements Summary",
            "",
            f"- **Scope**: {req.scope}",
            f"- **Audience**: {req.audience}",
            f"- **Tone**: {req.tone}",
            f"- **Must-Answer Topics**: {', '.join(req.must_answer_topics) or 'none'}",
            f"- **Must-Refuse Topics**: {', '.join(req.must_refuse_topics) or 'none'}",
            f"- **Clarifying Interview Questions Recorded**: {len(req.clarifying_qa)}",
            "",
        ]
    )

    if sc.budget_exhausted_reason:
        lines.extend(
            [
                "## Budget Early Stop",
                "",
                f"> Run stopped early with status `budget_exhausted`: {sc.budget_exhausted_reason}",
                "",
            ]
        )

    if artifact.degraded_stages:
        lines.extend(["## Degraded Stages", ""])
        for d in artifact.degraded_stages:
            lines.append(
                f"- **`{d.stage}`** (failed {d.attempts}x): {d.reason} -> Fallback: *{d.fallback_used}*"
            )
        lines.append("")

    if artifact.groundability.warnings:
        lines.extend(["## Corpus & Groundability Warnings", ""])
        for w in artifact.groundability.warnings:
            lines.append(f"- {w}")
        lines.append("")

    lines.extend(
        [
            "## Critic–Optimizer Loop Progression & Change Log",
            "",
            "| Loop | Score Before → After | Pass Rate | Direct Safety | Doc Injection | Failures Addressed | What Changed | Why / Critic Summary |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for rec in artifact.loop_history:
        score_span = (
            f"{rec.score_before:.2f} → {rec.score_after:.2f}"
            if rec.score_before is not None and rec.score_after is not None
            else f"— → {rec.average_score:.2f}"
        )
        changes_str = "; ".join(rec.changes_applied) if rec.changes_applied else "Initial synthesis"
        addressed_str = ", ".join(rec.failures_addressed) if rec.failures_addressed else "—"
        lines.append(
            f"| {rec.iteration} | {score_span} | {rec.pass_rate:.0%} | "
            f"{'PASS' if rec.safety_passed else 'FAIL'} | "
            f"{'PASS' if rec.doc_injection_passed else 'FAIL'} | "
            f"{addressed_str} | {changes_str} | {rec.critic_summary} |"
        )

    lines.extend(
        [
            "",
            "## Per-Stage Token & Latency Breakdown",
            "",
            "| Stage | Calls | Prompt Tokens | Completion Tokens | Total Tokens | Latency (ms) | Errors |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for stage_name, sm in sc.stage_metrics.items():
        lines.append(
            f"| `{stage_name}` | {sm.get('calls', 0)} | {sm.get('prompt_tokens', 0)} | "
            f"{sm.get('completion_tokens', 0)} | {sm.get('total_tokens', 0)} | "
            f"{sm.get('latency_ms', 0.0):.1f} | {sm.get('errors', 0)} |"
        )

    lines.extend(["", "## Prompt Versions (Reproducibility)", ""])
    for pname, pver in sorted(sc.prompt_versions.items()):
        lines.append(f"- `{pname}`: `{pver}`")

    lines.extend(
        [
            "",
            "## Evaluation Cases",
            "",
            "| Case | Category | Result | Failure Type | Score | Tools | Notes |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for r in artifact.eval_report.results:
        lines.append(
            f"| `{r.case_id}` | `{r.category}` | {'PASS' if r.passed else 'FAIL'} | "
            f"`{r.failure_category}` | {r.score:.2f} | {', '.join(r.tools_used) or 'none'} | {r.notes} |"
        )
    lines.append("")
    return "\n".join(lines)


class ReportStage(BaseStage):
    """Compiles the final Scorecard, honest report.md, and reproducible ForgeArtifact bundle."""

    name = "report"
    start_progress = 0.88
    end_progress = 1.00

    def run(self, ctx: StageContext) -> None:
        assert ctx.knowledge_index is not None and ctx.analysis is not None
        assert ctx.requirements is not None and ctx.groundability is not None

        chosen_spec = ctx.best_agent_spec or ctx.agent_spec
        chosen_eval = ctx.best_eval_report or ctx.eval_report
        assert chosen_spec is not None and chosen_eval is not None

        metrics_snap = ctx.metrics.snapshot()
        final_status: RunStatus = "budget_exhausted" if ctx.status == "budget_exhausted" else "done"

        scorecard = Scorecard(
            run_id=ctx.run_id,
            status=final_status,
            provider=ctx.llm.provider_name,
            model=ctx.llm.model,
            model_version=ctx.llm.model_version,
            seed=ctx.llm.seed,
            config_hash=ctx.config_hash,
            prompt_versions=ctx.prompt_versions,
            loops_executed=max(1, len(ctx.loop_history)),
            best_iteration=ctx.best_iteration,
            pass_rate=chosen_eval.pass_rate,
            average_score=chosen_eval.average_score,
            safety_passed=chosen_eval.safety_passed,
            doc_injection_passed=chosen_eval.doc_injection_passed,
            groundability_score=ctx.groundability.coverage_score,
            degraded_stages=ctx.degraded_stages,
            loop_history=ctx.loop_history,
            stage_metrics=metrics_snap.get("stages", {}),
            total_tokens=metrics_snap.get("total_tokens", 0),
            total_latency_ms=metrics_snap.get("elapsed_ms", 0.0),
            total_cost_usd=metrics_snap.get("cost_usd", 0.0),
            budget_exhausted_reason=ctx.budget_exhausted_reason,
        )
        ctx.scorecard = scorecard

        artifact = ForgeArtifact(
            run_id=ctx.run_id,
            status=final_status,
            goal=ctx.request.goal,
            provider=ctx.llm.provider_name,
            model=ctx.llm.model,
            model_version=ctx.llm.model_version,
            seed=ctx.llm.seed,
            config_hash=ctx.config_hash,
            prompt_versions=ctx.prompt_versions,
            requirements=ctx.requirements,
            analysis=ctx.analysis,
            groundability=ctx.groundability,
            agent_spec=chosen_spec,
            knowledge_index=ctx.knowledge_index,
            eval_cases=ctx.eval_cases,
            eval_report=chosen_eval,
            scorecard=scorecard,
            degraded_stages=ctx.degraded_stages,
            loop_history=ctx.loop_history,
            metrics=metrics_snap,
        )
        artifact.report_markdown = render_report_markdown(artifact)
        ctx.report_markdown = artifact.report_markdown
        ctx.artifact = artifact

        ctx.emit(
            stage=self.name,
            event_type="stage_progress",
            message=f"Generated report.md and scorecard.json for '{chosen_spec.name}' (status={final_status}).",
            progress=self.end_progress,
            data={"run_id": ctx.run_id, "status": final_status, "pass_rate": chosen_eval.pass_rate},
        )
