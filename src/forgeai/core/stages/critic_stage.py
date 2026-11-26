"""Stage 6 (critic): analyze Judge failures and produce targeted prompt/tool refinements."""

from __future__ import annotations

from forgeai.core.prompts.templates import build_critic_messages
from forgeai.core.schemas import CriticFeedback
from forgeai.core.stages.base import BaseStage, StageContext


class CriticStage(BaseStage):
    """Reviews JudgeStage outcomes and determines whether to iterate or stop early."""

    name = "critic"
    start_progress = 0.80
    end_progress = 0.88

    def run(self, ctx: StageContext) -> None:
        if ctx.agent_spec is None or ctx.eval_report is None:
            raise RuntimeError("CriticStage requires AgentSpec and EvalReport.")

        report = ctx.eval_report
        # Deterministic early-stop when target threshold & both safety checks pass
        if (
            report.average_score >= ctx.request.target_score
            and report.pass_rate >= ctx.request.target_score
            and report.safety_passed
            and report.doc_injection_passed
        ):
            ctx.critic_feedback = CriticFeedback(
                should_stop=True,
                summary=(
                    f"Target threshold ({ctx.request.target_score:.0%}) and security gates satisfied "
                    f"on loop {ctx.iteration}; stopping early."
                ),
            )
            ctx.emit(
                stage=self.name,
                event_type="stage_progress",
                message=ctx.critic_feedback.summary,
                progress=self.end_progress,
                data={"iteration": ctx.iteration, "should_stop": True},
            )
            return

        messages = build_critic_messages(
            ctx.request.goal,
            ctx.agent_spec,
            report,
            iteration=ctx.iteration,
            max_loops=ctx.request.max_loops,
        )
        feedback = ctx.llm.generate_json(
            messages,
            CriticFeedback,
            stage=self.name,
            metrics=ctx.metrics,
        )
        ctx.critic_feedback = feedback
        ctx.emit(
            stage=self.name,
            event_type="stage_progress",
            message=f"[loop {ctx.iteration}] Critic: {feedback.summary}",
            progress=self.end_progress,
            data={"iteration": ctx.iteration, "should_stop": feedback.should_stop},
        )

    def fallback(self, ctx: StageContext, reason: str) -> str:
        ctx.critic_feedback = CriticFeedback(
            should_stop=True,
            summary=f"Critic stage degraded ({reason}); keeping best candidate so far.",
        )
        return "Stop-loop CriticFeedback fallback"
