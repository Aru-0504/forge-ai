"""Unit tests for forgeai.llm: json_repair, retry, Ollama & OpenAI-compat clients."""

from __future__ import annotations

import httpx
import pytest
from pydantic import BaseModel

from forgeai.llm.base import LLMMessage, StubLLMClient, TransientLLMError
from forgeai.llm.json_repair import JSONRepairError, parse_and_repair_json
from forgeai.llm.ollama import OllamaLLMClient
from forgeai.llm.openai_compat import OpenAICompatLLMClient
from forgeai.llm.retry import RetryConfig, with_retry


class SampleSchema(BaseModel):
    answer: str
    score: int


def test_json_repair_handles_fences_prose_trailing_commas_and_truncation() -> None:
    fenced = 'Sure! Here is your JSON:\n```json\n{"a": 1, "b": [2, 3,],}\n```\nHope that helps.'
    assert parse_and_repair_json(fenced) == {"a": 1, "b": [2, 3]}

    truncated = '{"name": "agent", "tools": ["kb_search"'
    assert parse_and_repair_json(truncated) == {"name": "agent", "tools": ["kb_search"]}

    with pytest.raises(JSONRepairError):
        parse_and_repair_json("completely plain prose without json")


def test_retry_succeeds_after_transient_failures() -> None:
    attempts = {"count": 0}

    def flaky() -> str:
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise TransientLLMError("temporary 503")
        return "ok"

    result = with_retry(
        flaky,
        retry_exceptions=(TransientLLMError,),
        config=RetryConfig(max_retries=3, base_delay_seconds=0.001),
        sleep_fn=lambda _s: None,
    )
    assert result == "ok"
    assert attempts["count"] == 3


def test_generate_json_repairs_on_second_attempt() -> None:
    llm = StubLLMClient(
        canned_responses=[
            '{"answer": "missing score"}',
            '```json\n{"answer": "ok", "score": 95,}\n```',
        ]
    )
    parsed = llm.generate_json(
        [LLMMessage(role="user", content="return sample")],
        SampleSchema,
        stage="unit_test",
    )
    assert parsed.answer == "ok"
    assert parsed.score == 95


def test_ollama_client_retries_503_then_parses_response() -> None:
    calls = {"n": 0}

    def handler(_req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, text="overloaded")
        return httpx.Response(
            200,
            json={
                "model": "qwen2.5:7b-instruct",
                "message": {"role": "assistant", "content": '{"ok": true}'},
                "prompt_eval_count": 42,
                "eval_count": 11,
            },
        )

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as http_client:
        client = OllamaLLMClient(
            base_url="http://mock-ollama",
            retry_config=RetryConfig(max_retries=2, base_delay_seconds=0.001),
            http_client=http_client,
        )
        resp = client.chat([LLMMessage(role="user", content="hi")], json_mode=True)
        assert resp.content == '{"ok": true}'
        assert resp.usage.prompt_tokens == 42
        assert resp.usage.completion_tokens == 11
        assert calls["n"] == 2


def test_openai_compat_client_sends_auth_and_parses_usage() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.headers["Authorization"] == "Bearer test-secret"
        return httpx.Response(
            200,
            json={
                "model": "gpt-4o-mini",
                "choices": [{"message": {"role": "assistant", "content": '{"status": "ready"}'}}],
                "usage": {"prompt_tokens": 25, "completion_tokens": 9},
            },
        )

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as http_client:
        client = OpenAICompatLLMClient(
            base_url="https://mock-openai/v1",
            api_key="test-secret",
            model="gpt-4o-mini",
            http_client=http_client,
        )
        resp = client.chat([LLMMessage(role="user", content="ping")], json_mode=True)
        assert resp.provider == "openai_compat"
        assert resp.usage.total_tokens == 34
