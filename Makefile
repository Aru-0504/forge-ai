.PHONY: install dev run worker ui test lint typecheck check demo docker-up docker-ollama docker-down clean

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
	$(PYTHON) -m ruff check src tests ui

typecheck:
	$(PYTHON) -m mypy src

check: lint typecheck test

demo:
	$(PYTHON) demos/run_demos.py

docker-up:
	docker compose up --build

docker-ollama:
	docker compose --profile ollama up --build

docker-down:
	docker compose down

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache build dist *.egg-info
