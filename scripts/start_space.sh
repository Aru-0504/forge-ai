#!/usr/bin/env bash
set -euo pipefail

export FORGEAI_RUN_EMBEDDED_WORKER="${FORGEAI_RUN_EMBEDDED_WORKER:-true}"
export FORGEAI_API_BASE_URL="${FORGEAI_API_BASE_URL:-http://127.0.0.1:8000}"
SPACE_PORT="${PORT:-7860}"

echo "[ForgeAI Space] Starting FastAPI backend + embedded worker on 127.0.0.1:8000..."
uvicorn forgeai.service.api:app --host 127.0.0.1 --port 8000 &
API_PID=$!

cleanup() {
    echo "[ForgeAI Space] Shutting down FastAPI (PID ${API_PID})..."
    kill "${API_PID}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "[ForgeAI Space] Waiting for FastAPI /healthz..."
for _ in $(seq 1 50); do
    if python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)" 2>/dev/null; then
        echo "[ForgeAI Space] FastAPI is healthy."
        break
    fi
    sleep 0.5
done

echo "[ForgeAI Space] Launching Streamlit Control Room on 0.0.0.0:${SPACE_PORT}..."
exec streamlit run ui/app.py \
    --server.port="${SPACE_PORT}" \
    --server.address=0.0.0.0 \
    --server.headless=true \
    --server.enableCORS=false \
    --server.enableXsrfProtection=false \
    --browser.gatherUsageStats=false
