"""Ollama HTTP chat client with retry, fixed seed, and structured JSON support."""

from __future__ import annotations

import time
from typing import Any

import httpx

from forgeai.llm.base import (
    BaseLLMClient,
    LLMError,
    LLMMessage,
    LLMResponse,
    TokenUsage,
    TransientLLMError,
    estimate_tokens,
)
from forgeai.llm.retry import RetryConfig, with_retry


class OllamaLLMClient(BaseLLMClient):
    """Client for local or remote Ollama `/api/chat` endpoints."""

    provider_name = "ollama"

    def __init__(
        self,
        *,
        base_url: str = "http://localhost:11434",
        model: str = "qwen2.5:7b-instruct",
        model_version: str = "latest",
        temperature: float = 0.1,
        seed: int | None = 42,
        max_tokens: int = 2048,
        timeout_seconds: float = 60.0,
        retry_config: RetryConfig | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        super().__init__(
            model=model,
            model_version=model_version,
            temperature=temperature,
            seed=seed,
            max_tokens=max_tokens,
        )
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.retry_config = retry_config or RetryConfig()
        self._client = http_client

    def chat(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> LLMResponse:
        options: dict[str, Any] = {
            "temperature": self.temperature if temperature is None else temperature,
            "num_predict": self.max_tokens if max_tokens is None else max_tokens,
        }
        if self.seed is not None:
            options["seed"] = self.seed

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": False,
            "options": options,
        }
        if json_mode:
            payload["format"] = "json"

        def _do_request() -> tuple[dict[str, Any], float]:
            t0 = time.monotonic()
            try:
                if self._client is not None:
                    resp = self._client.post(f"{self.base_url}/api/chat", json=payload)
                else:
                    with httpx.Client(timeout=self.timeout_seconds) as client:
                        resp = client.post(f"{self.base_url}/api/chat", json=payload)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                raise TransientLLMError(f"Ollama transport error: {exc}") from exc

            elapsed_ms = (time.monotonic() - t0) * 1000.0
            if resp.status_code in {408, 429} or resp.status_code >= 500:
                raise TransientLLMError(
                    f"Ollama transient HTTP {resp.status_code}: {resp.text[:300]}"
                )
            if resp.status_code >= 400:
                raise LLMError(f"Ollama HTTP {resp.status_code}: {resp.text[:300]}")
            return resp.json(), elapsed_ms

        data, elapsed_ms = with_retry(
            _do_request,
            retry_exceptions=(TransientLLMError,),
            config=self.retry_config,
        )

        content = str(data.get("message", {}).get("content", ""))
        prompt_text = "\n".join(m.content for m in messages)
        usage = TokenUsage(
            prompt_tokens=int(data.get("prompt_eval_count") or estimate_tokens(prompt_text)),
            completion_tokens=int(data.get("eval_count") or estimate_tokens(content)),
        )
        return LLMResponse(
            content=content,
            model=str(data.get("model") or self.model),
            model_version=str(data.get("model_version") or self.model_version),
            provider=self.provider_name,
            usage=usage,
            latency_ms=elapsed_ms,
            raw=data,
        )
