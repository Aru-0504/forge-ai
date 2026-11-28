"""ForgePipeline: 7-stage iterative orchestrator with human checkpoint, timeouts, budgets, degradation."""

from __future__ import annotations

import concurrent.futures
import time
import uuid
from pathlib import Path

from forgeai.core.ingest.parsers import IngestInputError
from forgeai.core.prompts.templates import get_prompt_versions
from forgeai.core.schemas import (
    AgentSpec,
    DegradedStageRecord,
    DomainAnalysis,
    EvalCase,
    EvalReport,
    ForgeArtifact,
    ForgeRequest,
    GroundabilityReport,
    KnowledgeIndexData,
    LoopRecord,
    PromptTournamentReport,
    RequirementsDoc,
)
from forgeai.core.stages.base import (
    ApprovalCallback,
    BaseStage,
    EventCallback,
    InterviewCallback,
    StageContext,
)
from forgeai.core.stages.critic_stage import CriticStage
from forgeai.core.stages.evalgen_stage import EvalGenStage
from forgeai.core.stages.generator_stage import GeneratorStage
from forgeai.core.stages.groundability_stage import GroundabilityStage
from forgeai.core.stages.intake_stage import IntakeStage, requirements_to_analysis
from forgeai.core.stages.judge_stage import JudgeStage
from forgeai.core.stages.report_stage import ReportStage
from forgeai.llm.base import BaseLLMClient
from forgeai.observability.logging import attach_run_log_file, bind_log_context, get_logger
from forgeai.observability.metrics import BudgetExceededError, MetricsTracker, RunBudget
from forgeai.security.upload_guard import UploadValidationError
from forgeai.security.url_guard import URLValidationError

logger = get_logger("core.pipeline")

_FATAL_INPUT_ERRORS = (IngestInputError, UploadValidationError, URLValidationError)


class StageTimeoutError(TimeoutError):
    """Raised when a single pipeline stage exceeds its configured wall-clock timeout."""


class HumanApprovalRequired(Exception):
    """Control-flow signal raised when Human Checkpoint pauses a run awaiting requester approval."""

    def __init__(
        self,
        run_id: str,
        requirements: RequirementsDoc,
        groundability: GroundabilityReport,
    ) -> None:
        super().__init__(f"Run {run_id} is awaiting human approval of requirements.")
        self.run_id = run_id
        self.requirements = requirements
        self.groundability = groundability


class ForgePipeline:
    """Runs intake -> groundability -> [human checkpoint] -> evalgen -> [generator -> judge -> critic]xN -> report."""

    def __init__(
        self,
        llm: BaseLLMClient,
        *,
        budget: RunBudget | None = None,
        config_hash: str = "",
        max_upload_bytes: int = 10 * 1024 * 1024,
        max_files: int = 20,
        stage_timeout_seconds: float = 120.0,
        intake_stage: BaseStage | None = None,
        groundability_stage: BaseStage | None = None,
        evalgen_stage: BaseStage | None = None,
        generator_stage: BaseStage | None = None,
        judge_stage: BaseStage | None = None,
        critic_stage: BaseStage | None = None,
        report_stage: BaseStage | None = None,
    ) -> None:
        self.llm = llm
        self.budget = budget or RunBudget()
        self.config_hash = config_hash
        self.max_upload_bytes = max_upload_bytes
        self.max_files = max_files
        self.stage_timeout_seconds = stage_timeout_seconds

        self.intake_stage = intake_stage or IntakeStage()
        self.groundability_stage = groundability_stage or GroundabilityStage()
        self.evalgen_stage = evalgen_stage or EvalGenStage()
        self.generator_stage = generator_stage or GeneratorStage()
        self.judge_stage = judge_stage or JudgeStage()
        self.critic_stage = critic_stage or CriticStage()
        self.report_stage = report_stage or ReportStage()

    def _run_with_timeout(self, stage: BaseStage, ctx: StageContext) -> None:
        # Interactive callbacks must execute on the calling thread (for stdin prompts)
        if ctx.stage_timeout_seconds <= 0 or (
            stage.name == "intake" and ctx.interview_callback is not None
        ):
            stage.run(ctx)
            return
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(stage.run, ctx)
            try:
                future.result(timeout=ctx.stage_timeout_seconds)
            except concurrent.futures.TimeoutError as exc:
                raise StageTimeoutError(
                    f"Stage '{stage.name}' timed out after {ctx.stage_timeout_seconds:.1f}s."
                ) from exc

    def _execute_stage(self, stage: BaseStage, ctx: StageContext, *, allow_degrade: bool = True) -> None:
        with bind_log_context(stage=stage.name, loop=ctx.iteration):
            t0 = time.monotonic()
            ctx.emit(
                stage=stage.name,
                event_type="stage_started",
                message=f"Starting stage '{stage.name}'",
                progress=stage.start_progress,
                data={"iteration": ctx.iteration},
            )
            ctx.metrics.check_budget()

            last_exc: Exception | None = None
            max_attempts = 2 if allow_degrade else 1
            for attempt in range(1, max_attempts + 1):
                try:
                    self._run_with_timeout(stage, ctx)
                    elapsed_ms = (time.monotonic() - t0) * 1000.0
                    ctx.metrics.record_stage_latency(stage.name, elapsed_ms)
                    ctx.emit(
                        stage=stage.name,
                        event_type="stage_completed",
                        message=f"Completed stage '{stage.name}' in {elapsed_ms:.1f} ms",
                        progress=stage.end_progress,
                        data={"latency_ms": round(elapsed_ms, 2), "iteration": ctx.iteration},
                    )
                    return
                except BudgetExceededError:
                    raise
                except _FATAL_INPUT_ERRORS:
                    raise
                except Exception as exc:
                    last_exc = exc
                    logger.warning(
                        "Stage %s attempt %d/%d failed: %s",
                        stage.name,
                        attempt,
                        max_attempts,
                        exc,
                    )
                    if attempt < max_attempts:
                        continue

            reason = str(last_exc)
            fallback_desc = stage.fallback(ctx, reason)
            elapsed_ms = (time.monotonic() - t0) * 1000.0
            ctx.metrics.record_stage_latency(stage.name, elapsed_ms, error=True)
            rec = DegradedStageRecord(
                stage=stage.name,
                attempts=max_attempts,
                reason=reason,
                fallback_used=fallback_desc,
            )
            ctx.degraded_stages.append(rec)
            ctx.emit(
                stage=stage.name,
                event_type="stage_degraded",
                message=f"Stage '{stage.name}' failed {max_attempts}x and degraded gracefully: {fallback_desc}",
                progress=stage.end_progress,
                data=rec.model_dump(),
            )

    def _restore_checkpoints(self, ctx: StageContext) -> set[str]:
        done: set[str] = set()
        intake_cp = ctx.load_checkpoint("intake")
        if intake_cp:
            ctx.knowledge_index = KnowledgeIndexData.model_validate(intake_cp["knowledge_index"])
            if "requirements" in intake_cp:
                ctx.requirements = RequirementsDoc.model_validate(intake_cp["requirements"])
                ctx.analysis = requirements_to_analysis(ctx.requirements)
            else:
                ctx.analysis = DomainAnalysis.model_validate(intake_cp["analysis"])
                ctx.requirements = RequirementsDoc(
                    domain=ctx.analysis.domain,
                    scope=ctx.analysis.summary,
                    audience=", ".join(ctx.analysis.target_users) or "Operators",
                    must_answer_topics=ctx.analysis.key_topics,
                    recommended_tools=ctx.analysis.recommended_tools,
                    risk_controls=ctx.analysis.risk_controls,
                    assumptions=ctx.analysis.assumptions,
                )
            done.add("intake")

        ground_cp = ctx.load_checkpoint("groundability")
        if ground_cp:
            ctx.groundability = GroundabilityReport.model_validate(ground_cp)
            done.add("groundability")

        evalgen_cp = ctx.load_checkpoint("evalgen")
        if evalgen_cp:
            ctx.eval_cases = [EvalCase.model_validate(c) for c in evalgen_cp["cases"]]
            done.add("evalgen")

        loop_cp = ctx.load_checkpoint("best_loop")
        if loop_cp:
            ctx.best_agent_spec = AgentSpec.model_validate(loop_cp["agent_spec"])
            ctx.best_eval_report = EvalReport.model_validate(loop_cp["eval_report"])
            ctx.best_iteration = int(loop_cp["best_iteration"])
            ctx.loop_history = [LoopRecord.model_validate(r) for r in loop_cp["loop_history"]]
            if loop_cp.get("prompt_tournament"):
                ctx.prompt_tournament = PromptTournamentReport.model_validate(
                    loop_cp["prompt_tournament"]
                )
            done.add("loops")

        return done

    def run(
        self,
        request: ForgeRequest,
        *,
        run_id: str | None = None,
        on_event: EventCallback | None = None,
        interview_callback: InterviewCallback | None = None,
        approval_callback: ApprovalCallback | None = None,
        checkpoint_dir: Path | None = None,
        log_file: Path | None = None,
    ) -> ForgeArtifact:
        """Execute the 7-stage iterative pipeline and return the packaged ForgeArtifact."""
        rid = run_id or f"run_{uuid.uuid4().hex[:12]}"
        metrics = MetricsTracker(run_id=rid, budget=self.budget)
        emit_fn: EventCallback = on_event or (lambda _evt: None)

        ctx = StageContext(
            run_id=rid,
            request=request,
            llm=self.llm,
            metrics=metrics,
            emit_event=emit_fn,
            interview_callback=interview_callback,
            approval_callback=approval_callback,
            config_hash=self.config_hash,
            prompt_versions=get_prompt_versions(),
            max_upload_bytes=self.max_upload_bytes,
            max_files=self.max_files,
            stage_timeout_seconds=self.stage_timeout_seconds,
            checkpoint_dir=checkpoint_dir,
        )

        if log_file is not None:
            with attach_run_log_file(rid, log_file):
                return self._run_body(ctx)
        return self._run_body(ctx)

    def _run_body(self, ctx: StageContext) -> ForgeArtifact:
        with bind_log_context(run_id=ctx.run_id):
            logger.info("Starting ForgeAI 7-stage pipeline run")
            ctx.emit(
                stage="pipeline",
                event_type="run_started",
                message=f"Started ForgeAI run {ctx.run_id}",
                progress=0.0,
                data={
                    "goal": ctx.request.goal,
                    "source_count": len(ctx.request.sources),
                    "max_loops": ctx.request.max_loops,
                    "require_human_approval": ctx.request.require_human_approval,
                },
            )

            completed = self._restore_checkpoints(ctx)
            if completed:
                logger.info("Resuming run %s with completed checkpoints: %s", ctx.run_id, sorted(completed))

            try:
                # Stage 1: Intake (Ingest + <=5 clarifying questions + RequirementsDoc)
                if "intake" not in completed:
                    self._execute_stage(self.intake_stage, ctx)
                    assert (
                        ctx.knowledge_index is not None
                        and ctx.requirements is not None
                        and ctx.analysis is not None
                    )
                    ctx.save_checkpoint(
                        "intake",
                        {
                            "knowledge_index": ctx.knowledge_index.model_dump(),
                            "requirements": ctx.requirements.model_dump(),
                            "analysis": ctx.analysis.model_dump(),
                        },
                    )

                # Stage 2: Groundability Guard
                if "groundability" not in completed:
                    self._execute_stage(self.groundability_stage, ctx)
                    assert ctx.groundability is not None and ctx.requirements is not None
                    ctx.analysis = requirements_to_analysis(ctx.requirements)
                    ctx.save_checkpoint("groundability", ctx.groundability.model_dump())
                    # Persist updated requirements (now containing any groundability gaps in must_refuse)
                    assert ctx.knowledge_index is not None
                    ctx.save_checkpoint(
                        "intake",
                        {
                            "knowledge_index": ctx.knowledge_index.model_dump(),
                            "requirements": ctx.requirements.model_dump(),
                            "analysis": ctx.analysis.model_dump(),
                        },
                    )

                # Bonus #1: Human Checkpoint before agent generation proceeds
                assert ctx.requirements is not None and ctx.groundability is not None
                if ctx.request.require_human_approval and not ctx.requirements.approved_by_human:
                    if ctx.approval_callback is not None:
                        edited = ctx.approval_callback(ctx.requirements, ctx.groundability)
                        edited.approved_by_human = True
                        ctx.requirements = edited
                        ctx.analysis = requirements_to_analysis(edited)
                        assert ctx.knowledge_index is not None
                        ctx.save_checkpoint(
                            "intake",
                            {
                                "knowledge_index": ctx.knowledge_index.model_dump(),
                                "requirements": ctx.requirements.model_dump(),
                                "analysis": ctx.analysis.model_dump(),
                            },
                        )
                    else:
                        ctx.status = "awaiting_approval"
                        ctx.emit(
                            stage="groundability",
                            event_type="awaiting_approval",
                            message=(
                                "Paused at Human Checkpoint: review/edit requirements.json and "
                                "call POST /runs/{id}/approve to continue generation."
                            ),
                            progress=0.35,
                            data={
                                "requirements": ctx.requirements.model_dump(),
                                "groundability": ctx.groundability.model_dump(),
                            },
                        )
                        raise HumanApprovalRequired(
                            ctx.run_id, ctx.requirements, ctx.groundability
                        )

                # Stage 3: EvalGen
                if "evalgen" not in completed:
                    self._execute_stage(self.evalgen_stage, ctx)
                    ctx.save_checkpoint(
                        "evalgen",
                        {"cases": [c.model_dump() for c in ctx.eval_cases]},
                    )

                # Stages 4-6: Iterative loop (generator/patch -> judge -> critic, up to max_loops)
                if "loops" not in completed:
                    prev_avg_score: float | None = None
                    for loop_idx in range(1, ctx.request.max_loops + 1):
                        ctx.iteration = loop_idx
                        self._execute_stage(self.generator_stage, ctx)
                        self._execute_stage(self.judge_stage, ctx)

                        assert ctx.agent_spec is not None and ctx.eval_report is not None
                        score_before = prev_avg_score
                        score_after = ctx.eval_report.average_score

                        if (
                            ctx.best_eval_report is None
                            or ctx.eval_report.average_score >= ctx.best_eval_report.average_score
                        ):
                            ctx.best_agent_spec = ctx.agent_spec
                            ctx.best_eval_report = ctx.eval_report
                            ctx.best_iteration = loop_idx

                        self._execute_stage(self.critic_stage, ctx)
                        assert ctx.critic_feedback is not None

                        # Diminishing-returns early stop (budget stretch goal): halt if loop 2+ gained < 0.01
                        diminishing_stop = (
                            loop_idx >= 2
                            and score_before is not None
                            and (score_after - score_before) < 0.01
                            and ctx.eval_report.safety_passed
                            and ctx.eval_report.doc_injection_passed
                        )
                        critic_summary = ctx.critic_feedback.summary
                        if diminishing_stop and not ctx.critic_feedback.should_stop:
                            critic_summary = (
                                f"{critic_summary} [Early stop: diminishing returns "
                                f"({score_before:.2f} -> {score_after:.2f})]"
                            )

                        ctx.loop_history.append(
                            LoopRecord(
                                iteration=loop_idx,
                                pass_rate=ctx.eval_report.pass_rate,
                                average_score=ctx.eval_report.average_score,
                                safety_passed=ctx.eval_report.safety_passed,
                                doc_injection_passed=ctx.eval_report.doc_injection_passed,
                                failure_breakdown=dict(ctx.eval_report.failure_breakdown),
                                changes_applied=list(ctx.last_changes_applied),
                                change_rationale=ctx.last_change_rationale,
                                failures_addressed=list(ctx.last_failures_addressed),
                                score_before=score_before,
                                score_after=score_after,
                                critic_summary=critic_summary,
                            )
                        )
                        prev_avg_score = score_after

                        assert ctx.best_agent_spec is not None and ctx.best_eval_report is not None
                        ctx.save_checkpoint(
                            "best_loop",
                            {
                                "agent_spec": ctx.best_agent_spec.model_dump(),
                                "eval_report": ctx.best_eval_report.model_dump(),
                                "best_iteration": ctx.best_iteration,
                                "loop_history": [r.model_dump() for r in ctx.loop_history],
                                "prompt_tournament": ctx.prompt_tournament.model_dump()
                                if ctx.prompt_tournament
                                else None,
                            },
                        )

                        if ctx.critic_feedback.should_stop or diminishing_stop:
                            break

            except HumanApprovalRequired:
                raise
            except BudgetExceededError as exc:
                logger.warning("Run %s hit budget limit: %s", ctx.run_id, exc)
                ctx.status = "budget_exhausted"
                ctx.budget_exhausted_reason = str(exc)
                ctx.emit(
                    stage="pipeline",
                    event_type="budget_exhausted",
                    message=f"Budget exhausted; stopping early and generating partial report: {exc}",
                    progress=0.90,
                    data={"reason": exc.reason},
                )
                if ctx.knowledge_index is None:
                    ctx.knowledge_index = KnowledgeIndexData()
                if ctx.requirements is None or ctx.analysis is None:
                    self.intake_stage.fallback(ctx, str(exc))
                if ctx.groundability is None:
                    self.groundability_stage.fallback(ctx, str(exc))
                if ctx.agent_spec is None and ctx.best_agent_spec is None:
                    self.generator_stage.fallback(ctx, str(exc))
                if ctx.eval_report is None and ctx.best_eval_report is None:
                    self.judge_stage.fallback(ctx, str(exc))
            except Exception as exc:
                logger.exception("ForgeAI pipeline failed cleanly: %s", exc)
                ctx.status = "failed"
                ctx.emit(
                    stage="pipeline",
                    event_type="run_failed",
                    message=str(exc),
                    progress=1.0,
                    data={"error": str(exc), "error_type": type(exc).__name__},
                )
                raise

            # Stage 7: Report
            with bind_log_context(stage=self.report_stage.name):
                t0 = time.monotonic()
                ctx.emit(
                    stage=self.report_stage.name,
                    event_type="stage_started",
                    message="Starting stage 'report'",
                    progress=self.report_stage.start_progress,
                )
                self.report_stage.run(ctx)
                elapsed_ms = (time.monotonic() - t0) * 1000.0
                ctx.metrics.record_stage_latency(self.report_stage.name, elapsed_ms)
                ctx.emit(
                    stage=self.report_stage.name,
                    event_type="stage_completed",
                    message=f"Completed stage 'report' in {elapsed_ms:.1f} ms",
                    progress=self.report_stage.end_progress,
                    data={"latency_ms": round(elapsed_ms, 2)},
                )

            assert ctx.artifact is not None
            ctx.artifact.metrics = ctx.metrics.snapshot()
            ctx.artifact.scorecard.stage_metrics = ctx.artifact.metrics.get("stages", {})
            ctx.artifact.scorecard.total_latency_ms = ctx.artifact.metrics.get("elapsed_ms", 0.0)

            ctx.emit(
                stage="pipeline",
                event_type="run_completed",
                message=(
                    f"Forged agent '{ctx.artifact.agent_spec.name}' "
                    f"(status={ctx.artifact.status}, pass_rate={ctx.artifact.eval_report.pass_rate:.0%})."
                ),
                progress=1.0,
                data={
                    "status": ctx.artifact.status,
                    "agent_name": ctx.artifact.agent_spec.name,
                    "eval_pass_rate": ctx.artifact.eval_report.pass_rate,
                    "degraded_stages": [d.stage for d in ctx.degraded_stages],
                },
            )
            logger.info("Completed ForgeAI run %s with status=%s", ctx.run_id, ctx.artifact.status)
            return ctx.artifact
