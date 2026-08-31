.PHONY: help setup start worker worker-once rebuild-topics topic-status seed-topics seed-catalog production-check test db-up db-down db-logs search-up search-logs search-test schema backup owner radar

help:
	@echo "ScholarRadar local commands"
	@echo "  make setup    Install Python packages, start PostgreSQL, apply schema, create owner, test"
	@echo "  make start    Start PostgreSQL, the worker, Streamlit (SearXNG only when enabled)"
	@echo "  make worker   Run a standalone worker (do not combine with make start locally)"
	@echo "  make rebuild-topics  Queue outdated topics for exact-evidence rebuilding"
	@echo "  make topic-status    Show version and exact-evidence rebuild progress"
	@echo "  make seed-catalog    List the controlled research-area catalog"
	@echo "  make seed-topics     Queue the next 20 low-priority catalog jobs"
	@echo "  make production-check  Check launch-critical configuration and services"
	@echo "  make test     Run unit, database, and Streamlit smoke tests"
	@echo "  make db-up    Start local PostgreSQL"
	@echo "  make db-down  Stop local PostgreSQL without deleting its data"
	@echo "  make search-up    Start the private local SearXNG service"
	@echo "  make search-logs  Inspect SearXNG logs"
	@echo "  make search-test  Check the configured search provider (uses a search slot)"
	@echo "  make schema   Reapply the idempotent database schema"
	@echo "  make backup   Create a private timestamped local PostgreSQL backup"
	@echo "  make owner    Make DEV_USER_EMAIL the one site owner"

setup:
	bash scripts/setup_ubuntu.sh

start:
	bash scripts/start_local.sh

worker:
	.venv/bin/python -m scripts.run_worker

worker-once:
	.venv/bin/python -m scripts.run_worker --once

rebuild-topics:
	.venv/bin/python -m scripts.rebuild_topics

topic-status:
	.venv/bin/python -m scripts.rebuild_topics --status

seed-catalog:
	.venv/bin/python -m scripts.seed_topics

seed-topics:
	.venv/bin/python -m scripts.seed_topics --queue --limit 20

production-check:
	.venv/bin/python -m scripts.check_production

test:
	bash scripts/test_local.sh

db-up:
	docker compose up -d postgres

db-down:
	docker compose stop postgres

db-logs:
	docker compose logs -f postgres

search-up:
	docker compose up -d searxng

search-logs:
	docker compose logs -f searxng

search-test:
	.venv/bin/python -m scripts.check_search

schema:
	bash scripts/apply_schema.sh

backup:
	bash scripts/backup_db.sh

owner:
	.venv/bin/python -m scripts.bootstrap_owner

radar:
	.venv/bin/python -m scripts.run_radar "$(AREA)"
