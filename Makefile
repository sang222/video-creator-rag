PYTHON ?= python3
VENV ?= .venv
BIN := $(VENV)/bin

.PHONY: install db-up db-down migrate seed test run health

install:
	$(PYTHON) -m venv $(VENV)
	$(BIN)/python -m pip install --upgrade pip
	$(BIN)/python -m pip install ".[dev]"

db-up:
	docker-compose up -d postgres

db-down:
	docker-compose down

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
