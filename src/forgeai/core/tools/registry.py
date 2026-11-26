"""Typed tool registry with validation, timing, and error isolation."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from forgeai.core.schemas import ToolTrace


@dataclass(frozen=True)
class ToolDefinition:
    """Metadata and callable handler for a forged-agent tool."""

    name: str
    description: str
    parameters_schema: dict[str, Any]
    handler: Callable[[dict[str, Any]], dict[str, Any]] = field(repr=False)

    def prompt_line(self) -> str:
        params = ", ".join(self.parameters_schema.keys())
        return f"- `{self.name}`: {self.description} (args: {params})"


class ToolRegistry:
    """Registry that binds allowed tools for a specific forged agent."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, tool: ToolDefinition) -> None:
        self._tools[tool.name] = tool

    def list_names(self) -> list[str]:
        return sorted(self._tools.keys())

    def prompt_descriptions(self, enabled: list[str] | None = None) -> list[str]:
        allowed = set(enabled) if enabled is not None else set(self._tools.keys())
        return [
            tool.prompt_line()
            for name, tool in sorted(self._tools.items())
            if name in allowed
        ]

    def execute(
        self,
        name: str,
        args: dict[str, Any],
        *,
        enabled: list[str] | None = None,
    ) -> ToolTrace:
        t0 = time.monotonic()
        if enabled is not None and name not in enabled:
            return ToolTrace(
                tool_name=name,
                args=args,
                result={"error": f"Tool {name!r} is not enabled for this agent."},
                ok=False,
                latency_ms=round((time.monotonic() - t0) * 1000.0, 2),
            )
        tool = self._tools.get(name)
        if tool is None:
            return ToolTrace(
                tool_name=name,
                args=args,
                result={"error": f"Unknown tool: {name!r}"},
                ok=False,
                latency_ms=round((time.monotonic() - t0) * 1000.0, 2),
            )
        try:
            result = tool.handler(args)
            ok = "error" not in result
        except Exception as exc:
            result = {"error": str(exc)}
            ok = False

        return ToolTrace(
            tool_name=name,
            args=args,
            result=result,
            ok=ok,
            latency_ms=round((time.monotonic() - t0) * 1000.0, 2),
        )
