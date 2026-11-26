"""Resumable SQLite job/event/chat store + durable filesystem artifacts & run inputs."""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from forgeai.core.schemas import (
    ChatMessage,
    ForgeArtifact,
    ForgeRequest,
    PipelineEvent,
    RunStatus,
    SourceDocument,
    utc_now_iso,
)
from forgeai.security.sanitize import sanitize_filename


class ForgeStore:
    """Thread-safe SQLite store for resumable runs, SSE events, chat histories, and artifacts."""

    def __init__(self, db_path: Path, artifacts_dir: Path) -> None:
        self.db_path = Path(db_path)
        self.artifacts_dir = Path(artifacts_dir)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=15.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        return conn

    def _init_schema(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS forge_runs (
                    run_id TEXT PRIMARY KEY,
                    goal TEXT NOT NULL,
                    status TEXT NOT NULL,
                    agent_name TEXT DEFAULT '',
                    agent_slug TEXT DEFAULT '',
                    domain TEXT DEFAULT '',
                    provider TEXT DEFAULT '',
                    model TEXT DEFAULT '',
                    model_version TEXT DEFAULT '',
                    config_hash TEXT DEFAULT '',
                    progress REAL DEFAULT 0.0,
                    eval_pass_rate REAL DEFAULT NULL,
                    eval_average_score REAL DEFAULT NULL,
                    error TEXT DEFAULT '',
                    artifact_path TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS pipeline_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    message TEXT NOT NULL,
                    progress REAL NOT NULL,
                    data_json TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    FOREIGN KEY (run_id) REFERENCES forge_runs(run_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_events_run_id ON pipeline_events(run_id, id);

                CREATE TABLE IF NOT EXISTS chat_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    citations_json TEXT NOT NULL,
                    tool_traces_json TEXT NOT NULL,
                    timestamp TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_chat_run_session ON chat_messages(run_id, session_id, id);
                """
            )

    def run_dir(self, run_id: str) -> Path:
        path = self.artifacts_dir / run_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def persist_request(self, run_id: str, request: ForgeRequest) -> Path:
        """Persist raw inputs and request metadata on disk so runs are resumable across crashes."""
        base = self.run_dir(run_id)
        inputs_dir = base / "inputs"
        inputs_dir.mkdir(parents=True, exist_ok=True)

        source_records: list[dict[str, str]] = []
        for idx, doc in enumerate(request.sources, start=1):
            safe = sanitize_filename(doc.filename, f"source_{idx}.txt")
            stored_name = f"{idx:02d}_{safe}"
            (inputs_dir / stored_name).write_bytes(doc.content)
            source_records.append(
                {
                    "filename": doc.filename,
                    "stored_name": stored_name,
                    "media_type": doc.media_type,
                }
            )

        payload = {
            "goal": request.goal,
            "agent_name_hint": request.agent_name_hint,
            "urls": request.urls,
            "chunk_size": request.chunk_size,
            "chunk_overlap": request.chunk_overlap,
            "max_csv_rows": request.max_csv_rows,
            "max_loops": request.max_loops,
            "target_score": request.target_score,
            "redact_pii": request.redact_pii,
            "allow_private_urls": request.allow_private_urls,
            "allowed_http_hosts": request.allowed_http_hosts,
            "sources": source_records,
        }
        req_path = base / "request.json"
        req_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return req_path

    def load_request(self, run_id: str) -> ForgeRequest | None:
        base = self.artifacts_dir / run_id
        req_path = base / "request.json"
        if not req_path.exists():
            return None
        payload = json.loads(req_path.read_text(encoding="utf-8"))
        inputs_dir = base / "inputs"
        docs: list[SourceDocument] = []
        for item in payload.get("sources", []):
            file_path = inputs_dir / item["stored_name"]
            if file_path.exists():
                docs.append(
                    SourceDocument(
                        filename=item["filename"],
                        content=file_path.read_bytes(),
                        media_type=item.get("media_type", "application/octet-stream"),
                    )
                )
        return ForgeRequest(
            goal=payload["goal"],
            agent_name_hint=payload.get("agent_name_hint"),
            sources=docs,
            urls=payload.get("urls", []),
            chunk_size=payload.get("chunk_size", 900),
            chunk_overlap=payload.get("chunk_overlap", 150),
            max_csv_rows=payload.get("max_csv_rows", 5000),
            max_loops=payload.get("max_loops", 3),
            target_score=payload.get("target_score", 0.9),
            redact_pii=payload.get("redact_pii", True),
            allow_private_urls=payload.get("allow_private_urls", False),
            allowed_http_hosts=payload.get("allowed_http_hosts", []),
        )

    def create_run(
        self,
        *,
        run_id: str,
        request: ForgeRequest,
        provider: str,
        model: str,
        model_version: str = "latest",
        config_hash: str = "",
    ) -> dict[str, Any]:
        self.persist_request(run_id, request)
        now = utc_now_iso()
        artifact_path = str(self.run_dir(run_id))
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO forge_runs (
                    run_id, goal, status, provider, model, model_version, config_hash,
                    progress, artifact_path, created_at, updated_at
                ) VALUES (?, ?, 'queued', ?, ?, ?, ?, 0.0, ?, ?, ?)
                """,
                (
                    run_id,
                    request.goal,
                    provider,
                    model,
                    model_version,
                    config_hash,
                    artifact_path,
                    now,
                    now,
                ),
            )
        return self.get_run(run_id) or {}

    def claim_next_queued_run(self) -> str | None:
        """Atomically transition the oldest queued run to `running` and return its run_id."""
        now = utc_now_iso()
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT run_id FROM forge_runs WHERE status = 'queued' ORDER BY created_at ASC LIMIT 1"
            ).fetchone()
            if row is None:
                conn.commit()
                return None
            run_id = str(row["run_id"])
            conn.execute(
                "UPDATE forge_runs SET status = 'running', progress = MAX(progress, 0.02), updated_at = ? WHERE run_id = ?",
                (now, run_id),
            )
            conn.commit()
            return run_id

    def requeue_interrupted_runs(self) -> list[str]:
        """Re-queue any runs left in `running` state after a process crash so they resume from checkpoints."""
        now = utc_now_iso()
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT run_id FROM forge_runs WHERE status = 'running'"
            ).fetchall()
            run_ids = [str(r["run_id"]) for r in rows]
            if run_ids:
                conn.execute(
                    "UPDATE forge_runs SET status = 'queued', updated_at = ? WHERE status = 'running'",
                    (now,),
                )
        return run_ids

    def update_run_status(
        self,
        run_id: str,
        *,
        status: RunStatus,
        progress: float | None = None,
        error: str | None = None,
        agent_name: str | None = None,
        agent_slug: str | None = None,
        domain: str | None = None,
        eval_pass_rate: float | None = None,
        eval_average_score: float | None = None,
        artifact_path: str | None = None,
    ) -> None:
        fields: list[str] = ["status = ?", "updated_at = ?"]
        values: list[Any] = [status, utc_now_iso()]

        optional_map = {
            "progress": progress,
            "error": error,
            "agent_name": agent_name,
            "agent_slug": agent_slug,
            "domain": domain,
            "eval_pass_rate": eval_pass_rate,
            "eval_average_score": eval_average_score,
            "artifact_path": artifact_path,
        }
        for col, val in optional_map.items():
            if val is not None:
                fields.append(f"{col} = ?")
                values.append(val)
        values.append(run_id)

        with self._lock, self._connect() as conn:
            conn.execute(
                f"UPDATE forge_runs SET {', '.join(fields)} WHERE run_id = ?",
                values,
            )

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM forge_runs WHERE run_id = ?", (run_id,)).fetchone()
        return dict(row) if row else None

    def list_runs(self, *, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM forge_runs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def append_event(self, event: PipelineEvent) -> int:
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO pipeline_events (
                    run_id, stage, event_type, message, progress, data_json, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.run_id,
                    event.stage,
                    event.event_type,
                    event.message,
                    event.progress,
                    json.dumps(event.data, ensure_ascii=False),
                    event.timestamp,
                ),
            )
            conn.execute(
                "UPDATE forge_runs SET progress = ?, updated_at = ? WHERE run_id = ?",
                (event.progress, utc_now_iso(), event.run_id),
            )
            return int(cur.lastrowid or 0)

    def list_events(self, run_id: str, *, after_id: int = 0) -> list[tuple[int, PipelineEvent]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, run_id, stage, event_type, message, progress, data_json, timestamp
                FROM pipeline_events
                WHERE run_id = ? AND id > ?
                ORDER BY id ASC
                """,
                (run_id, after_id),
            ).fetchall()
        out: list[tuple[int, PipelineEvent]] = []
        for row in rows:
            evt = PipelineEvent(
                run_id=row["run_id"],
                stage=row["stage"],
                event_type=row["event_type"],
                message=row["message"],
                progress=float(row["progress"]),
                data=json.loads(row["data_json"]),
                timestamp=row["timestamp"],
            )
            out.append((int(row["id"]), evt))
        return out

    def save_artifact(self, artifact: ForgeArtifact) -> Path:
        target = self.run_dir(artifact.run_id)
        artifact.save_to_dir(target)
        final_status: RunStatus = (
            "budget_exhausted" if artifact.status == "budget_exhausted" else "done"
        )
        self.update_run_status(
            artifact.run_id,
            status=final_status,
            progress=1.0,
            agent_name=artifact.agent_spec.name,
            agent_slug=artifact.agent_spec.slug,
            domain=artifact.agent_spec.domain,
            eval_pass_rate=artifact.eval_report.pass_rate,
            eval_average_score=artifact.eval_report.average_score,
            artifact_path=str(target),
        )
        return target

    def load_artifact(self, run_id: str) -> ForgeArtifact | None:
        run = self.get_run(run_id)
        artifact_dir = (
            Path(run["artifact_path"])
            if run and run.get("artifact_path")
            else self.artifacts_dir / run_id
        )
        if not (artifact_dir / "manifest.json").exists():
            return None
        return ForgeArtifact.load_from_dir(artifact_dir)

    def append_chat_message(self, run_id: str, session_id: str, msg: ChatMessage) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO chat_messages (
                    run_id, session_id, role, content, citations_json, tool_traces_json, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    session_id,
                    msg.role,
                    msg.content,
                    json.dumps(msg.citations, ensure_ascii=False),
                    json.dumps([t.model_dump() for t in msg.tool_traces], ensure_ascii=False),
                    msg.timestamp,
                ),
            )

    def get_chat_history(self, run_id: str, session_id: str, *, limit: int = 50) -> list[ChatMessage]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT role, content, citations_json, tool_traces_json, timestamp
                FROM chat_messages
                WHERE run_id = ? AND session_id = ?
                ORDER BY id ASC
                LIMIT ?
                """,
                (run_id, session_id, limit),
            ).fetchall()
        return [
            ChatMessage(
                role=row["role"],
                content=row["content"],
                citations=json.loads(row["citations_json"]),
                tool_traces=json.loads(row["tool_traces_json"]),
                timestamp=row["timestamp"],
            )
            for row in rows
        ]
