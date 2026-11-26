"""FastAPI service: POST /runs, GET /runs/{id}, SSE /events, /approve, /regress, /agents/{id}/chat, /healthz."""

from __future__ import annotations

import base64
import hmac
import json
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

from forgeai import __version__
from forgeai.config import Settings, get_settings
from forgeai.core.ingest.parsers import IngestInputError
from forgeai.core.regression import run_regression
from forgeai.core.runtime.agent import ForgedAgentRuntime
from forgeai.core.schemas import (
    ChatMessage,
    ForgeRequest,
    RequirementsDoc,
    SourceDocument,
)
from forgeai.llm.base import BaseLLMClient, create_llm_client
from forgeai.observability.logging import configure_logging
from forgeai.observability.metrics import MetricsRegistry, MetricsTracker, RunBudget
from forgeai.security.upload_guard import UploadValidationError, validate_upload_batch
from forgeai.security.url_guard import URLValidationError, validate_url
from forgeai.service.events import EventBus
from forgeai.service.jobs import ForgeJobQueue
from forgeai.service.store import ForgeStore


class InlineSourcePayload(BaseModel):
    filename: str
    content: str | None = None
    content_base64: str | None = None


class CreateRunJSONRequest(BaseModel):
    brief: str | None = Field(default=None, max_length=2000)
    goal: str | None = Field(default=None, max_length=2000)
    agent_name_hint: str | None = None
    sources: list[InlineSourcePayload] = Field(default_factory=list)
    urls: list[str] = Field(default_factory=list)
    clarifying_answers: dict[str, str] = Field(default_factory=dict)
    require_human_approval: bool = False
    chunk_size: int = Field(default=900, ge=100, le=8000)
    chunk_overlap: int = Field(default=150, ge=0, le=2000)
    max_loops: int | None = Field(default=None, ge=1, le=5)
    redact_pii: bool = True


class ApproveRequirementsRequest(BaseModel):
    requirements: RequirementsDoc | None = None


class ChatTurnRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    session_id: str | None = None


def create_app(
    *,
    settings: Settings | None = None,
    llm: BaseLLMClient | None = None,
) -> FastAPI:
    """Create a configured FastAPI application with managed SQLite job worker lifecycle."""
    cfg = settings or get_settings()
    cfg.ensure_directories()
    configure_logging(cfg.log_level, cfg.log_format)

    client = llm or create_llm_client(cfg)
    store = ForgeStore(db_path=cfg.db_path, artifacts_dir=cfg.artifacts_dir)
    event_bus = EventBus(store)
    metrics_registry = MetricsRegistry()
    job_queue = ForgeJobQueue(
        settings=cfg,
        llm=client,
        store=store,
        event_bus=event_bus,
        registry=metrics_registry,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if cfg.run_embedded_worker:
            await job_queue.start()
        try:
            yield
        finally:
            if cfg.run_embedded_worker:
                await job_queue.stop()

    app = FastAPI(
        title="ForgeAI Agent Factory API",
        version=__version__,
        description="Ingest domain corpora, forge evaluated tool-using agents, approve requirements, run regressions, and chat.",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.state.settings = cfg
    app.state.llm = client
    app.state.store = store
    app.state.event_bus = event_bus
    app.state.job_queue = job_queue
    app.state.metrics_registry = metrics_registry

    def _require_api_key(x_api_key: str | None = Header(default=None)) -> None:
        if cfg.api_key:
            if not x_api_key or not hmac.compare_digest(x_api_key, cfg.api_key):
                raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key.")

    @app.get("/healthz")
    @app.get("/health")
    async def healthz() -> dict[str, Any]:
        return {
            "status": "ok",
            "version": __version__,
            "provider": client.provider_name,
            "model": client.model,
            "model_version": client.model_version,
            "config_hash": cfg.config_hash(),
        }

    @app.get("/metrics")
    async def metrics(format: str = Query(default="json")) -> Any:
        if format == "prometheus":
            return PlainTextResponse(metrics_registry.export_prometheus(), media_type="text/plain")
        return metrics_registry.export_dict()

    async def _parse_run_request(request: Request) -> ForgeRequest:
        content_type = (request.headers.get("content-type") or "").lower()
        docs: list[SourceDocument] = []
        urls: list[str] = []
        clarifying_answers: dict[str, str] = {}
        require_human_approval = False

        if "multipart/form-data" in content_type:
            form = await request.form()
            brief = str(form.get("brief") or form.get("goal") or "").strip()
            if len(brief) < 5:
                raise HTTPException(
                    status_code=400,
                    detail="Field 'brief' (or 'goal') is required and must be at least 5 characters.",
                )
            agent_name_hint = str(form.get("agent_name_hint") or "").strip() or None
            redact_pii = str(form.get("redact_pii", "true")).lower() != "false"
            require_human_approval = (
                str(form.get("require_human_approval", "false")).lower() == "true"
            )
            max_loops = int(str(form.get("max_loops") or cfg.max_improvement_loops))

            raw_qa = str(form.get("clarifying_answers") or "").strip()
            if raw_qa:
                try:
                    parsed_qa = json.loads(raw_qa)
                    if isinstance(parsed_qa, dict):
                        clarifying_answers = {str(k): str(v) for k, v in parsed_qa.items()}
                except json.JSONDecodeError as exc:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Invalid JSON in clarifying_answers: {exc}",
                    ) from exc

            for u in form.getlist("urls"):
                if isinstance(u, str) and u.strip():
                    urls.append(u.strip())

            for key in ("files", "docs"):
                for item in form.getlist(key):
                    if hasattr(item, "read"):
                        data = await item.read()
                        fname = getattr(item, "filename", None) or "upload.txt"
                        mtype = getattr(item, "content_type", None) or "application/octet-stream"
                        docs.append(SourceDocument(filename=fname, content=data, media_type=mtype))

            chunk_size = cfg.chunk_size
            chunk_overlap = cfg.chunk_overlap
        else:
            try:
                body = await request.json()
                payload = CreateRunJSONRequest.model_validate(body)
            except Exception as exc:
                raise HTTPException(status_code=400, detail=f"Invalid request payload: {exc}") from exc

            brief = (payload.brief or payload.goal or "").strip()
            if len(brief) < 5:
                raise HTTPException(
                    status_code=400,
                    detail="Field 'brief' (or 'goal') is required and must be at least 5 characters.",
                )
            agent_name_hint = payload.agent_name_hint
            redact_pii = payload.redact_pii
            require_human_approval = payload.require_human_approval
            clarifying_answers = dict(payload.clarifying_answers)
            max_loops = payload.max_loops or cfg.max_improvement_loops
            urls = list(payload.urls)
            chunk_size = payload.chunk_size
            chunk_overlap = payload.chunk_overlap

            for src in payload.sources:
                if src.content_base64:
                    raw_bytes = base64.b64decode(src.content_base64)
                elif src.content is not None:
                    raw_bytes = src.content.encode("utf-8")
                else:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Source {src.filename!r} must provide 'content' or 'content_base64'.",
                    )
                docs.append(SourceDocument(filename=src.filename, content=raw_bytes))

        if not docs and not urls:
            raise HTTPException(
                status_code=400,
                detail="Empty submission: upload at least one readable document or provide a valid URL.",
            )

        try:
            if docs:
                validate_upload_batch(
                    [(d.filename, d.content) for d in docs],
                    max_files=cfg.max_files_per_run,
                    max_bytes_per_file=cfg.max_upload_bytes,
                )
            for u in urls:
                validate_url(u, allow_private=cfg.allow_private_urls, resolve_dns=False)
        except (UploadValidationError, URLValidationError, IngestInputError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        return ForgeRequest(
            goal=brief,
            agent_name_hint=agent_name_hint,
            sources=docs,
            urls=urls,
            clarifying_answers=clarifying_answers,
            require_human_approval=require_human_approval,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            max_csv_rows=cfg.max_csv_rows,
            max_loops=max(1, min(5, max_loops)),
            target_score=cfg.target_eval_score,
            redact_pii=redact_pii,
            allow_private_urls=cfg.allow_private_urls,
            allowed_http_hosts=cfg.allowed_http_hosts_list,
        )

    @app.post("/runs", status_code=202, dependencies=[Depends(_require_api_key)])
    async def create_run(request: Request) -> dict[str, Any]:
        req = await _parse_run_request(request)
        run_id = await job_queue.enqueue(req)
        return {
            "run_id": run_id,
            "status": "queued",
            "status_url": f"/runs/{run_id}",
            "events_url": f"/runs/{run_id}/events",
            "approve_url": f"/runs/{run_id}/approve",
            "chat_url": f"/agents/{run_id}/chat",
        }

    @app.get("/runs", dependencies=[Depends(_require_api_key)])
    async def list_runs(limit: int = Query(default=50, ge=1, le=200)) -> dict[str, Any]:
        return {"runs": store.list_runs(limit=limit)}

    @app.get("/runs/{run_id}", dependencies=[Depends(_require_api_key)])
    async def get_run(run_id: str) -> dict[str, Any]:
        run = store.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"Run {run_id!r} not found.")

        artifact = (
            store.load_artifact(run_id)
            if run["status"] in {"done", "budget_exhausted"}
            else None
        )
        pending_approval = (
            store.load_pending_approval(run_id)
            if run["status"] == "awaiting_approval"
            else None
        )
        run_folder = (
            Path(run["artifact_path"]) if run.get("artifact_path") else store.artifacts_dir / run_id
        )
        log_path = run_folder / "run_logs.jsonl"
        payload: dict[str, Any] = {
            **run,
            "has_logs": log_path.exists(),
            "pending_approval": pending_approval,
            "artifacts": None,
        }
        if artifact is not None:
            payload["artifacts"] = {
                "manifest": {
                    "schema_version": artifact.schema_version,
                    "provider": artifact.provider,
                    "model": artifact.model,
                    "model_version": artifact.model_version,
                    "seed": artifact.seed,
                    "config_hash": artifact.config_hash,
                    "prompt_versions": artifact.prompt_versions,
                },
                "requirements": artifact.requirements.model_dump(),
                "analysis": artifact.analysis.model_dump(),
                "groundability": artifact.groundability.model_dump(),
                "agent_spec": artifact.agent_spec.model_dump(),
                "eval_report": artifact.eval_report.model_dump(),
                "scorecard": artifact.scorecard.model_dump(),
                "degraded_stages": [d.model_dump() for d in artifact.degraded_stages],
                "report_markdown": artifact.report_markdown,
                "metrics": artifact.metrics,
            }
        return payload

    @app.get("/runs/{run_id}/events", dependencies=[Depends(_require_api_key)])
    async def stream_run_events(
        run_id: str,
        last_event_id: int = Query(default=0, ge=0),
    ) -> StreamingResponse:
        if store.get_run(run_id) is None:
            raise HTTPException(status_code=404, detail=f"Run {run_id!r} not found.")
        return StreamingResponse(
            event_bus.stream(run_id, last_event_id=last_event_id),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )

    @app.post("/runs/{run_id}/approve", status_code=202, dependencies=[Depends(_require_api_key)])
    async def approve_run_requirements(
        run_id: str,
        payload: ApproveRequirementsRequest | None = None,
    ) -> dict[str, Any]:
        """Human Checkpoint: approve or edit the RequirementsDoc and resume generation."""
        edited = payload.requirements if payload else None
        approved = await job_queue.approve(run_id, edited_requirements=edited)
        if approved is None:
            raise HTTPException(
                status_code=404,
                detail=f"Run {run_id!r} is not paused at a Human Checkpoint or lacks intake checkpoints.",
            )
        return {
            "run_id": run_id,
            "status": "queued",
            "approved": True,
            "requirements": approved.model_dump(),
        }

    @app.post("/runs/{run_id}/resume", status_code=202, dependencies=[Depends(_require_api_key)])
    async def resume_run(run_id: str) -> dict[str, Any]:
        ok = await job_queue.resume(run_id)
        if not ok:
            raise HTTPException(
                status_code=404,
                detail=f"Run {run_id!r} not found or lacks persisted inputs for resume.",
            )
        return {"run_id": run_id, "status": "queued", "resumed": True}

    @app.post("/runs/{run_id}/regress", dependencies=[Depends(_require_api_key)])
    async def regress_run(run_id: str, request: Request) -> dict[str, Any]:
        """Regression Mode: regenerate agent on updated docs and re-run run `{run_id}`'s old eval set."""
        baseline = store.load_artifact(run_id)
        if baseline is None:
            raise HTTPException(status_code=404, detail=f"Baseline artifact for run {run_id!r} not found.")

        form = await request.form()
        docs: list[SourceDocument] = []
        for key in ("files", "docs"):
            for item in form.getlist(key):
                if hasattr(item, "read"):
                    data = await item.read()
                    fname = getattr(item, "filename", None) or "updated.txt"
                    docs.append(SourceDocument(filename=fname, content=data))
        if not docs:
            raise HTTPException(status_code=400, detail="Upload at least one updated document in 'files'.")

        try:
            validate_upload_batch(
                [(d.filename, d.content) for d in docs],
                max_files=cfg.max_files_per_run,
                max_bytes_per_file=cfg.max_upload_bytes,
            )
            regenerated, report = run_regression(
                baseline_artifact=baseline,
                updated_sources=docs,
                llm=client,
                config_hash=cfg.config_hash(),
            )
        except (UploadValidationError, IngestInputError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        # Persist regenerated artifact and regression report under store
        store.create_run(
            run_id=regenerated.run_id,
            request=ForgeRequest(goal=regenerated.goal, sources=docs),
            provider=client.provider_name,
            model=client.model,
            model_version=client.model_version,
            config_hash=cfg.config_hash(),
        )
        reg_dir = store.save_artifact(regenerated)
        (reg_dir / "regression_report.json").write_text(
            report.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
        (reg_dir / "regression_report.md").write_text(report.report_markdown, encoding="utf-8")
        return report.model_dump()

    @app.post("/agents/{agent_id}/chat", dependencies=[Depends(_require_api_key)])
    async def chat_with_agent(agent_id: str, payload: ChatTurnRequest) -> dict[str, Any]:
        artifact = store.load_artifact(agent_id)
        if artifact is None:
            raise HTTPException(status_code=404, detail=f"Forged agent {agent_id!r} not found.")

        session_id = payload.session_id or f"sess_{uuid.uuid4().hex[:10]}"
        history = store.get_chat_history(agent_id, session_id)
        runtime = ForgedAgentRuntime.from_artifact(
            artifact,
            client,
            allowed_http_hosts=cfg.allowed_http_hosts_list,
        )
        tracker = MetricsTracker(
            run_id=f"chat_{session_id}",
            budget=RunBudget(
                max_tokens=cfg.max_tokens_per_run,
                max_latency_seconds=cfg.max_latency_seconds_per_run,
                max_cost_usd=cfg.max_cost_usd_per_run,
                input_cost_per_1k=cfg.input_cost_per_1k,
                output_cost_per_1k=cfg.output_cost_per_1k,
            ),
        )
        response = runtime.answer(payload.message, history=history, metrics=tracker)

        store.append_chat_message(
            agent_id,
            session_id,
            ChatMessage(role="user", content=payload.message),
        )
        store.append_chat_message(
            agent_id,
            session_id,
            ChatMessage(
                role="assistant",
                content=response.answer,
                citations=response.citations,
                tool_traces=response.tool_traces,
            ),
        )
        metrics_registry.inc("agent_chat_turns")
        return {
            "agent_id": agent_id,
            "session_id": session_id,
            "agent_name": artifact.agent_spec.name,
            **response.model_dump(),
        }

    @app.get("/agents/{agent_id}/sessions/{session_id}", dependencies=[Depends(_require_api_key)])
    async def get_chat_session(agent_id: str, session_id: str) -> dict[str, Any]:
        history = store.get_chat_history(agent_id, session_id)
        return {
            "agent_id": agent_id,
            "session_id": session_id,
            "messages": [m.model_dump() for m in history],
        }

    return app


app = create_app()
