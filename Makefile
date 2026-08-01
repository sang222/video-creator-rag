PYTHON ?= python3
VENV ?= .venv
BIN := $(VENV)/bin

.PHONY: install db-up db-down api-up worker-up frontend-up frontend-logs docker-build docker-migrate docker-seed migrate seed test run health

install:
	$(PYTHON) -m venv $(VENV)
	$(BIN)/python -m pip install --upgrade pip
	$(BIN)/python -m pip install ".[dev]"

db-up:
	docker-compose up -d postgres

db-down:
	docker-compose down

api-up:
	docker compose up -d --build --wait postgres api production-workflow-worker

worker-up:
	docker compose up -d --build production-workflow-worker

frontend-up:
	docker-compose up -d frontend

frontend-logs:
	docker-compose logs -f frontend

docker-build:
	docker-compose build api frontend

docker-migrate:
	docker-compose run --rm api alembic upgrade head

docker-seed:
	docker-compose run --rm api vcos config seed

migrate:
	$(BIN)/alembic upgrade head

seed:
	$(BIN)/vcos config seed

test:
	$(BIN)/pytest

run:
	$(BIN)/uvicorn app.main:app --reload

health:
	$(BIN)/vcos health
