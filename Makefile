SHELL := /bin/sh
COMPOSE ?= docker compose
ENV_FILE ?= $(if $(wildcard .env),.env,.env.example)
BUILD_ENV_FILE ?= .env.example
QUAL_ENV_FILE ?= qualification.env

.DEFAULT_GOAL := help
.PHONY: help init-env validate quality-python quality-contracts quality-frontend test-backend test-frontend test-browser build signer-preflight up down destroy ps logs test smoke qualify-gateway qualify-billing-plan qualify-billing-run qualify-billing-controlled qualify-billing-broker qualify-billing-report

help: ## Show project commands.
	@awk 'BEGIN {FS = ":.*## "; print "Livepeer Clearinghouse\n"} /^[a-zA-Z0-9_-]+:.*## / {printf "  %-18s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

init-env: ## Create a permission-restricted .env from the example.
	@test ! -e .env || { echo '.env already exists' >&2; exit 1; }
	@cp .env.example .env && chmod 600 .env

validate: ## Validate the four-service Compose distribution.
	@$(COMPOSE) --env-file $(ENV_FILE) config --quiet
	@sh -n deploy/signer/entrypoint.sh

quality-python: ## Check Python formatting, lint, and strict types.
	@uv run ruff format --check backend tests scripts deploy/signer/preflight.py
	@uv run ruff check backend tests scripts deploy/signer/preflight.py
	@uv run mypy backend/src backend/tests

quality-contracts: ## Validate versioned APIs, events, and SDK contracts.
	@uv run pytest tests/contracts backend/tests/contract -q

quality-frontend: ## Check both Lit applications and shared packages.
	@cd frontend && npm run check:static && npm run build

test-backend: quality-python ## Run backend tests with 85% coverage in every metric.
	@uv run pytest backend/tests --cov=clearinghouse --cov-branch --cov-report=json:backend/coverage.json --cov-report=term-missing -q
	@uv run python backend/scripts/check_coverage.py

test-frontend: ## Run independent 85% coverage gates for all frontend units.
	@cd frontend && npm run test:coverage

test-browser: ## Run cross-browser, accessibility, and visual application tests.
	@cd frontend && npm run test:browsers

build: ## Build core, edge, and pinned remote-signer images.
	@$(COMPOSE) --env-file $(BUILD_ENV_FILE) build core edge remote-signer

signer-preflight: ## Validate signer keys, RPC, balances, broker settings, and static orchestrators.
	@uv run python deploy/signer/preflight.py --env-file "$(ENV_FILE)" --rpc

up: signer-preflight ## Build and start the complete four-service stack.
	@$(COMPOSE) --env-file $(ENV_FILE) up -d --build --wait

down: ## Stop containers while preserving SQLite, signer, and broker data.
	@$(COMPOSE) --env-file $(ENV_FILE) down --remove-orphans

destroy: ## Delete containers and volumes only with DESTROY=1.
	@test "$(DESTROY)" = 1 || { echo 'Refusing volume deletion; rerun with DESTROY=1' >&2; exit 1; }
	@$(COMPOSE) --env-file $(ENV_FILE) down --volumes --remove-orphans

ps: ## Show stack status.
	@$(COMPOSE) --env-file $(ENV_FILE) ps

logs: ## Follow stack logs.
	@$(COMPOSE) --env-file $(ENV_FILE) logs --follow --tail=200

test: validate quality-python quality-contracts quality-frontend test-backend test-frontend test-browser ## Run every local quality gate.

smoke: ## Verify core readiness through the running edge.
	@endpoint=$$($(COMPOSE) --env-file $(ENV_FILE) port edge 8080); \
		curl --fail --silent --show-error "http://$$endpoint/health/ready"

qualify-gateway: ## Qualify a staged local stack with the official Python gateway.
	@uv run python scripts/qualify_gateway.py --env-file "$(QUAL_ENV_FILE)"

qualify-billing-plan: ## Plan bounded billing cases without creating workloads.
	@uv run python scripts/qualification_cases.py --env-file "$(QUAL_ENV_FILE)" $(if $(CASES),--cases "$(CASES)",)

qualify-billing-run: ## Execute explicitly selected bounded billing cases.
	@test -n "$(CASES)" || { echo 'CASES is required (comma-separated case ids)' >&2; exit 1; }
	@uv run python scripts/run_qualification_suite.py --env-file "$(QUAL_ENV_FILE)" --cases "$(CASES)"

qualify-billing-controlled: ## Run deterministic billing lifecycle integrations without network spend.
	@uv run python scripts/qualification_controlled.py --env-file "$(QUAL_ENV_FILE)" $(if $(CASES),--cases "$(CASES)",)

qualify-billing-broker: ## Replay owned duplicate/delayed events through local Redpanda.
	@uv run python scripts/qualification_broker.py --env-file "$(QUAL_ENV_FILE)" --compose-env "$(ENV_FILE)"

qualify-billing-report: ## Summarize sanitized billing qualification evidence.
	@uv run python scripts/qualification_report.py --evidence-root "$${QUAL_EVIDENCE_ROOT:-tmp/qualification}"
