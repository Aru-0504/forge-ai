"""SQLite-backed resumable background worker pool (runs embedded in API or as standalone worker)."""

from __future__ import annotations

import asyncio
import concurrent.futures
import signal
import uuid

from forgeai.config import Settings, get_settings
from forgeai.core.schemas import ForgeRequest, RequirementsDoc
from forgeai.core.stages.pipeline import ForgePipeline, HumanApprovalRequired
from forgeai.llm.base import BaseLLMClient, create_llm_client
from forgeai.observability.logging import configure_logging, get_logger
from forgeai.observability.metrics import MetricsRegistry, RunBudget
from forgeai.service.events import EventBus
from forgeai.service.store import ForgeStore

logger = get_logger("service.jobs")


class ForgeJobQueue:
    """SQLite-backed job queue & worker pool supporting crash recovery and resume."""

    def __init__(
        self,
        *,
        settings: Settings,
        llm: BaseLLMClient,
        store: ForgeStore,
        event_bus: EventBus,
        registry: MetricsRegistry,
    ) -> None:
        self.settings = settings
        self.llm = llm
        self.store = store
        self.event_bus = event_bus
        self.registry = registry

        self._workers: list[asyncio.Task[None]] = []
        self._wake_event = asyncio.Event()
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=settings.worker_concurrency,
            thread_name_prefix="forgeai-worker",
        )
        self._started = False
        self._done_events: dict[str, asyncio.Event] = {}

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        requeued = self.store.requeue_interrupted_runs()
        if requeued:
            logger.info("Re-queued %d interrupted run(s) for checkpoint resume: %s", len(requeued), requeued)
        for idx in range(self.settings.worker_concurrency):
            task = asyncio.create_task(self._worker_loop(idx), name=f"forgeai-worker-{idx}")
            self._workers.append(task)
        self._wake_event.set()
        logger.info("Started %d ForgeAI background workers", len(self._workers))

    async def stop(self) -> None:
        if not self._started:
            return
        self._started = False
        for task in self._workers:
            task.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()
        self._executor.shutdown(wait=False, cancel_futures=True)

    async def enqueue(self, request: ForgeRequest, *, run_id: str | None = None) -> str:
        rid = run_id or f"run_{uuid.uuid4().hex[:12]}"
        self.store.create_run(
            run_id=rid,
            request=request,
            provider=self.llm.provider_name,
            model=self.llm.model,
            model_version=self.llm.model_version,
            config_hash=self.settings.config_hash(),
        )
        self._done_events[rid] = asyncio.Event()
        self._wake_event.set()
        self.registry.inc("forge_jobs_enqueued")
        return rid

    async def resume(self, run_id: str) -> bool:
        """Re-queue an existing failed or interrupted run from its saved stage checkpoints."""
        run = self.store.get_run(run_id)
        if run is None:
            return False
        if self.store.load_request(run_id) is None:
            return False
        self.store.update_run_status(run_id, status="queued", error="")
        self._done_events[run_id] = asyncio.Event()
        self._wake_event.set()
        return True

    async def approve(
        self,
        run_id: str,
        edited_requirements: RequirementsDoc | None = None,
    ) -> RequirementsDoc | None:
        """Approve/edit requirements for a paused Human Checkpoint run and wake workers."""
        approved = self.store.approve_requirements(run_id, edited=edited_requirements)
        if approved is None:
            return None
        self._done_events[run_id] = asyncio.Event()
        self._wake_event.set()
        return approved

    async def wait_for(self, run_id: str, timeout: float = 30.0) -> dict | None:
        evt = self._done_events.get(run_id)
        if evt is not None:
            await asyncio.wait_for(evt.wait(), timeout=timeout)
        return self.store.get_run(run_id)

    async def _worker_loop(self, worker_id: int) -> None:
        loop = asyncio.get_running_loop()
        while True:
            run_id = self.store.claim_next_queued_run()
            if run_id is None:
                self._wake_event.clear()
                try:
                    await asyncio.wait_for(
                        self._wake_event.wait(),
                        timeout=self.settings.worker_poll_interval,
                    )
                except TimeoutError:
                    pass
                continue

            try:
                await loop.run_in_executor(self._executor, self._execute_run, run_id)
            except Exception as exc:
                logger.exception("Worker %d failed on run %s: %s", worker_id, run_id, exc)
            finally:
                done = self._done_events.get(run_id)
                if done is not None:
                    done.set()

    def _execute_run(self, run_id: str) -> None:
        request = self.store.load_request(run_id)
        if request is None:
            self.store.update_run_status(
                run_id,
                status="failed",
                progress=1.0,
                error="Persisted request.json missing; cannot execute or resume run.",
            )
            self.registry.inc("forge_runs_failed")
            return

        run_folder = self.store.run_dir(run_id)
        checkpoint_dir = run_folder / "checkpoints"
        log_file = run_folder / "run_logs.jsonl"

        budget = RunBudget(
            max_tokens=self.settings.max_tokens_per_run,
            max_latency_seconds=self.settings.max_latency_seconds_per_run,
            max_cost_usd=self.settings.max_cost_usd_per_run,
            input_cost_per_1k=self.settings.input_cost_per_1k,
            output_cost_per_1k=self.settings.output_cost_per_1k,
        )
        pipeline = ForgePipeline(
            self.llm,
            budget=budget,
            config_hash=self.settings.config_hash(),
            max_upload_bytes=self.settings.max_upload_bytes,
            max_files=self.settings.max_files_per_run,
            stage_timeout_seconds=self.settings.stage_timeout_seconds,
        )
        try:
            artifact = pipeline.run(
                request,
                run_id=run_id,
                on_event=self.event_bus.publish,
                checkpoint_dir=checkpoint_dir,
                log_file=log_file,
            )
            self.store.save_artifact(artifact)
            self.registry.inc(f"forge_runs_{artifact.status}")
        except HumanApprovalRequired as pause:
            self.store.save_pending_approval(run_id, pause.requirements, pause.groundability)
            self.registry.inc("forge_runs_awaiting_approval")
            logger.info("Run %s paused at Human Checkpoint awaiting approval", run_id)
        except Exception as exc:
            self.store.update_run_status(
                run_id,
                status="failed",
                progress=1.0,
                error=str(exc),
            )
            self.registry.inc("forge_runs_failed")
            raise


async def run_standalone_worker(settings: Settings | None = None) -> None:
    """Run a standalone worker process polling the shared SQLite job store."""
    cfg = settings or get_settings()
    cfg.ensure_directories()
    configure_logging(cfg.log_level, cfg.log_format)

    llm = create_llm_client(cfg)
    store = ForgeStore(db_path=cfg.db_path, artifacts_dir=cfg.artifacts_dir)
    event_bus = EventBus(store)
    registry = MetricsRegistry()
    queue = ForgeJobQueue(
        settings=cfg,
        llm=llm,
        store=store,
        event_bus=event_bus,
        registry=registry,
    )
    stop_event = asyncio.Event()

    def _handle_stop(*_args) -> None:
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _handle_stop)
        except (ValueError, OSError):
            pass

    await queue.start()
    logger.info("ForgeAI standalone worker running (db=%s)", cfg.db_path)
    try:
        await stop_event.wait()
    finally:
        await queue.stop()


if __name__ == "__main__":
    asyncio.run(run_standalone_worker())
