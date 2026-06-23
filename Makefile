PYTHON ?= python3
API_DIR := apps/api
WEB_DIR := apps/web
COMPOSE := $(shell if docker compose version >/dev/null 2>&1; then echo "docker compose"; else echo "docker-compose"; fi)

.PHONY: setup dev test lint format seed demo down

setup:
	cd $(API_DIR) && uv sync
	pnpm install
	@echo "setup ok"

dev:
	$(COMPOSE) up --build

test:
	cd $(API_DIR) && PYTHONPATH=src uv run pytest -q
	cd $(WEB_DIR) && pnpm test

lint:
	cd $(API_DIR) && uv run ruff check src tests ../../scripts
	cd $(API_DIR) && PYTHONPATH=src uv run mypy src
	cd $(WEB_DIR) && pnpm lint
	@echo "lint ok"

format:
	cd $(API_DIR) && uv run ruff format src tests ../../scripts
	cd $(WEB_DIR) && pnpm format

seed:
	cd $(API_DIR) && PYTHONPATH=src uv run python ../../scripts/seed_demo.py

demo:
	cd $(API_DIR) && PYTHONPATH=src uv run python ../../scripts/run_demo.py

down:
	$(COMPOSE) down --remove-orphans
