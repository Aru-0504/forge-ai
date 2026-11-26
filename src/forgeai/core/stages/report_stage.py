"""Stage 7 (report): compile Scorecard, human-readable report.md, and packaged ForgeArtifact."""

from __future__ import annotations

from forgeai.core.schemas import ForgeArtifact, RunStatus, Scorecard
from forgeai.core.stages.base import BaseStage, StageContext


def render_report_markdown(artifact: ForgeArtifact) -> str:
    """Render a concise, auditable Markdown run report for engineers and reviewers."""
    sc = artifact.scorecard
    spec = artifact.agent_spec
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
        f"- **Tools Enabled**: `{', '.join(spec.enabled_tools)}`",
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
        f"| Loops Executed | {sc.loops_executed} (best: loop {sc.best_iteration}) |",
        f"| Total Tokens | {sc.total_tokens} |",
        f"| Total Latency | {sc.total_latency_ms:.1f} ms |",
        f"| Estimated Cost | ${sc.total_cost_usd:.6f} |",
        "",
    ]

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
            "## Loop Progression",
            "",
            "| Loop | Pass Rate | Avg Score | Direct Safety | Doc Injection | Critic Summary |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    for rec in artifact.loop_history:
        lines.append(
            f"| {rec.iteration} | {rec.pass_rate:.0%} | {rec.average_score:.2f} | "
            f"{'PASS' if rec.safety_passed else 'FAIL'} | "
            f"{'PASS' if rec.doc_injection_passed else 'FAIL'} | {rec.critic_summary} |"
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

    lines.extend(
        [
            "",
            "## Prompt Versions (Reproducibility)",
            "",
        ]
    )
    for pname, pver in sorted(sc.prompt_versions.items()):
        lines.append(f"- `{pname}`: `{pver}`")

    lines.extend(
        [
            "",
            "## Evaluation Cases",
            "",
            "| Case | Category | Result | Score | Tools | Notes |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    for r in artifact.eval_report.results:
        lines.append(
            f"| `{r.case_id}` | `{r.category}` | {'PASS' if r.passed else 'FAIL'} | "
            f"{r.score:.2f} | {', '.join(r.tools_used) or 'none'} | {r.notes} |"
        )
    lines.append("")
    return "\n".join(lines)


class ReportStage(BaseStage):
    """Compiles the final Scorecard, report.md, and reproducible ForgeArtifact bundle."""

    name = "report"
    start_progress = 0.88
    end_progress = 1.00

    def run(self, ctx: StageContext) -> None:
        assert ctx.knowledge_index is not None and ctx.analysis is not None
        assert ctx.groundability is not None

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
            analysis=ctx.analysis,
            groundability=ctx.groundability,
            agent_spec=chosen_spec,
            knowledge_index=ctx.knowledge_index,
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
