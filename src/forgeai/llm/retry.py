"""Retry with exponential backoff and jitter for transient LLM errors."""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class RetryConfig:
    """Configuration for exponential backoff retries."""

    max_retries: int = 3
    base_delay_seconds: float = 0.5
    max_delay_seconds: float = 15.0
    jitter_ratio: float = 0.25


def with_retry(
    fn: Callable[[], T],
    *,
    retry_exceptions: tuple[type[BaseException], ...],
    config: RetryConfig | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> T:
    """Execute `fn` with bounded exponential backoff on retryable exceptions."""
    cfg = config or RetryConfig()
    attempt = 0
    while True:
        try:
            return fn()
        except retry_exceptions:
            if attempt >= cfg.max_retries:
                raise
            delay = min(cfg.max_delay_seconds, cfg.base_delay_seconds * (2**attempt))
            jitter = delay * cfg.jitter_ratio * random.random()
            sleep_fn(delay + jitter)
            attempt += 1
