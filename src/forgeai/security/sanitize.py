"""Input sanitization, PII redaction, and prompt-injection heuristics."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_MULTI_NEWLINE_RE = re.compile(r"\n{4,}")

_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_PHONE_RE = re.compile(
    r"(?<!\w)(?:\+?1[-.\s]?)?(?:\(\d{3}\)|\d{3})[-.\s]\d{3}[-.\s]\d{4}(?!\w)"
)
_CC_RE = re.compile(r"\b(?:\d[ -]*?){13,16}\b")

_INJECTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "ignore_previous_instructions",
        re.compile(
            r"\b(?:ignore|disregard|forget|override)\s+(?:all\s+)?(?:previous|prior|above|system)\s+(?:instructions|prompts|rules|directions)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "system_prompt_exfiltration",
        re.compile(
            r"\b(?:reveal|print|dump|show|output|repeat)\s+(?:your\s+)?(?:system\s+prompt|hidden\s+instructions|developer\s+message)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "role_hijack",
        re.compile(
            r"(?:^|\n)\s*(?:system|developer|assistant)\s*:\s*",
            re.IGNORECASE,
        ),
    ),
    (
        "delimiter_escape",
        re.compile(
            r"</?(?:system|instruction|tool_call|prompt)>|\[INST\]|<<SYS>>",
            re.IGNORECASE,
        ),
    ),
)


@dataclass(frozen=True)
class InjectionScanResult:
    """Result of scanning untrusted text for prompt-injection markers."""

    flagged: bool
    matches: list[str] = field(default_factory=list)


def detect_prompt_injection(text: str) -> InjectionScanResult:
    """Detect common prompt-injection signatures in user or document text."""
    matched: list[str] = []
    for name, pattern in _INJECTION_PATTERNS:
        if pattern.search(text):
            matched.append(name)
    return InjectionScanResult(flagged=bool(matched), matches=matched)


def redact_pii(text: str) -> str:
    """Redact common PII patterns (SSN, credit card-like sequences, email, phone)."""
    out = _SSN_RE.sub("[REDACTED_SSN]", text)
    out = _EMAIL_RE.sub("[REDACTED_EMAIL]", out)
    out = _PHONE_RE.sub("[REDACTED_PHONE]", out)

    def _mask_cc(match: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", match.group(0))
        if 13 <= len(digits) <= 16 and _luhn_valid(digits):
            return "[REDACTED_CARD]"
        return match.group(0)

    return _CC_RE.sub(_mask_cc, out)


def _luhn_valid(digits: str) -> bool:
    total = 0
    reverse = digits[::-1]
    for idx, ch in enumerate(reverse):
        n = int(ch)
        if idx % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def sanitize_text(
    text: str,
    *,
    max_length: int | None = None,
    strip_control: bool = True,
    apply_pii_redaction: bool = False,
) -> str:
    """Normalize unicode, strip dangerous control characters, and optionally redact PII."""
    normalized = unicodedata.normalize("NFKC", text)
    if strip_control:
        normalized = _CONTROL_CHARS_RE.sub("", normalized)
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    normalized = _MULTI_NEWLINE_RE.sub("\n\n\n", normalized)
    if apply_pii_redaction:
        normalized = redact_pii(normalized)
    if max_length is not None and len(normalized) > max_length:
        normalized = normalized[:max_length]
    return normalized.strip()


def sanitize_filename(filename: str, fallback: str = "upload.txt") -> str:
    """Prevent path traversal and strip unsafe characters from an uploaded filename."""
    # Extract basename across both POSIX and Windows separators
    base = filename.replace("\\", "/").split("/")[-1]
    base = unicodedata.normalize("NFKC", base)
    base = _CONTROL_CHARS_RE.sub("", base).strip()
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", base)
    base = base.lstrip(".")
    if not base or base in {".", ".."}:
        return fallback
    p = Path(base)
    stem = p.stem[:80] or "file"
    suffix = p.suffix[:16].lower()
    return f"{stem}{suffix}"
