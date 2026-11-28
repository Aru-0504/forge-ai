"""Built-in deterministic tools and strict brief-driven tool selection for forged agents."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import httpx

from forgeai.core.ingest.index import HybridKnowledgeIndex
from forgeai.core.schemas import KnowledgeIndexData, RetrievalConfig
from forgeai.core.tools.registry import ToolDefinition, ToolRegistry
from forgeai.security.safe_math import MathEvaluationError, safe_eval_math
from forgeai.security.url_guard import URLValidationError, validate_url

ALL_BUILTIN_TOOLS: frozenset[str] = frozenset(
    {"kb_search", "table_lookup", "calc", "policy_cite", "current_date", "open_meteo", "http_get"}
)

_CALC_HINTS_RE = re.compile(
    r"\b(?:calc|calculate|compute|cost|price|total|per[- ]diem|sum|budget|discount|margin|lead time|math|prorat\w*)\b",
    re.IGNORECASE,
)
_DATE_HINTS_RE = re.compile(
    r"\b(?:current date|today(?:'s)? date|what date|deadline|expir(?:e|y|ation)|calendar|days until|days since|as of today|tenure)\b",
    re.IGNORECASE,
)
_WEATHER_HINTS_RE = re.compile(
    r"\b(?:weather|forecast|open[- ]?meteo|temperature|wind ?speed|precipitation|rainfall|sea state|meteorolog\w*|marine weather)\b",
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
    """Strict tool-selection rules: attach ONLY tools the brief and corpus genuinely need.

    Prevents factory defects such as attaching `open_meteo` (weather) or `table_lookup`
    to a text-only HR policy assistant even if an LLM over-recommends them.
    """
    selected: list[str] = ["kb_search", "policy_cite"]

    # Attach table_lookup ONLY when structured tables (CSV/JSON) were actually ingested
    if index.tables:
        selected.append("table_lookup")

    # Attach calc ONLY when the brief requests arithmetic/costs OR structured tables exist
    if _CALC_HINTS_RE.search(goal) or index.tables:
        selected.append("calc")

    # Attach current_date ONLY when the brief involves dates/deadlines/current-date checks
    if _DATE_HINTS_RE.search(goal):
        selected.append("current_date")

    # Attach open_meteo ONLY when the brief explicitly asks for weather/forecast data
    if _WEATHER_HINTS_RE.search(goal):
        selected.append("open_meteo")

    # Attach http_get ONLY when an explicit host allowlist is configured AND requested
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

    def _current_date(args: dict[str, Any]) -> dict[str, Any]:
        offset_raw = args.get("utc_offset_hours", 0)
        try:
            offset_hours = float(offset_raw)
        except (TypeError, ValueError):
            return {"error": f"Invalid utc_offset_hours: {offset_raw!r}"}
        if not (-12.0 <= offset_hours <= 14.0):
            return {"error": "utc_offset_hours must be between -12 and +14."}
        tz = timezone(timedelta(hours=offset_hours)) if offset_hours else UTC
        now = datetime.now(tz)
        return {
            "date": now.strftime("%Y-%m-%d"),
            "iso_timestamp": now.isoformat(),
            "day_of_week": now.strftime("%A"),
            "utc_offset_hours": offset_hours,
        }

    def _open_meteo(args: dict[str, Any]) -> dict[str, Any]:
        try:
            lat = float(args["latitude"])
            lon = float(args["longitude"])
        except (KeyError, TypeError, ValueError):
            return {"error": "Numeric 'latitude' and 'longitude' are required."}
        if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
            return {"error": "Coordinates out of range (lat in [-90, 90], lon in [-180, 180])."}

        url = (
            "https://api.open-meteo.com/v1/forecast"
            f"?latitude={lat:.4f}&longitude={lon:.4f}&current_weather=true"
        )
        try:
            safe_url = validate_url(
                url,
                allow_private=False,
                resolve_dns=http_client is None,
                allowed_hosts=["api.open-meteo.com"],
            )
        except URLValidationError as exc:
            return {"error": f"Open-Meteo URL blocked by security policy: {exc}"}

        try:
            if http_client is not None:
                resp = http_client.get(safe_url)
            else:
                with httpx.Client(timeout=10.0, follow_redirects=False) as client:
                    resp = client.get(safe_url, headers={"User-Agent": "ForgeAI-OpenMeteo/0.2"})
            resp.raise_for_status()
            payload = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            return {"error": f"Open-Meteo request failed: {exc}"}

        cw = payload.get("current_weather") or {}
        return {
            "source": "open-meteo.com",
            "latitude": payload.get("latitude", lat),
            "longitude": payload.get("longitude", lon),
            "temperature_c": cw.get("temperature"),
            "windspeed_kmh": cw.get("windspeed"),
            "winddirection_deg": cw.get("winddirection"),
            "weathercode": cw.get("weathercode"),
            "time": cw.get("time"),
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
            name="current_date",
            description="Return the current date, day of week, and ISO timestamp (optional utc_offset_hours).",
            parameters_schema={"utc_offset_hours": "optional number (-12 to 14)"},
            handler=_current_date,
        )
    )
    registry.register(
        ToolDefinition(
            name="open_meteo",
            description="Fetch live current weather (temperature, windspeed) from the keyless Open-Meteo API.",
            parameters_schema={"latitude": "float (-90..90)", "longitude": "float (-180..180)"},
            handler=_open_meteo,
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
