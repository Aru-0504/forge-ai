FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/home/forgeai \
    PORT=7860 \
    FORGEAI_DATA_DIR=/app/data \
    FORGEAI_DB_PATH=/app/data/forgeai.db \
    FORGEAI_ARTIFACTS_DIR=/app/data/artifacts \
    FORGEAI_RUN_EMBEDDED_WORKER=true \
    FORGEAI_API_BASE_URL=http://127.0.0.1:8000 \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY ui ./ui
COPY demos ./demos
COPY scripts/start_space.sh /app/start_space.sh

RUN pip install --upgrade pip \
    && pip install --no-cache-dir ".[ui]" \
    && sed -i 's/\r$//' /app/start_space.sh \
    && chmod +x /app/start_space.sh \
    && useradd --create-home --uid 1000 --shell /bin/bash forgeai \
    && mkdir -p /app/data/artifacts /home/forgeai/.streamlit \
    && chown -R forgeai:forgeai /app /home/forgeai

USER forgeai

EXPOSE 7860 8000 8501

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)" || exit 1

CMD ["/app/start_space.sh"]
