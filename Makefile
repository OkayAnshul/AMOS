# Common commands. `make help` lists them.
.PHONY: help test lint types check fmt up down migrate ingest eval eval-baseline routing retrieval memory-trials run worker release

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
eval:  ## End-to-end evaluation suite (compares against engineering/eval-baseline.json)
	.venv/bin/python -m amos.evaluation.cli

eval-baseline:  ## Run the suite AND overwrite the stored baseline. Deliberate act.
	.venv/bin/python -m amos.evaluation.cli --update-baseline

retrieval:  ## Retrieval recall@k and MRR
	.venv/bin/python -m amos.rag.cli evaluate 5

memory-trials:  ## Memory storage reliability (capture a baseline with AMOS_MEMORY_RECONCILE_ENABLED=false first)
	.venv/bin/python -m amos.memory.cli 2

routing:  ## Agent routing accuracy
	.venv/bin/python -m amos.agents.cli

# --- release. Bump, verify, commit, tag — in that order, every time. ---

release:  ## Tag a release: make release VERSION=1.2.0
	@test -n "$(VERSION)" || (echo "usage: make release VERSION=1.2.0"; exit 1)
	@sed -i 's/^__version__ = ".*"$$/__version__ = "$(VERSION)"/' src/amos/__init__.py
	@$(MAKE) --no-print-directory check
	@git add src/amos/__init__.py
	@git commit -m "chore: version $(VERSION)"
	@git tag -a "v$(VERSION)" -m "v$(VERSION)"
	@echo "tagged v$(VERSION) — push with: git push origin main --tags"
