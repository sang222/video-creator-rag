PYTHON ?= python3
VENV ?= .venv
BIN := $(VENV)/bin

.PHONY: install db-up db-down ollama-up ollama-logs ollama-health ollama-pull-cloud-models migrate seed test run health

install:
	$(PYTHON) -m venv $(VENV)
	$(BIN)/python -m pip install --upgrade pip
	$(BIN)/python -m pip install ".[dev]"

db-up:
	docker-compose up -d postgres

db-down:
	docker-compose down

ollama-up:
	docker-compose up -d ollama

ollama-logs:
	docker-compose logs -f ollama

ollama-health:
	curl -fsS http://localhost:11434/api/tags

ollama-pull-cloud-models:
	docker-compose up -d ollama
	docker-compose exec -T ollama sh -lc 'set -u; models="$$(env | awk -F= '\''/^VCOS_LLM_MODEL_/ {print $$2}'\'' | sort -u)"; test -n "$$models"; failed=""; for model in $$models; do echo "Pulling $$model"; if OLLAMA_HOST=http://127.0.0.1:11434 ollama pull "$$model"; then echo "Pulled $$model"; else echo "Failed $$model"; failed="$$failed $$model"; fi; done; OLLAMA_HOST=http://127.0.0.1:11434 ollama list; if test -n "$$failed"; then echo "Failed models:$$failed"; exit 1; fi'

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
