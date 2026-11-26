"""Resilient JSON extraction and repair for LLM structured outputs."""

from __future__ import annotations

import json
import re
from typing import Any

_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)
_TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")
_LINE_COMMENT_RE = re.compile(r"(?m)^\s*//.*$")


class JSONRepairError(ValueError):
    """Raised when raw LLM text cannot be repaired into valid JSON."""


def _extract_balanced_json(text: str) -> str | None:
    """Find the first balanced `{...}` or `[...]` block in arbitrary text."""
    start_idx = -1
    for i, ch in enumerate(text):
        if ch in "{[":
            start_idx = i
            break
    if start_idx == -1:
        return None

    stack: list[str] = []
    in_string = False
    escape = False
    for idx in range(start_idx, len(text)):
        ch = text[idx]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]":
            if stack and stack[-1] == ch:
                stack.pop()
                if not stack:
                    return text[start_idx : idx + 1]
    # Truncated JSON: close remaining open delimiters
    if stack:
        candidate = text[start_idx:].rstrip().rstrip(",")
        if in_string:
            candidate += '"'
        return candidate + "".join(reversed(stack))
    return None


def _clean_json_text(candidate: str) -> str:
    cleaned = _LINE_COMMENT_RE.sub("", candidate)
    cleaned = _TRAILING_COMMA_RE.sub(r"\1", cleaned)
    return cleaned.strip()


def parse_and_repair_json(raw: str) -> Any:
    """Attempt strict JSON parsing, falling back to fenced extraction and syntax repair."""
    text = raw.strip()
    if not text:
        raise JSONRepairError("Empty LLM response; expected JSON.")

    # 1. Fast path: valid JSON as-is
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 2. Extract from markdown code fences if present
    candidates: list[str] = []
    for match in _FENCE_RE.finditer(text):
        candidates.append(match.group(1).strip())
    candidates.append(text)

    for candidate in candidates:
        cleaned = _clean_json_text(candidate)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass

        extracted = _extract_balanced_json(cleaned)
        if extracted:
            repaired = _clean_json_text(extracted)
            try:
                return json.loads(repaired)
            except json.JSONDecodeError:
                pass

    raise JSONRepairError(f"Could not parse or repair JSON from output: {text[:240]!r}")
