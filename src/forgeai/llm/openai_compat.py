"""OpenAI-compatible `/chat/completions` HTTP client with retry, seed, and JSON mode."""

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


class OpenAICompatLLMClient(BaseLLMClient):
    """Client for OpenAI, Groq, Together, OpenRouter, Fireworks, vLLM, or LiteLLM endpoints."""

    provider_name = "openai_compat"

    def __init__(
        self,
        *,
        base_url: str = "https://api.openai.com/v1",
        api_key: str = "",
        model: str = "qwen/qwen-2.5-7b-instruct",
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
        self.api_key = api_key
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
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": self.temperature if temperature is None else temperature,
            "max_tokens": self.max_tokens if max_tokens is None else max_tokens,
        }
        if self.seed is not None:
            payload["seed"] = self.seed
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        def _do_request() -> tuple[dict[str, Any], float]:
            t0 = time.monotonic()
            try:
                if self._client is not None:
                    resp = self._client.post(
                        f"{self.base_url}/chat/completions",
                        headers=headers,
                        json=payload,
                    )
                else:
                    with httpx.Client(timeout=self.timeout_seconds) as client:
                        resp = client.post(
                            f"{self.base_url}/chat/completions",
                            headers=headers,
                            json=payload,
                        )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                raise TransientLLMError(f"OpenAI-compat transport error: {exc}") from exc

            elapsed_ms = (time.monotonic() - t0) * 1000.0
            if resp.status_code in {408, 429} or resp.status_code >= 500:
                raise TransientLLMError(
                    f"OpenAI-compat transient HTTP {resp.status_code}: {resp.text[:300]}"
                )
            if resp.status_code >= 400:
                raise LLMError(f"OpenAI-compat HTTP {resp.status_code}: {resp.text[:300]}")
            return resp.json(), elapsed_ms

        data, elapsed_ms = with_retry(
            _do_request,
            retry_exceptions=(TransientLLMError,),
            config=self.retry_config,
        )

        choices = data.get("choices") or []
        if not choices:
            raise LLMError("OpenAI-compat response contained no choices.")
        content = str(choices[0].get("message", {}).get("content", ""))

        usage_raw = data.get("usage") or {}
        prompt_text = "\n".join(m.content for m in messages)
        usage = TokenUsage(
            prompt_tokens=int(usage_raw.get("prompt_tokens") or estimate_tokens(prompt_text)),
            completion_tokens=int(usage_raw.get("completion_tokens") or estimate_tokens(content)),
        )
        return LLMResponse(
            content=content,
            model=str(data.get("model") or self.model),
            model_version=str(data.get("system_fingerprint") or self.model_version),
            provider=self.provider_name,
            usage=usage,
            latency_ms=elapsed_ms,
            raw=data,
        )
