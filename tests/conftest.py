"""Shared pytest fixtures for ForgeAI unit and integration tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from forgeai.config import Settings, reset_settings_cache
from forgeai.llm.base import StubLLMClient

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _clean_settings_cache():
    reset_settings_cache()
    yield
    reset_settings_cache()


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES_DIR


@pytest.fixture
def hr_policy_bytes() -> bytes:
    return (FIXTURES_DIR / "hr_policy.md").read_bytes()


@pytest.fixture
def ship_spares_bytes() -> bytes:
    return (FIXTURES_DIR / "ship_spares.csv").read_bytes()


@pytest.fixture
def stub_llm() -> StubLLMClient:
    return StubLLMClient(model="stub-test-v1")


@pytest.fixture
def temp_settings(tmp_path: Path) -> Settings:
    data_dir = tmp_path / "data"
    return Settings(
        llm_provider="stub",
        llm_model="stub-test-v1",
        data_dir=data_dir,
        db_path=data_dir / "forgeai.db",
        artifacts_dir=data_dir / "artifacts",
        worker_concurrency=1,
        log_format="text",
    )
