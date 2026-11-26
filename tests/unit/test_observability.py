"""Unit tests for forgeai.observability: structured logging, metrics, and budget limits."""

from __future__ import annotations

import json
import logging

import pytest

from forgeai.observability.logging import JsonLogFormatter, bind_log_context
from forgeai.observability.metrics import (
    BudgetExceededError,
    MetricsRegistry,
    MetricsTracker,
    RunBudget,
)


def test_json_log_formatter_includes_bound_context() -> None:
    formatter = JsonLogFormatter()
    record = logging.LogRecord(
        name="forgeai.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="Stage running",
        args=(),
        exc_info=None,
    )
    with bind_log_context(run_id="run_abc", stage="analyze"):
        raw = formatter.format(record)
    parsed = json.loads(raw)
    assert parsed["run_id"] == "run_abc"
    assert parsed["stage"] == "analyze"
    assert parsed["msg"] == "Stage running"


def test_metrics_tracker_records_usage_and_enforces_token_budget() -> None:
    tracker = MetricsTracker(
        run_id="run_1",
        budget=RunBudget(max_tokens=500, max_cost_usd=1.0),
    )
    tracker.record_llm_call(stage="analyze", prompt_tokens=150, completion_tokens=50, latency_ms=12.5)
    tracker.record_tool_call()
    snap = tracker.snapshot()
    assert snap["total_tokens"] == 200
    assert snap["tool_calls"] == 1
    assert snap["stages"]["analyze"]["calls"] == 1

    with pytest.raises(BudgetExceededError):
        tracker.record_llm_call(stage="design", prompt_tokens=300, completion_tokens=100, latency_ms=10.0)


def test_metrics_tracker_enforces_cost_budget() -> None:
    tracker = MetricsTracker(
        run_id="run_2",
        budget=RunBudget(
            max_tokens=100_000,
            max_cost_usd=0.01,
            input_cost_per_1k=0.01,
            output_cost_per_1k=0.02,
        ),
    )
    with pytest.raises(BudgetExceededError):
        tracker.record_llm_call(stage="design", prompt_tokens=1000, completion_tokens=500, latency_ms=5.0)


def test_metrics_registry_exports_prometheus_text() -> None:
    reg = MetricsRegistry()
    reg.inc("forge_jobs_enqueued", 2)
    reg.add("cost_usd_total", 0.125)
    text = reg.export_prometheus()
    assert "forgeai_forge_jobs_enqueued 2" in text
    assert "forgeai_cost_usd_total 0.125000" in text
