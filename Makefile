.PHONY: help install install-dev run worker beat test coverage lint \
       docker-up docker-down docker-build docker-logs \
       db-migrate db-upgrade db-downgrade db-history \
       clean

# ── Defaults ────────────────────────────────────────────────
PYTHON   ?= python
APP       = app.main:app
CELERY    = app.worker.celery_app
QUEUES    = analysis,signals,execution,maintenance,notifications

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

# ── Install ─────────────────────────────────────────────────
install: ## Install production dependencies
	pip install --upgrade pip && pip install .

install-dev: ## Install with dev dependencies
	pip install --upgrade pip && pip install ".[dev]"

# ── Local Dev ───────────────────────────────────────────────
run: ## Start the API server (uvicorn, reload)
	uvicorn $(APP) --reload --host 0.0.0.0 --port 8000

worker: ## Start Celery worker
	celery -A $(CELERY) worker -l info -Q $(QUEUES)

beat: ## Start Celery beat scheduler
	celery -A $(CELERY) beat -l info

# ── Testing ─────────────────────────────────────────────────
test: ## Run tests
	$(PYTHON) -m pytest tests/ -v

coverage: ## Run tests with coverage report
	$(PYTHON) -m coverage run -m pytest tests/ -v && \
	$(PYTHON) -m coverage report -m

# ── Linting ─────────────────────────────────────────────────
lint: ## Run ruff linter
	$(PYTHON) -m ruff check app/ tests/

format: ## Auto-format with ruff
	$(PYTHON) -m ruff format app/ tests/

# ── Database (Alembic) ──────────────────────────────────────
db-migrate: ## Create a new migration (usage: make db-migrate msg="add users table")
	alembic revision --autogenerate -m "$(msg)"

db-upgrade: ## Apply all pending migrations
	alembic upgrade head

db-downgrade: ## Rollback one migration
	alembic downgrade -1

db-history: ## Show migration history
	alembic history --verbose

# ── Docker ──────────────────────────────────────────────────
docker-build: ## Build all Docker images
	docker compose build

docker-up: ## Start all services (detached)
	docker compose up -d

docker-down: ## Stop all services
	docker compose down

docker-logs: ## Tail logs for all services
	docker compose logs -f

docker-restart: ## Rebuild and restart all services
	docker compose down && docker compose up -d --build

docker-ps: ## Show running containers
	docker compose ps

# ── Cleanup ─────────────────────────────────────────────────
clean: ## Remove caches and build artifacts
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	rm -rf .coverage htmlcov/ dist/ *.egg-info/
