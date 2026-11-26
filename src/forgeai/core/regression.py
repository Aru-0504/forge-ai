"""Bonus #3: Regression Mode — regenerate agent on updated docs, re-run old eval set, report what broke."""

from __future__ import annotations

from typing import Literal

from forgeai.core.runtime.agent import ForgedAgentRuntime
from forgeai.core.schemas import (
    EvalCase,
    ForgeArtifact,
    ForgeRequest,
    RegressionCaseDiff,
    RegressionReport,
    SourceDocument,
)
from forgeai.core.stages.judge_stage import run_eval_suite
from forgeai.core.stages.pipeline import ForgePipeline
from forgeai.llm.base import BaseLLMClient


def render_regression_markdown(report: RegressionReport) -> str:
    """Render a clear Regression Mode diff report highlighting what broke or improved."""
    banner = "REGRESSIONS DETECTED" if report.has_regressions else "NO REGRESSIONS"
    lines = [
        f"# ForgeAI Regression Report ({banner})",
        "",
        f"- **Baseline Run**: `{report.baseline_run_id}` (pass rate: `{report.old_pass_rate:.0%}`)",
        f"- **Regenerated Run**: `{report.regenerated_run_id}` (pass rate on old evals: `{report.new_pass_rate:.0%}`)",
        f"- **Summary**: {report.summary}",
        "",
        "| Bucket | Count |",
        "| --- | --- |",
        f"| Regressed (broke) | {report.regressed_count} |",
        f"| Fixed | {report.fixed_count} |",
        f"| Still Passing | {report.still_passing_count} |",
        f"| Still Failing | {report.still_failing_count} |",
        "",
        "## Case-by-Case Diff",
        "",
        "| Case | Category | Status | Old Score | New Score | What Changed |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for d in report.diffs:
        lines.append(
            f"| `{d.case_id}` | `{d.category}` | **{d.status.upper()}** | "
            f"{d.old_score:.2f} | {d.new_score:.2f} | {d.new_notes} |"
        )
    lines.append("")
    return "\n".join(lines)


def run_regression(
    *,
    baseline_artifact: ForgeArtifact,
    updated_sources: list[SourceDocument],
    llm: BaseLLMClient,
    config_hash: str = "",
    regenerated_run_id: str | None = None,
) -> tuple[ForgeArtifact, RegressionReport]:
    """Regenerate agent from updated docs and re-run baseline's OLD eval cases to report regressions."""
    old_cases: list[EvalCase] = list(baseline_artifact.eval_cases)
    if not old_cases:
        old_cases = [
            EvalCase(
                id=r.case_id,
                category=r.category,
                question=r.question,
                expect_refusal=r.category in {"safety_injection", "ungroundable_refusal"},
            )
            for r in baseline_artifact.eval_report.results
        ]

    # 1. Regenerate agent against updated documents
    pipeline = ForgePipeline(llm, config_hash=config_hash)
    req = ForgeRequest(
        goal=baseline_artifact.goal,
        agent_name_hint=baseline_artifact.agent_spec.name,
        sources=updated_sources,
        chunk_size=baseline_artifact.agent_spec.retrieval_config.chunk_size,
        chunk_overlap=baseline_artifact.agent_spec.retrieval_config.chunk_overlap,
        max_loops=2,
    )
    regenerated = pipeline.run(req, run_id=regenerated_run_id)

    # 2. Re-run the OLD eval set against the regenerated agent
    new_runtime = ForgedAgentRuntime.from_artifact(regenerated, llm)
    new_eval = run_eval_suite(old_cases, new_runtime, iteration=1)

    old_by_id = {r.case_id: r for r in baseline_artifact.eval_report.results}
    diffs: list[RegressionCaseDiff] = []
    regressed = fixed = still_passing = still_failing = 0

    for new_res in new_eval.results:
        old_res = old_by_id.get(new_res.case_id)
        old_passed = old_res.passed if old_res else True
        old_score = old_res.score if old_res else 1.0
        old_notes = old_res.notes if old_res else ""

        status: Literal["regressed", "fixed", "still_passing", "still_failing"]
        if old_passed and not new_res.passed:
            status = "regressed"
            regressed += 1
        elif not old_passed and new_res.passed:
            status = "fixed"
            fixed += 1
        elif old_passed and new_res.passed:
            status = "still_passing"
            still_passing += 1
        else:
            status = "still_failing"
            still_failing += 1

        diffs.append(
            RegressionCaseDiff(
                case_id=new_res.case_id,
                category=new_res.category,
                question=new_res.question,
                status=status,
                old_passed=old_passed,
                new_passed=new_res.passed,
                old_score=old_score,
                new_score=new_res.score,
                old_notes=old_notes,
                new_notes=new_res.notes,
            )
        )

    summary = (
        f"Re-ran {len(diffs)} baseline eval cases on regenerated agent: "
        f"{regressed} regressed, {fixed} fixed, {still_passing} still passing, {still_failing} still failing."
    )
    report = RegressionReport(
        baseline_run_id=baseline_artifact.run_id,
        regenerated_run_id=regenerated.run_id,
        total_cases=len(diffs),
        regressed_count=regressed,
        fixed_count=fixed,
        still_passing_count=still_passing,
        still_failing_count=still_failing,
        old_pass_rate=baseline_artifact.eval_report.pass_rate,
        new_pass_rate=new_eval.pass_rate,
        has_regressions=regressed > 0,
        diffs=diffs,
        summary=summary,
    )
    report.report_markdown = render_regression_markdown(report)
    return regenerated, report
