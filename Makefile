# Common commands. `make help` lists them.
.PHONY: help test lint types check fmt up down migrate ingest eval routing retrieval run worker

help:
	@grep -E '^[a-z-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-12s %s\n", $$1, $$2}'

test:  ## Run the test suite (database tests skip if none is running)
	.venv/bin/python -m pytest -q

lint:  ## Ruff
	.venv/bin/ruff check src tests

fmt:  ## Format
	.venv/bin/ruff format src tests

types:  ## mypy --strict
	.venv/bin/mypy src

check: lint types test  ## Everything CI runs

up:  ## Start PostgreSQL
	podman-compose up -d

down:  ## Stop PostgreSQL
	podman-compose down

migrate:  ## Apply migrations
	.venv/bin/alembic upgrade head

ingest:  ## Index docs/ into the corpus (~4 min, paced for the quota)
	.venv/bin/python -m amos.rag.cli ingest docs

run:  ## Start the API
	.venv/bin/python -m amos

worker:  ## Start a worker
	.venv/bin/python -m amos.worker

# --- measurements. These cost real API calls. Run deliberately. ---
eval:  ## End-to-end evaluation suite
	.venv/bin/python -m amos.evaluation.cli

retrieval:  ## Retrieval recall@k and MRR
	.venv/bin/python -m amos.rag.cli evaluate 5

routing:  ## Agent routing accuracy
	.venv/bin/python -m amos.agents.cli
