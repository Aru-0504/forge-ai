# ForgeAI

**Live Demo (Render Free Tier)**: https://forgeai-d7vu.onrender.com

**ForgeAI** is a deployable **AI Agent Factory**: give it a brief and domain documents (`.md`, `.txt`, `.csv`, `.json`, `.yaml`, `.pdf`, `.docx`, `.html`), and its 7-stage iterative pipeline ingests, conducts a ≤5-question clarifying interview, checks groundability, generates ≥20 domain/refusal/edge-case/security evals, runs an optional 3-candidate Prompt Tournament, iteratively improves the agent via targeted Critic–Optimizer fixes (up to 3 loops), and packages a reproducible, citation-first specialist agent behind a FastAPI service, background job worker, CLI, and Streamlit Control Room UI.

## One-Command Quickstart

```bash
# Install & run tests (offline stub model, no GPU required)
make dev && make test

# Run the FastAPI service (with embedded SQLite job worker)
make run

# In a second terminal, launch the thin Streamlit Factory + Chat UI
make ui

# Or run the full multi-container stack (API + Worker + UI)
make docker-up
# With optional local Ollama container:
make docker-ollama
```

## Architecture & Service Boundaries

- **Pure Core Library (`src/forgeai/core/`)** — zero HTTP or UI imports. Implements the 7 stages (`intake`, `groundability`, `evalgen`, `generator`, `judge`, `critic`, `report`), multi-format ingestion, hybrid BM25 + TF-IDF retrieval, deterministic tools, and `ForgedAgentRuntime`.
- **FastAPI Service (`src/forgeai/service/`)** — wraps the core behind resumable SQLite-backed background jobs (`queued`, `running`, `awaiting_approval`, `done`, `failed`, `budget_exhausted`):
  - `POST /interview` — preview ≤5 corpus-grounded clarifying questions before launching a run
  - `POST /runs` — submit brief + uploaded docs (multipart or JSON; supports `require_human_approval=true` and `enable_prompt_tournament=true`)
  - `GET /runs/{id}` — run status, pending approval requirements, scorecard, report, and artifacts
  - `GET /runs/{id}/events` — live progress over Server-Sent Events (SSE) with SQLite replay
  - `POST /runs/{id}/approve` — Human Checkpoint: approve or edit the structured requirements document before generation proceeds
  - `POST /runs/{id}/resume` — resume an interrupted or failed run from saved stage checkpoints
  - `POST /runs/{id}/regress` — Regression Mode: regenerate agent on updated docs and re-run the old eval set to report what broke
  - `POST /agents/{id}/chat` — chat with a forged agent (SQLite session history)
  - `GET /healthz` — liveness & model/config metadata (`GET /metrics` for JSON/Prometheus counters)
- **Thin UI (`ui/app.py`)** — 4-workspace Streamlit Control Room (Agent Factory + Human Approval Editor, Scorecard Inspector, Regression & Baseline Diff, Chat Runner).
- **CLI (`src/forgeai/cli.py`)** — `forgeai forge` (supports `--interactive` ≤5 clarifying Q&A + requirements approval and `--tournament` 3-candidate Prompt Tournament), `forgeai regress`, `forgeai compare`, `forgeai chat`, `forgeai inspect`, `forgeai serve`, `forgeai worker`.

## Model Backends & Hosting Tradeoff

One `BaseLLMClient` interface supports two real backends (plus an offline `stub` backend for CI):

| Mode | `FORGEAI_LLM_PROVIDER` | Intended Use |
| --- | --- | --- |
| **Local Open Model** | `ollama` | Local / unattended review (e.g. `qwen2.5:7b-instruct` via `http://localhost:11434`). |
| **Hosted Open Model** | `openai_compat` | Deployed demo on free-tier hosting (e.g. Groq, OpenRouter, Together, or Fireworks serving an open 7B–8B model). |
| **Offline Stub** | `stub` | Deterministic CI smoke tests and instant zero-key local runs. |

> **Hosting Tradeoff**: Free-tier cloud hosts (Render, Railway, Fly.io) do not provide GPUs and cannot run a 7B model at usable latency on shared CPU. For unattended local review, run with `FORGEAI_LLM_PROVIDER=ollama`; for hosted deployment, configure `FORGEAI_LLM_PROVIDER=openai_compat` pointing at a free-tier open-model endpoint. Every stage is kept compact and JSON-repaired so small open models run reliably on both backends, and the exact model name, version, seed, prompt versions, and config hash are recorded in every run's `manifest.json` and `scorecard.json`.

## Hosted Deployment (Free Tier)

- **Hosted URL (Render)**: https://forgeai-d7vu.onrender.com (single-container Docker Blueprint via [render.yaml](render.yaml) running FastAPI + embedded SQLite worker + Streamlit UI)
- **Recommended Free-Tier Env**:
  - `FORGEAI_LLM_PROVIDER=openai_compat` (or `stub` for zero-key demo mode)
  - `FORGEAI_OPENAI_BASE_URL=https://api.groq.com/openai/v1` (or OpenRouter)
  - `FORGEAI_LLM_MODEL=llama-3.1-8b-instant` (or `qwen/qwen-2.5-7b-instruct`)
  - `FORGEAI_RUN_EMBEDDED_WORKER=true` (single-container free tier)
  - `FORGEAI_MAX_TOKENS_PER_RUN=250000`, `FORGEAI_MAX_LATENCY_SECONDS_PER_RUN=180`
- **Free-Tier Limits**: Ephemeral filesystem resets on cold restart unless a persistent volume is mounted at `/app/data`; Render free tier spins down after ~15 minutes of inactivity (~30–50s cold wake); `FORGEAI_MAX_UPLOAD_MB=10` and `FORGEAI_MAX_CSV_ROWS=5000` protect 512 MB RAM containers.

## Committed Real-Service Demos

Both full demos were executed through the real FastAPI service (`make demo` / [scripts/run_demos.py](scripts/run_demos.py)) with complete artifacts (`manifest.json`, `requirements.md`, `scorecard.json`, `prompt_tournament.json`, `report.md`, `eval_cases.json`, `eval_report.json`, `agent_spec.json`, `knowledge_index.json`, `run_logs.jsonl`, `sse_events.txt`, `sample_transcript.md`) committed:

- [`demos/run1_hr_policy/`](demos/run1_hr_policy/) — **HR Policy Advisor** (2-doc HR corpus, 20 eval cases, 3-candidate Prompt Tournament, honest `95%` pass rate with residual failure report)
- [`demos/run2_ship_spares/`](demos/run2_ship_spares/) — **Ship Spares Operations Specialist** (CSV catalog + SOP, 20 eval cases, 3-candidate Prompt Tournament, honest `95%` pass rate)
- [`demos/baseline_comparison/`](demos/baseline_comparison/comparison_report.md) — **Factory vs. Human Baseline** head-to-head evaluation report (`95%` factory vs `80%` hand-written baseline across 20 eval cases, with candid wins/losses)

## Documentation

- [Architecture](docs/architecture.md)
- [Design Decisions](docs/design_decisions.md)
- [Operations Runbook](docs/runbook.md) — config, common errors, and how to read a run's artifacts
- [Security Model](docs/security.md)
