"""Command-line interface for ForgeAI (calls the same core package as the API)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import click
import uvicorn

from forgeai import __version__
from forgeai.config import Settings, get_settings
from forgeai.core.runtime.agent import ForgedAgentRuntime
from forgeai.core.schemas import ChatMessage, ForgeArtifact, ForgeRequest, SourceDocument
from forgeai.core.stages.pipeline import ForgePipeline
from forgeai.llm.base import create_llm_client
from forgeai.observability.logging import configure_logging
from forgeai.observability.metrics import RunBudget
from forgeai.service.jobs import run_standalone_worker


@click.group()
@click.version_option(version=__version__)
def main() -> None:
    """ForgeAI — AI Agent Factory CLI."""


@main.command("forge")
@click.option("--goal", "--brief", "goal", required=True, help="Brief/goal for the agent to forge.")
@click.option(
    "--source",
    "sources",
    multiple=True,
    required=True,
    type=click.Path(exists=True, path_type=Path),
    help="Path to a source document or folder of documents (repeatable).",
)
@click.option("--name", "agent_name_hint", default=None, help="Optional agent name hint.")
@click.option(
    "--out",
    "out_dir",
    required=True,
    type=click.Path(file_okay=False, path_type=Path),
    help="Directory to write the packaged ForgeArtifact.",
)
@click.option("--run-id", default=None, help="Optional deterministic run ID.")
@click.option("--max-loops", default=None, type=int, help="Max improvement loops (1-5).")
@click.option(
    "--provider",
    type=click.Choice(["stub", "ollama", "openai_compat"]),
    default=None,
    help="Override FORGEAI_LLM_PROVIDER.",
)
@click.option("--model", default=None, help="Override FORGEAI_LLM_MODEL.")
def forge_cmd(
    goal: str,
    sources: tuple[Path, ...],
    agent_name_hint: str | None,
    out_dir: Path,
    run_id: str | None,
    max_loops: int | None,
    provider: str | None,
    model: str | None,
) -> None:
    """Forge a domain-specific agent from local files/folders and save its artifacts."""
    settings = _build_settings(provider=provider, model=model)
    configure_logging(settings.log_level, "text")
    llm = create_llm_client(settings)

    docs: list[SourceDocument] = []
    for path in sources:
        if path.is_dir():
            for file_path in sorted(p for p in path.rglob("*") if p.is_file()):
                docs.append(SourceDocument(filename=file_path.name, content=file_path.read_bytes()))
        else:
            docs.append(SourceDocument(filename=path.name, content=path.read_bytes()))

    req = ForgeRequest(
        goal=goal,
        agent_name_hint=agent_name_hint,
        sources=docs,
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
        max_csv_rows=settings.max_csv_rows,
        max_loops=max_loops or settings.max_improvement_loops,
        target_score=settings.target_eval_score,
        redact_pii=settings.redact_pii,
        allowed_http_hosts=settings.allowed_http_hosts_list,
    )
    budget = RunBudget(
        max_tokens=settings.max_tokens_per_run,
        max_latency_seconds=settings.max_latency_seconds_per_run,
        max_cost_usd=settings.max_cost_usd_per_run,
        input_cost_per_1k=settings.input_cost_per_1k,
        output_cost_per_1k=settings.output_cost_per_1k,
    )
    pipeline = ForgePipeline(
        llm,
        budget=budget,
        config_hash=settings.config_hash(),
        max_upload_bytes=settings.max_upload_bytes,
        max_files=settings.max_files_per_run,
        stage_timeout_seconds=settings.stage_timeout_seconds,
    )

    def _print_event(evt) -> None:
        click.echo(f"[{evt.progress:5.0%}] ({evt.stage}) {evt.message}")

    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        artifact = pipeline.run(
            req,
            run_id=run_id,
            on_event=_print_event,
            checkpoint_dir=out_dir / "checkpoints",
            log_file=out_dir / "run_logs.jsonl",
        )
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc

    saved = artifact.save_to_dir(out_dir)
    click.echo(f"\nForged agent: {artifact.agent_spec.name} ({artifact.run_id}) [status={artifact.status}]")
    click.echo(f"Eval summary: {artifact.eval_report.summary}")
    click.echo(f"Artifacts written to: {saved}")


@main.command("chat")
@click.option(
    "--artifact",
    "artifact_dir",
    required=True,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Path to a packaged ForgeArtifact directory.",
)
@click.option("--question", "-q", default=None, help="Single question (non-interactive mode).")
@click.option(
    "--provider",
    type=click.Choice(["stub", "ollama", "openai_compat"]),
    default=None,
)
@click.option("--model", default=None)
def chat_cmd(
    artifact_dir: Path,
    question: str | None,
    provider: str | None,
    model: str | None,
) -> None:
    """Chat with a forged agent artifact interactively or via a single question."""
    settings = _build_settings(provider=provider, model=model)
    llm = create_llm_client(settings)
    artifact = ForgeArtifact.load_from_dir(artifact_dir)
    runtime = ForgedAgentRuntime.from_artifact(
        artifact,
        llm,
        allowed_http_hosts=settings.allowed_http_hosts_list,
    )

    if question:
        resp = runtime.answer(question)
        click.echo(resp.answer)
        if resp.citations:
            click.echo(f"\nCitations: {', '.join(resp.citations)}")
        return

    click.echo(f"Chatting with {artifact.agent_spec.name} ({artifact.agent_spec.domain})")
    click.echo("Type 'exit' or 'quit' to end.\n")
    history: list[ChatMessage] = []
    while True:
        user_input = click.prompt("You", prompt_suffix="> ").strip()
        if user_input.lower() in {"exit", "quit"}:
            break
        resp = runtime.answer(user_input, history=history)
        click.echo(f"\n{artifact.agent_spec.name}> {resp.answer}")
        if resp.citations:
            click.echo(f"  Citations: {', '.join(resp.citations)}")
        if resp.tool_traces:
            tools_str = ", ".join(t.tool_name for t in resp.tool_traces)
            click.echo(f"  Tools: {tools_str}")
        click.echo("")
        history.append(ChatMessage(role="user", content=user_input))
        history.append(
            ChatMessage(role="assistant", content=resp.answer, citations=resp.citations)
        )


@main.command("inspect")
@click.argument("artifact_dir", type=click.Path(exists=True, file_okay=False, path_type=Path))
def inspect_cmd(artifact_dir: Path) -> None:
    """Print a concise scorecard summary of a packaged ForgeArtifact directory."""
    artifact = ForgeArtifact.load_from_dir(artifact_dir)
    summary = {
        "run_id": artifact.run_id,
        "status": artifact.status,
        "agent_name": artifact.agent_spec.name,
        "domain": artifact.agent_spec.domain,
        "model": f"{artifact.provider}:{artifact.model} ({artifact.model_version})",
        "seed": artifact.seed,
        "config_hash": artifact.config_hash,
        "enabled_tools": artifact.agent_spec.enabled_tools,
        "sources": artifact.knowledge_index.sources,
        "chunks": len(artifact.knowledge_index.chunks),
        "tables": len(artifact.knowledge_index.tables),
        "loops_executed": artifact.scorecard.loops_executed,
        "eval_summary": artifact.eval_report.summary,
        "degraded_stages": [d.stage for d in artifact.degraded_stages],
        "total_tokens": artifact.scorecard.total_tokens,
        "total_latency_ms": artifact.scorecard.total_latency_ms,
    }
    click.echo(json.dumps(summary, indent=2))


@main.command("serve")
@click.option("--host", default=None, help="Bind host (default from FORGEAI_API_HOST).")
@click.option("--port", default=None, type=int, help="Bind port (default from FORGEAI_API_PORT).")
@click.option("--reload", "reload_app", is_flag=True, default=False)
def serve_cmd(host: str | None, port: int | None, reload_app: bool) -> None:
    """Run the ForgeAI FastAPI server."""
    settings = get_settings()
    uvicorn.run(
        "forgeai.service.api:app",
        host=host or settings.api_host,
        port=port or settings.api_port,
        reload=reload_app,
    )


@main.command("worker")
def worker_cmd() -> None:
    """Run a standalone ForgeAI background job worker polling the shared SQLite queue."""
    asyncio.run(run_standalone_worker())


def _build_settings(*, provider: str | None, model: str | None) -> Settings:
    base = get_settings()
    updates: dict[str, str] = {}
    if provider:
        updates["llm_provider"] = provider
    if model:
        updates["llm_model"] = model
    return base.model_copy(update=updates) if updates else base


if __name__ == "__main__":
    main()
