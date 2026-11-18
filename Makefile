# Common commands. `make help` lists them.
.PHONY: help test lint types check fmt up down migrate ingest eval eval-baseline routing retrieval memory-trials run worker release attribution-check

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

release:  ## Tag a release: make release VERSION=1.3.0
	@test -n "$(VERSION)" || (echo "usage: make release VERSION=1.3.0"; exit 1)
	@sed -i 's/^__version__ = ".*"$$/__version__ = "$(VERSION)"/' src/amos/__init__.py
	@$(MAKE) --no-print-directory check
	@git add src/amos/__init__.py
	@# --allow-empty-message is wrong here; what we want is to tolerate the
	@# version ALREADY being $(VERSION), which happens whenever the bump was
	@# committed separately. Nothing to commit is a valid state, not an error.
	@git diff --cached --quiet || git commit -m "chore: version $(VERSION)"
	@git tag -a "v$(VERSION)" -m "v$(VERSION)"
	@echo "tagged v$(VERSION) — push with: git push origin main --tags"

# --- attribution. Everything pushed is authored by OkayAnshul, with no assistant name or trailer. ---
# The pattern is spelled with a bracket so this file does not match itself.

attribution-check:  ## Fail if tracked files or unpushed commits name the assistant or carry a co-author trailer
	@if git grep -I -n -i -E '[c]laude' -- . ; then echo "attribution-check: the tracked files above name the assistant"; exit 1; fi
	@base=$$(git rev-parse --verify -q origin/main || git rev-list --max-parents=0 HEAD); \
	 bad=$$(git log --format='%an%n%ae%n%cn%n%ce%n%B' $$base..HEAD | grep -i -E '[c]laude|^[c]o-authored-by:'); \
	 if [ -n "$$bad" ]; then echo "$$bad"; echo "attribution-check: unpushed commits carry the lines above"; exit 1; fi; \
	 others=$$(git log --format='%an' $$base..HEAD | sort -u | grep -v -x 'OkayAnshul'); \
	 if [ -n "$$others" ]; then echo "attribution-check: unexpected author(s): $$others"; exit 1; fi
	@echo "attribution-check: clean"
