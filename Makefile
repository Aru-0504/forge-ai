.PHONY: install dev run worker ui test lint typecheck check demo verify docker-up docker-ollama docker-down clean

PYTHON ?= python

install:
	$(PYTHON) -m pip install -e .

dev:
	$(PYTHON) -m pip install -e ".[dev,ui]"

run:
	$(PYTHON) -m forgeai.cli serve --host 0.0.0.0 --port 8000

worker:
	$(PYTHON) -m forgeai.cli worker

ui:
	streamlit run ui/app.py

test:
	$(PYTHON) -m pytest -v

lint:
	$(PYTHON) -m ruff check src tests ui scripts

typecheck:
	$(PYTHON) -m mypy src

check: lint typecheck test

demo:
	$(PYTHON) scripts/run_demos.py

verify:
	$(PYTHON) scripts/verify_e2e.py

docker-up:
	docker compose up --build

docker-ollama:
	docker compose --profile ollama up --build

docker-down:
	docker compose down

clean:
	$(PYTHON) -c "import pathlib, shutil; [shutil.rmtree(p, ignore_errors=True) for p in ['.pytest_cache', '.mypy_cache', '.ruff_cache', '.tmp_demo_service', 'build', 'dist'] if pathlib.Path(p).exists()]"
