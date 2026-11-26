"""Token accounting, latency tracking, and token/latency/cost budget guardrails."""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any


class BudgetExceededError(RuntimeError):
    """Raised when a run exceeds its configured token, latency, or USD cost budget."""

    def __init__(self, message: str, *, reason: str = "budget") -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class RunBudget:
    """Per-run token, wall-clock latency, and USD cost limits."""

    max_tokens: int = 100_000
    max_latency_seconds: float = 600.0
    max_cost_usd: float = 2.0
    input_cost_per_1k: float = 0.0005
    output_cost_per_1k: float = 0.0015

    def estimate_cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        return (prompt_tokens / 1000.0) * self.input_cost_per_1k + (
            completion_tokens / 1000.0
        ) * self.output_cost_per_1k


@dataclass
class StageMetric:
    """Aggregated telemetry for one pipeline stage or runtime component."""

    stage: str
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    errors: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "calls": self.calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "cost_usd": round(self.cost_usd, 6),
            "latency_ms": round(self.latency_ms, 2),
            "errors": self.errors,
        }


class MetricsTracker:
    """Thread-safe per-run metrics & budget enforcer (tokens, latency, USD cost)."""

    def __init__(self, run_id: str, budget: RunBudget | None = None) -> None:
        self.run_id = run_id
        self.budget = budget or RunBudget()
        self.started_at = time.monotonic()
        self._lock = threading.Lock()
        self._stages: dict[str, StageMetric] = {}
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.cost_usd = 0.0
        self.llm_calls = 0
        self.tool_calls = 0
        self.errors = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    @property
    def elapsed_ms(self) -> float:
        return (time.monotonic() - self.started_at) * 1000.0

    @property
    def elapsed_seconds(self) -> float:
        return time.monotonic() - self.started_at

    def check_budget(self) -> None:
        """Raise BudgetExceededError if token, latency, or cost ceilings are breached."""
        if self.total_tokens > self.budget.max_tokens:
            raise BudgetExceededError(
                f"Run {self.run_id} exceeded token budget: "
                f"{self.total_tokens} > {self.budget.max_tokens}",
                reason="token_budget",
            )
        if self.elapsed_seconds > self.budget.max_latency_seconds:
            raise BudgetExceededError(
                f"Run {self.run_id} exceeded latency budget: "
                f"{self.elapsed_seconds:.2f}s > {self.budget.max_latency_seconds:.2f}s",
                reason="latency_budget",
            )
        if self.cost_usd > self.budget.max_cost_usd:
            raise BudgetExceededError(
                f"Run {self.run_id} exceeded cost budget: "
                f"${self.cost_usd:.4f} > ${self.budget.max_cost_usd:.4f}",
                reason="cost_budget",
            )

    def record_llm_call(
        self,
        *,
        stage: str,
        prompt_tokens: int,
        completion_tokens: int,
        latency_ms: float,
        error: bool = False,
    ) -> None:
        cost = self.budget.estimate_cost(prompt_tokens, completion_tokens)
        with self._lock:
            self.llm_calls += 1
            self.prompt_tokens += prompt_tokens
            self.completion_tokens += completion_tokens
            self.cost_usd += cost
            if error:
                self.errors += 1

            sm = self._stages.get(stage)
            if sm is None:
                sm = StageMetric(stage=stage)
                self._stages[stage] = sm
            sm.calls += 1
            sm.prompt_tokens += prompt_tokens
            sm.completion_tokens += completion_tokens
            sm.cost_usd += cost
            sm.latency_ms += latency_ms
            if error:
                sm.errors += 1

            self.check_budget()

    def record_stage_latency(self, stage: str, latency_ms: float, *, error: bool = False) -> None:
        with self._lock:
            sm = self._stages.get(stage)
            if sm is None:
                sm = StageMetric(stage=stage)
                self._stages[stage] = sm
            sm.latency_ms += latency_ms
            if error:
                sm.errors += 1
                self.errors += 1

    def record_tool_call(self) -> None:
        with self._lock:
            self.tool_calls += 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "run_id": self.run_id,
                "elapsed_ms": round(self.elapsed_ms, 2),
                "llm_calls": self.llm_calls,
                "tool_calls": self.tool_calls,
                "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens,
                "total_tokens": self.total_tokens,
                "cost_usd": round(self.cost_usd, 6),
                "errors": self.errors,
                "budget": {
                    "max_tokens": self.budget.max_tokens,
                    "max_latency_seconds": self.budget.max_latency_seconds,
                    "max_cost_usd": self.budget.max_cost_usd,
                    "remaining_tokens": max(0, self.budget.max_tokens - self.total_tokens),
                    "remaining_latency_seconds": round(
                        max(0.0, self.budget.max_latency_seconds - self.elapsed_seconds), 2
                    ),
                    "remaining_cost_usd": round(
                        max(0.0, self.budget.max_cost_usd - self.cost_usd), 6
                    ),
                },
                "stages": {k: v.to_dict() for k, v in self._stages.items()},
            }


@dataclass
class MetricsRegistry:
    """Process-wide metrics registry for the HTTP API `/metrics` endpoint."""

    _lock: threading.Lock = field(default_factory=threading.Lock)
    counters: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    totals: dict[str, float] = field(default_factory=lambda: defaultdict(float))

    def inc(self, name: str, value: int = 1) -> None:
        with self._lock:
            self.counters[name] += value

    def add(self, name: str, value: float) -> None:
        with self._lock:
            self.totals[name] += value

    def record_run(self, tracker: MetricsTracker, *, status: str) -> None:
        snap = tracker.snapshot()
        with self._lock:
            self.counters[f"forge_runs_{status}"] += 1
            self.counters["llm_calls_total"] += snap["llm_calls"]
            self.counters["tool_calls_total"] += snap["tool_calls"]
            self.counters["tokens_prompt_total"] += snap["prompt_tokens"]
            self.counters["tokens_completion_total"] += snap["completion_tokens"]
            self.totals["cost_usd_total"] += snap["cost_usd"]
            self.totals["run_latency_ms_total"] += snap["elapsed_ms"]

    def export_dict(self) -> dict[str, Any]:
        with self._lock:
            return {
                "counters": dict(self.counters),
                "totals": {k: round(v, 6) for k, v in self.totals.items()},
            }

    def export_prometheus(self) -> str:
        lines: list[str] = []
        with self._lock:
            for key, count_val in sorted(self.counters.items()):
                lines.append(f"forgeai_{key} {count_val}")
            for key, total_val in sorted(self.totals.items()):
                lines.append(f"forgeai_{key} {total_val:.6f}")
        return "\n".join(lines) + ("\n" if lines else "")
