"""Bonus #2: Factory vs. Human Baseline head-to-head evaluation engine."""

from __future__ import annotations

from typing import Literal

from forgeai.core.runtime.agent import ForgedAgentRuntime
from forgeai.core.schemas import (
    AgentSpec,
    BaselineComparisonCase,
    BaselineComparisonReport,
    EvalCase,
    ForgeArtifact,
)
from forgeai.core.stages.judge_stage import run_eval_suite
from forgeai.llm.base import BaseLLMClient


def render_comparison_markdown(report: BaselineComparisonReport) -> str:
    """Render an honest head-to-head Factory vs. Human Baseline comparison report."""
    lines = [
        f"# Factory vs. Human Baseline Comparison: {report.factory_agent_name}",
        "",
        "| Metric | Factory-Forged Agent | Hand-Written Human Baseline |",
        "| --- | --- | --- |",
        f"| Agent Name | {report.factory_agent_name} | {report.baseline_agent_name} |",
        f"| Eval Pass Rate | **{report.factory_pass_rate:.0%}** | **{report.baseline_pass_rate:.0%}** |",
        f"| Average Score | **{report.factory_avg_score:.2f}** | **{report.baseline_avg_score:.2f}** |",
        f"| Head-to-Head Wins | {report.factory_wins} | {report.baseline_wins} (ties: {report.ties}) |",
        "",
        "## Where the Factory Beats the Human Baseline",
        "",
        *([f"- {item}" for item in report.where_factory_wins] or ["- No strict wins on this eval suite."]),
        "",
        "## Where the Human Baseline Beats (or Matches) the Factory",
        "",
        *([f"- {item}" for item in report.where_baseline_wins] or ["- Factory matched or beat baseline on all automated metrics; human prompt remains easier to read and tailor for nuanced corporate tone."]),
        "",
        "## Case-by-Case Breakdown",
        "",
        "| Case | Category | Factory | Baseline | Winner | Factory Notes | Baseline Notes |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for c in report.cases:
        f_str = f"{'PASS' if c.factory_passed else 'FAIL'} ({c.factory_score:.2f})"
        b_str = f"{'PASS' if c.baseline_passed else 'FAIL'} ({c.baseline_score:.2f})"
        lines.append(
            f"| `{c.case_id}` | `{c.category}` | {f_str} | {b_str} | **{c.winner.upper()}** | "
            f"{c.factory_notes} | {c.baseline_notes} |"
        )
    lines.append("")
    return "\n".join(lines)


def compare_factory_vs_baseline(
    *,
    factory_artifact: ForgeArtifact,
    baseline_spec: AgentSpec,
    llm: BaseLLMClient,
    eval_cases: list[EvalCase] | None = None,
) -> BaselineComparisonReport:
    """Evaluate both a factory-forged agent and a hand-written human baseline on identical evals."""
    cases = list(eval_cases or factory_artifact.eval_cases)
    if not cases:
        cases = [
            EvalCase(
                id=r.case_id,
                category=r.category,
                question=r.question,
                expect_refusal=r.category in {"safety_injection", "ungroundable_refusal"},
            )
            for r in factory_artifact.eval_report.results
        ]

    factory_rt = ForgedAgentRuntime.from_artifact(factory_artifact, llm)
    baseline_rt = ForgedAgentRuntime(
        spec=baseline_spec,
        knowledge_index=factory_artifact.knowledge_index,
        llm=llm,
    )

    factory_eval = run_eval_suite(cases, factory_rt, iteration=1)
    baseline_eval = run_eval_suite(cases, baseline_rt, iteration=1)

    case_rows: list[BaselineComparisonCase] = []
    factory_wins = 0
    baseline_wins = 0
    ties = 0
    where_factory: list[str] = []
    where_baseline: list[str] = []

    for f_res, b_res in zip(factory_eval.results, baseline_eval.results, strict=False):
        winner: Literal["factory", "baseline", "tie"]
        if f_res.score > b_res.score or (f_res.passed and not b_res.passed):
            winner = "factory"
            factory_wins += 1
            where_factory.append(
                f"`{f_res.case_id}` ({f_res.category}): Factory scored {f_res.score:.2f} vs "
                f"baseline {b_res.score:.2f} ({b_res.notes})."
            )
        elif b_res.score > f_res.score or (b_res.passed and not f_res.passed):
            winner = "baseline"
            baseline_wins += 1
            where_baseline.append(
                f"`{b_res.case_id}` ({b_res.category}): Human baseline scored {b_res.score:.2f} vs "
                f"factory {f_res.score:.2f} ({f_res.notes})."
            )
        else:
            winner = "tie"
            ties += 1

        case_rows.append(
            BaselineComparisonCase(
                case_id=f_res.case_id,
                category=f_res.category,
                question=f_res.question,
                factory_passed=f_res.passed,
                baseline_passed=b_res.passed,
                factory_score=f_res.score,
                baseline_score=b_res.score,
                winner=winner,
                factory_notes=f_res.notes,
                baseline_notes=b_res.notes,
            )
        )

    # Note qualitative tradeoffs honestly even when scores tie
    if len(baseline_spec.system_prompt) < len(factory_artifact.agent_spec.system_prompt):
        where_baseline.append(
            f"Prompt conciseness: Human prompt is {len(baseline_spec.system_prompt)} chars vs "
            f"factory's {len(factory_artifact.agent_spec.system_prompt)} chars (lower per-turn prompt token cost)."
        )
    if factory_artifact.agent_spec.must_refuse_topics and not baseline_spec.must_refuse_topics:
        where_factory.append(
            "Explicit must-refuse guardrails: Factory automatically attached groundability-derived "
            f"{len(factory_artifact.agent_spec.must_refuse_topics)} must-refuse topic(s)."
        )

    report = BaselineComparisonReport(
        factory_agent_name=factory_artifact.agent_spec.name,
        baseline_agent_name=baseline_spec.name,
        total_cases=len(case_rows),
        factory_pass_rate=factory_eval.pass_rate,
        baseline_pass_rate=baseline_eval.pass_rate,
        factory_avg_score=factory_eval.average_score,
        baseline_avg_score=baseline_eval.average_score,
        factory_wins=factory_wins,
        baseline_wins=baseline_wins,
        ties=ties,
        cases=case_rows,
        where_factory_wins=where_factory,
        where_baseline_wins=where_baseline,
    )
    report.report_markdown = render_comparison_markdown(report)
    return report
