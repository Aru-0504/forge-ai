# Operations Runbook

## Configuration Quick Reference

Copy `.env.example` to `.env`. Key environment variables:

| Variable | Default | Purpose |
| --- | --- | --- |
| `FORGEAI_LLM_PROVIDER` | `stub` | `stub`, `ollama` (local review), or `openai_compat` (hosted demo). |
| `FORGEAI_LLM_MODEL` | `qwen2.5:7b-instruct` | Model identifier logged in every artifact. |
| `FORGEAI_LLM_SEED` | `42` | Fixed seed forwarded to Ollama/OpenAI-compatible backends. |
| `FORGEAI_MAX_IMPROVEMENT_LOOPS` | `3` | Maximum `generator → judge → critic` iterations. |
| `FORGEAI_STAGE_TIMEOUT_SECONDS` | `120.0` | Per-stage wall-clock timeout before retry/degradation. |
| `FORGEAI_MAX_TOKENS_PER_RUN` | `100000` | Hard token budget ceiling (`budget_exhausted` early stop). |
| `FORGEAI_MAX_LATENCY_SECONDS_PER_RUN` | `600.0` | Hard wall-clock latency budget ceiling. |
| `FORGEAI_HTTP_ALLOWED_HOSTS` | *(empty)* | Comma-separated host allowlist for runtime `http_get` tool. |
| `FORGEAI_RUN_EMBEDDED_WORKER` | `true` | Set `false` when running a dedicated `worker` container. |

## How to Read a Run's Artifacts

Every run writes a self-contained directory under `data/artifacts/<run_id>/` (or `demos/<run>/`):

| File | What to Inspect |
| --- | --- |
| `report.md` | Start here: executive scorecard, loop progression, per-stage tokens & latency, degraded stages, and eval results. |
| `scorecard.json` | Machine-readable pass rate, direct & indirect injection gates, per-stage token/latency metrics, `model_version`, `seed`, `prompt_versions`, `config_hash`. |
| `manifest.json` | Index of all artifact files, sources, chunk/table counts, and reproducibility metadata. |
| `agent_spec.json` & `system_prompt.md` | Final forged agent's persona, enabled tools, guardrails, and exact system prompt. |
| `eval_report.json` | Detailed per-case answers, tool traces, citations, and deterministic check booleans. |
| `groundability.json` | Coverage score, detected scripts (non-English flags), and ingestion warnings (e.g. scanned PDFs, CSV truncation). |
| `run_logs.jsonl` | Durable structured JSON logs with `run_id`, `stage`, and `loop` on every line. |

## Common Errors & Resolutions

| Error / Status | Cause | Fix |
| --- | --- | --- |
| `400 Empty submission` | No documents or URLs attached to `POST /runs`. | Upload at least one supported file or URL. |
| `No extractable text... Scanned or image-only PDF` | Uploaded PDF contains only raster images. | Run OCR preprocessing (e.g., `ocrmypdf`) before uploading. |
| `CSV truncated to N rows` (warning) | CSV exceeded `FORGEAI_MAX_CSV_ROWS`. | Expected safeguard; raise `FORGEAI_MAX_CSV_ROWS` if RAM permits. |
| `status = "budget_exhausted"` | Token, latency, or USD cost ceiling hit mid-run. | Inspect partial `report.md`; increase `FORGEAI_MAX_TOKENS_PER_RUN` or `FORGEAI_MAX_LATENCY_SECONDS_PER_RUN`. |
| `degraded_stages` non-empty in `report.md` | A stage timed out or failed twice and fell back gracefully. | Inspect `run_logs.jsonl` for the exact `stage` and `run_id`, then re-run or `POST /runs/{id}/resume`. |
| `URL blocked by security policy` | `http_get` target is private/internal IP or not in `FORGEAI_HTTP_ALLOWED_HOSTS`. | Add the approved public hostname to `FORGEAI_HTTP_ALLOWED_HOSTS`. |
