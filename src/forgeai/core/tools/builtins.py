"""Built-in deterministic tools and rule-based tool selection for forged agents."""

from __future__ import annotations

import re
from typing import Any

import httpx

from forgeai.core.ingest.index import HybridKnowledgeIndex
from forgeai.core.schemas import KnowledgeIndexData, RetrievalConfig
from forgeai.core.tools.registry import ToolDefinition, ToolRegistry
from forgeai.security.safe_math import MathEvaluationError, safe_eval_math
from forgeai.security.url_guard import URLValidationError, validate_url

ALL_BUILTIN_TOOLS: frozenset[str] = frozenset(
    {"kb_search", "table_lookup", "calc", "policy_cite", "http_get"}
)

_CALC_HINTS_RE = re.compile(
    r"\b(?:calc|calculate|compute|cost|price|total|per[- ]diem|sum|budget|discount|margin|lead time|math)\b",
    re.IGNORECASE,
)
_HTTP_HINTS_RE = re.compile(
    r"\b(?:http|api|fetch|live status|webhook|endpoint|remote url)\b",
    re.IGNORECASE,
)


def select_tools_for_agent(
    goal: str,
    index: KnowledgeIndexData,
    *,
    recommended: list[str] | None = None,
    allowed_http_hosts: list[str] | None = None,
) -> list[str]:
    """Deterministic tool-selection rules: attach ONLY tools the brief and corpus genuinely need."""
    selected: list[str] = ["kb_search", "policy_cite"]

    # Always enable table_lookup when structured tables are ingested
    if index.tables:
        selected.append("table_lookup")

    # Enable calc when brief mentions math/costs/totals or corpus contains numeric tables
    if _CALC_HINTS_RE.search(goal) or index.tables:
        selected.append("calc")

    # Include safe LLM recommendations filtered against known built-ins
    for tool in recommended or []:
        if tool in ALL_BUILTIN_TOOLS and tool != "http_get":
            selected.append(tool)

    # Only enable http_get if an explicit host allowlist is configured AND requested
    if allowed_http_hosts and (
        _HTTP_HINTS_RE.search(goal) or "http_get" in (recommended or [])
    ):
        selected.append("http_get")

    return list(dict.fromkeys(selected))


def build_default_registry(
    index: HybridKnowledgeIndex,
    *,
    retrieval_config: RetrievalConfig | None = None,
    allowed_http_hosts: list[str] | None = None,
    http_client: httpx.Client | None = None,
) -> ToolRegistry:
    """Create a ToolRegistry wired to a forged agent's KnowledgeIndex, RetrievalConfig, and allowlists."""
    registry = ToolRegistry()
    rcfg = retrieval_config or RetrievalConfig()
    host_allowlist = list(allowed_http_hosts or [])

    def _kb_search(args: dict[str, Any]) -> dict[str, Any]:
        query = str(args.get("query", "")).strip()
        if not query:
            return {"error": "query is required"}
        top_k = max(1, min(15, int(args.get("top_k", rcfg.top_k))))
        source_filter = args.get("source")
        hits = index.search(
            query,
            top_k=top_k,
            source_filter=source_filter,
            bm25_weight=rcfg.bm25_weight,
            cosine_weight=rcfg.cosine_weight,
            min_score=rcfg.min_score_threshold,
        )
        return {
            "query": query,
            "retrieval_config": {
                "top_k": top_k,
                "bm25_weight": rcfg.bm25_weight,
                "cosine_weight": rcfg.cosine_weight,
                "min_score_threshold": rcfg.min_score_threshold,
            },
            "matches": [
                {
                    "source": h.chunk.source,
                    "chunk_id": h.chunk.chunk_id,
                    "section": h.chunk.section,
                    "score": h.score,
                    "text": h.chunk.text,
                    "injection_flagged": h.chunk.injection_flagged,
                }
                for h in hits
            ],
        }

    def _table_lookup(args: dict[str, Any]) -> dict[str, Any]:
        query = str(args.get("query", "")).strip().lower()
        table_filter = str(args.get("source", "")).strip().lower()
        limit = max(1, min(25, int(args.get("limit", 5))))
        if not index.data.tables:
            return {"rows": [], "note": "No structured tables ingested."}

        tokens = [t for t in query.split() if t]
        scored: list[tuple[int, dict[str, Any]]] = []
        for table in index.data.tables:
            if table_filter and table_filter not in table.source.lower():
                continue
            for row_idx, row in enumerate(table.rows, start=1):
                haystack = " ".join(str(v).lower() for v in row.values())
                if not tokens:
                    score = 1
                else:
                    score = sum(2 if tok in haystack else 0 for tok in tokens)
                    if query and query in haystack:
                        score += 3
                if score > 0:
                    enriched = {"_source": table.source, "_row": row_idx, **row}
                    scored.append((score, enriched))

        scored.sort(key=lambda item: item[0], reverse=True)
        return {
            "query": query,
            "total_matches": len(scored),
            "rows": [row for _, row in scored[:limit]],
        }

    def _calc(args: dict[str, Any]) -> dict[str, Any]:
        expr = str(args.get("expression", "")).strip()
        if not expr:
            return {"error": "expression is required"}
        try:
            value = safe_eval_math(expr)
        except MathEvaluationError as exc:
            return {"error": str(exc), "expression": expr}
        return {"expression": expr, "result": value}

    def _policy_cite(args: dict[str, Any]) -> dict[str, Any]:
        source = str(args.get("source", "")).strip()
        chunk_id = str(args.get("chunk_id", "")).strip()
        chunk = index.get_chunk(source, chunk_id)
        if chunk is None:
            return {"error": f"Citation {source}#{chunk_id} not found."}
        return {
            "citation": f"{chunk.source}#{chunk.chunk_id}",
            "section": chunk.section,
            "text": chunk.text,
        }

    def _http_get(args: dict[str, Any]) -> dict[str, Any]:
        url = str(args.get("url", "")).strip()
        if not host_allowlist:
            return {"error": "http_get is disabled because FORGEAI_HTTP_ALLOWED_HOSTS is empty."}
        try:
            safe_url = validate_url(
                url,
                allow_private=False,
                resolve_dns=http_client is None,
                allowed_hosts=host_allowlist,
            )
        except URLValidationError as exc:
            return {"error": f"URL blocked by security policy: {exc}"}

        try:
            if http_client is not None:
                resp = http_client.get(safe_url)
            else:
                with httpx.Client(timeout=10.0, follow_redirects=False) as client:
                    resp = client.get(safe_url, headers={"User-Agent": "ForgeAI-Tool/0.2"})
        except httpx.HTTPError as exc:
            return {"error": f"HTTP request failed: {exc}"}

        body = resp.text[:4096]
        return {
            "url": safe_url,
            "status_code": resp.status_code,
            "body_preview": body,
        }

    registry.register(
        ToolDefinition(
            name="kb_search",
            description="Search ingested documents using hybrid BM25 + TF-IDF retrieval.",
            parameters_schema={"query": "string", "top_k": "int (1-15)", "source": "optional string"},
            handler=_kb_search,
        )
    )
    registry.register(
        ToolDefinition(
            name="table_lookup",
            description="Query structured CSV/JSON tables by keyword, SKU, part number, or attribute.",
            parameters_schema={"query": "string", "limit": "int (1-25)", "source": "optional string"},
            handler=_table_lookup,
        )
    )
    registry.register(
        ToolDefinition(
            name="calc",
            description="Evaluate bounded arithmetic expressions safely using AST math evaluator.",
            parameters_schema={"expression": "string (e.g. '4 * 320 + 150')"},
            handler=_calc,
        )
    )
    registry.register(
        ToolDefinition(
            name="policy_cite",
            description="Fetch the exact verbatim text of a cited chunk by source and chunk_id.",
            parameters_schema={"source": "string", "chunk_id": "string"},
            handler=_policy_cite,
        )
    )
    registry.register(
        ToolDefinition(
            name="http_get",
            description="Fetch a GET URL restricted by host allowlist and SSRF private-IP blocking.",
            parameters_schema={"url": "https URL matching allowed hosts"},
            handler=_http_get,
        )
    )
    return registry
