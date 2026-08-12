.PHONY: help setup start test db-up db-down db-logs schema owner radar

help:
	@echo "ScholarRadar local commands"
	@echo "  make setup    Install Python packages, start PostgreSQL, apply schema, create owner, test"
	@echo "  make start    Start PostgreSQL and Streamlit"
	@echo "  make test     Run unit, database, and Streamlit smoke tests"
	@echo "  make db-up    Start local PostgreSQL"
	@echo "  make db-down  Stop local PostgreSQL without deleting its data"
	@echo "  make schema   Reapply the idempotent database schema"
	@echo "  make owner    Make DEV_USER_EMAIL the one site owner"

setup:
	bash scripts/setup_ubuntu.sh

start:
	bash scripts/start_local.sh

test:
	bash scripts/test_local.sh

db-up:
	docker compose up -d postgres

db-down:
	docker compose stop postgres

db-logs:
	docker compose logs -f postgres

schema:
	bash scripts/apply_schema.sh

owner:
	.venv/bin/python -m scripts.bootstrap_owner

radar:
	.venv/bin/python -m scripts.run_radar "$(AREA)"
