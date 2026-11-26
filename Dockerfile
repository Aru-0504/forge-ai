FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    FORGEAI_DATA_DIR=/app/data \
    FORGEAI_DB_PATH=/app/data/forgeai.db \
    FORGEAI_ARTIFACTS_DIR=/app/data/artifacts

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY ui ./ui
COPY demos ./demos

RUN pip install --upgrade pip \
    && pip install --no-cache-dir ".[ui]" \
    && useradd --create-home --shell /bin/bash forgeai \
    && mkdir -p /app/data/artifacts \
    && chown -R forgeai:forgeai /app

USER forgeai

EXPOSE 8000 8501

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)" || exit 1

CMD ["uvicorn", "forgeai.service.api:app", "--host", "0.0.0.0", "--port", "8000"]
