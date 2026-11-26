"""Environment-driven configuration for ForgeAI."""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables and `.env`."""

    model_config = SettingsConfigDict(
        env_prefix="FORGEAI_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Environment & logging
    env: Literal["development", "staging", "production"] = "development"
    log_level: str = "INFO"
    log_format: Literal["json", "text"] = "json"

    # LLM configuration
    llm_provider: Literal["stub", "ollama", "openai_compat"] = "stub"
    llm_model: str = "qwen2.5:7b-instruct"
    llm_model_version: str = "latest"
    llm_temperature: float = Field(default=0.1, ge=0.0, le=2.0)
    llm_seed: int = Field(default=42, ge=0)
    llm_max_tokens: int = Field(default=2048, ge=64, le=32768)
    llm_timeout_seconds: float = Field(default=60.0, gt=0.0)
    llm_max_retries: int = Field(default=3, ge=0, le=10)

    # Provider endpoints & credentials
    ollama_base_url: str = "http://localhost:11434"
    openai_base_url: str = "https://api.openai.com/v1"
    openai_api_key: str = ""

    # Pipeline loops, timeouts, and budgets (token + latency + cost)
    max_improvement_loops: int = Field(default=3, ge=1, le=5)
    target_eval_score: float = Field(default=0.9, ge=0.1, le=1.0)
    stage_timeout_seconds: float = Field(default=120.0, gt=0.0)
    max_tokens_per_run: int = Field(default=100_000, ge=500)
    max_latency_seconds_per_run: float = Field(default=600.0, gt=0.0)
    max_cost_usd_per_run: float = Field(default=2.0, gt=0.0)
    input_cost_per_1k: float = Field(default=0.0005, ge=0.0)
    output_cost_per_1k: float = Field(default=0.0015, ge=0.0)

    # Storage paths
    data_dir: Path = Path("./data")
    db_path: Path = Path("./data/forgeai.db")
    artifacts_dir: Path = Path("./data/artifacts")

    # Security limits & allowlists
    max_upload_mb: int = Field(default=10, ge=1, le=200)
    max_files_per_run: int = Field(default=20, ge=1, le=200)
    max_csv_rows: int = Field(default=5000, ge=10, le=100_000)
    allow_private_urls: bool = False
    http_allowed_hosts: str = ""  # comma-separated allowlist for runtime http_get tool
    redact_pii: bool = True

    # Chunking / retrieval defaults
    chunk_size: int = Field(default=900, ge=100, le=8000)
    chunk_overlap: int = Field(default=150, ge=0, le=2000)
    retrieval_top_k: int = Field(default=5, ge=1, le=20)

    # Service & worker settings
    api_host: str = "0.0.0.0"
    api_port: int = Field(default=8000, ge=1, le=65535)
    worker_concurrency: int = Field(default=2, ge=1, le=16)
    worker_poll_interval: float = Field(default=0.25, gt=0.01)
    run_embedded_worker: bool = True
    api_key: str = ""
    api_base_url: str = "http://localhost:8000"

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def allowed_http_hosts_list(self) -> list[str]:
        return [h.strip().lower() for h in self.http_allowed_hosts.split(",") if h.strip()]

    def config_hash(self) -> str:
        """Deterministic SHA-256 hash of reproducibility-relevant settings (excluding secrets)."""
        relevant = {
            "llm_provider": self.llm_provider,
            "llm_model": self.llm_model,
            "llm_model_version": self.llm_model_version,
            "llm_temperature": self.llm_temperature,
            "llm_seed": self.llm_seed,
            "llm_max_tokens": self.llm_max_tokens,
            "max_improvement_loops": self.max_improvement_loops,
            "target_eval_score": self.target_eval_score,
            "chunk_size": self.chunk_size,
            "chunk_overlap": self.chunk_overlap,
            "max_csv_rows": self.max_csv_rows,
            "redact_pii": self.redact_pii,
        }
        canonical = json.dumps(relevant, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]

    def ensure_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached settings instance."""
    return Settings()


def reset_settings_cache() -> None:
    """Clear the cached settings instance (useful in tests)."""
    get_settings.cache_clear()
