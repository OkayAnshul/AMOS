# Common commands. `make help` lists them.
.PHONY: help test lint types check fmt up down migrate ingest eval eval-baseline routing retrieval memory-trials run worker release attribution-check backup restore-drill restore

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

# --- backups (V1.5). pg_dump runs inside the container: the host has no Postgres client. ---
# The corpus costs a day of embedding quota to rebuild and seconds to restore.

PG := podman exec amos-postgres
# Row counts for every table in the public schema, one "table|count" line each.
COUNTS_SQL := SELECT table_name || '|' || (xpath('/row/c/text()', query_to_xml(format('SELECT count(*) AS c FROM %I', table_name), false, true, '')))[1]::text FROM information_schema.tables WHERE table_schema = 'public' AND table_type = 'BASE TABLE' ORDER BY convert_to(table_name, 'UTF8')
VECTOR_PROBE_SQL := SELECT count(*) FROM (SELECT id FROM chunks ORDER BY embedding <=> (SELECT embedding FROM chunks WHERE embedding IS NOT NULL LIMIT 1) LIMIT 5) s

backup:  ## Dump the database to backups/amos-<timestamp>.dump (custom format)
	@mkdir -p backups
	@f=backups/amos-$$(date +%Y%m%d-%H%M%S).dump; \
	 $(PG) pg_dump -U amos -Fc amos > "$$f.partial" || { echo "backup: pg_dump failed; $$f.partial is incomplete"; exit 1; }; \
	 mv "$$f.partial" "$$f" && echo "backup: $$f ($$(du -h "$$f" | cut -f1))"

restore-drill:  ## Restore the newest backup into a scratch database and compare row counts with amos
	@f=$$(ls -t backups/*.dump 2>/dev/null | head -1); \
	 test -n "$$f" || { echo "restore-drill: no backup, run make backup"; exit 1; }; \
	 echo "restore-drill: $$f -> amos_drill"; \
	 $(PG) dropdb -U amos --if-exists amos_drill && \
	 $(PG) createdb -U amos -T template0 amos_drill && \
	 podman exec -i amos-postgres pg_restore -U amos -d amos_drill --no-owner --exit-on-error < "$$f" && \
	 $(PG) psql -U amos -d amos -At -c "$(COUNTS_SQL)" > backups/counts-live.txt && \
	 $(PG) psql -U amos -d amos_drill -At -c "$(COUNTS_SQL)" > backups/counts-drill.txt && \
	 probe=$$($(PG) psql -U amos -d amos_drill -At -c "$(VECTOR_PROBE_SQL)"); \
	 status=$$?; \
	 $(PG) dropdb -U amos --if-exists amos_drill; \
	 test $$status -eq 0 || { echo "restore-drill: FAILED during restore"; exit 1; }; \
	 echo "  vector search in the restored copy returned $$probe of 5 rows"; \
	 printf '  %-20s %10s %10s\n' table amos amos_drill; \
	 LC_ALL=C join -t'|' -a1 -a2 -e MISSING -o 0,1.2,2.2 backups/counts-live.txt backups/counts-drill.txt \
	   | awk -F'|' '{ printf "  %-20s %10s %10s%s\n", $$1, $$2, $$3, ($$2 == $$3 ? "" : "   <- differs") }'; \
	 if cmp -s backups/counts-live.txt backups/counts-drill.txt; then echo "restore-drill: PASSED, every table matches"; \
	 else echo "restore-drill: FAILED, counts differ (was amos written to after the backup?)"; exit 1; fi

restore:  ## DESTRUCTIVE: replace the amos database with FILE=backups/<name>.dump (stop the API and worker first)
	@test -n "$(FILE)" && test -f "$(FILE)" || { echo "usage: make restore FILE=backups/amos-<timestamp>.dump"; exit 1; }
	@echo "This drops every object in the amos database and replaces it with $(FILE)."
	@read -p "Type 'amos' to continue: " answer && test "$$answer" = amos || { echo "restore: cancelled"; exit 1; }
	@podman exec -i amos-postgres pg_restore -U amos -d amos --clean --if-exists --no-owner --exit-on-error < "$(FILE)"
	@echo "restore: done. Run make migrate if the backup predates the current schema"

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
