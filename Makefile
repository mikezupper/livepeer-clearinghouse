SHELL := /bin/sh
COMPOSE ?= docker compose
LOCAL_ENV ?= .env
ENV_FILE ?= $(if $(wildcard $(LOCAL_ENV)),$(LOCAL_ENV),.env.example)
CORE_SERVICES := postgres redpanda redpanda-init migrate bootstrap-operator api consumer admin-web user-web edge
TEST_PROJECT ?= livepeer-clearinghouse-test

.DEFAULT_GOAL := help
.PHONY: help init-env prepare-development-secrets deployment-preflight operations-ownership-check migration-status migration-downgrade validate quality-repository quality-python quality-contracts quality-frontend test-backend-unit test-backend-live test-migrations test-migration-matrix test-admin-web test-user-web test-shared-web test-browser-e2e test-browser-accessibility test-browser-smoke build up up-core down destroy ps logs test smoke qualification-harness qualification-journey qualification-recovery qualification-evidence signer-preflight signer-smoke observability-up observability-check observability-down ops-status reconcile-check reconcile-repair retention-dry-run retention-apply backup backup-record restore-verify rotation-record capacity broker-recovery-test

help: ## Show documented project commands.
	@awk 'BEGIN {FS = ":.*## "; print "Livepeer Open Clearinghouse\n"} /^[a-zA-Z0-9_-]+:.*## / {printf "  %-18s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

init-env: ## Create a permission-restricted local .env from the safe example.
	@test ! -e "$(LOCAL_ENV)" || { echo "$(LOCAL_ENV) already exists" >&2; exit 1; }
	@cp .env.example "$(LOCAL_ENV)"
	@chmod 600 "$(LOCAL_ENV)"
	@echo "Created $(LOCAL_ENV); replace placeholders before make up."

prepare-development-secrets: ## Create ignored mode-0600 local-only development secret files.
	@sh deploy/prepare-development-secrets.sh

deployment-preflight: ## Reject unsafe production credentials and in-checkout secrets.
	@uv run python deploy/runtime_preflight.py --env-file "$(ENV_FILE)"

operations-ownership-check: ## Validate the deployment-owned runbook ownership contract.
	@uv run python deploy/operations_ownership.py "$${OPERATIONS_OWNERSHIP_FILE:-deploy/operations-ownership.env.example}"

migration-status: ## Compare the running PostgreSQL revision with the repository head.
	@set -eu; \
	  current="$$( $(COMPOSE) --env-file $(ENV_FILE) exec -T postgres psql -XAt -U "$${POSTGRES_USER:-clearinghouse}" -d "$${POSTGRES_DB:-clearinghouse}" -c 'select version_num from alembic_version' )"; \
	  expected="$${EXPECTED_REVISION:-$$(uv run alembic -c backend/alembic.ini heads | awk 'NR==1 {print $$1}')}"; \
	  uv run python deploy/migration_revision.py "$$current" "$$expected"

migration-downgrade: ## Guardedly downgrade a stopped deployment to REVISION.
	@test "$${CONFIRM:-}" = migration-downgrade && test -n "$${REVISION:-}" && test -n "$${VERIFIED_BACKUP_ID:-}" && test -n "$${CHANGE_RECORD_ID:-}" || { echo "Set CONFIRM=migration-downgrade REVISION=... VERIFIED_BACKUP_ID=... CHANGE_RECORD_ID=..." >&2; exit 3; }
	@OPERATIONS_OWNERSHIP_FILE="$${OPERATIONS_OWNERSHIP_FILE:-deploy/operations-ownership.env.example}" $(MAKE) --no-print-directory operations-ownership-check
	@set -eu; \
	  revision="$$(uv run python deploy/migration_revision.py "$${REVISION}")"; \
	  change_record="$$(uv run python deploy/migration_revision.py --change-id "$${CHANGE_RECORD_ID}")"; \
	  artifact="$$(uv run python deploy/migration_revision.py --artifact-id "$${VERIFIED_BACKUP_ID}")"; \
	  verified="$$( $(COMPOSE) --env-file $(ENV_FILE) exec -T postgres psql -XAt -v artifact="$$artifact" -U "$${POSTGRES_USER:-clearinghouse}" -d "$${POSTGRES_DB:-clearinghouse}" -c "select exists(select 1 from backup_events where artifact_id=:'artifact' and action='restore_verified')" )"; \
	  test "$$verified" = t || { echo "Refusing downgrade without a cataloged restore-verified backup" >&2; exit 3; }; \
	  running="$$( $(COMPOSE) --env-file $(ENV_FILE) ps --status running --services api consumer )"; \
	  test -z "$$running" || { echo "Refusing downgrade while API or consumer is running" >&2; exit 3; }; \
	  $(COMPOSE) --env-file $(ENV_FILE) run --rm migrate alembic -c backend/alembic.ini downgrade "$$revision"; \
	  EXPECTED_REVISION="$$revision" $(MAKE) --no-print-directory migration-status; \
	  printf '{"status":"downgraded","change_record_id":"%s","verified_backup_id":"%s"}\n' "$$change_record" "$$artifact"

validate: ## Validate Compose, shell entrypoints, and deployment tests.
	@$(COMPOSE) --env-file .env.example config --quiet
	@$(COMPOSE) --project-directory . -f deploy/ops/restore.compose.yaml --env-file .env.example config --quiet
	@sh -n deploy/backend-entrypoint.sh deploy/backend-test-entrypoint.sh deploy/migration-fixture-matrix.sh deploy/signer/entrypoint.sh deploy/signer/diagnostic-entrypoint.sh deploy/ops/control.sh deploy/ops/fault-guard.sh deploy/observability/qualify.sh
	@bash -n deploy/ops/entrypoint.sh deploy/ops/database.sh
	@uv run ruff check deploy tests/deploy
	@uv run mypy deploy tests/deploy
	@uv run python -m unittest discover -s tests/deploy -v

quality-repository: ## Validate repository policy, knowledge harness, and architecture.
	@uv run python scripts/check_harness.py
	@uv run python scripts/check_docs.py
	@uv run python scripts/check_ci.py
	@uv run python scripts/validate_supply_chain.py
	@uv run pytest tests/harness backend/tests/architecture -q

quality-python: ## Check Python formatting, lint, and strict types.
	@uv run ruff format --check .
	@uv run ruff check .
	@uv run mypy

quality-contracts: ## Validate canonical OpenAPI, AsyncAPI, schemas, and generated metadata.
	@uv run pytest tests/contracts -q
	@uv run python scripts/check_contract_drift.py

quality-frontend: ## Check frontend architecture, types, lint, and production builds.
	@cd frontend && npm run check:static && npm run build

test-backend-unit: ## Run deterministic backend unit tests without external services.
	@uv run pytest backend/tests/unit -q

test-backend-live: prepare-development-secrets ## Run merged PG18/Redpanda backend coverage.
	@set -eu; \
	  project="$(TEST_PROJECT)-backend"; \
	  trap 'cd "$(CURDIR)"; $(COMPOSE) -p "'"$$project"'" --env-file $(ENV_FILE) --profile test down --volumes --remove-orphans >/dev/null 2>&1 || true; chmod 664 backend/coverage.json' EXIT INT TERM; \
	  touch backend/coverage.json; \
	  chmod 666 backend/coverage.json; \
	  CLEARINGHOUSE_TEST_UID="$$(id -u)" CLEARINGHOUSE_TEST_GID="$$(id -g)" \
	    $(COMPOSE) -p "$$project" --env-file $(ENV_FILE) --profile test run --rm --build backend-test

test-migrations: prepare-development-secrets ## Prove a clean PG18 head/base/head migration cycle.
	@set -eu; \
	  project="$(TEST_PROJECT)-migrations"; \
	  trap '$(COMPOSE) -p "'"$$project"'" --env-file $(ENV_FILE) down --volumes --remove-orphans >/dev/null 2>&1 || true' EXIT INT TERM; \
	  $(COMPOSE) -p "$$project" --env-file $(ENV_FILE) up -d --wait postgres; \
	  $(COMPOSE) -p "$$project" --env-file $(ENV_FILE) run --rm --build migrate alembic -c backend/alembic.ini upgrade head; \
	  $(COMPOSE) -p "$$project" --env-file $(ENV_FILE) run --rm migrate alembic -c backend/alembic.ini downgrade base; \
	  $(COMPOSE) -p "$$project" --env-file $(ENV_FILE) run --rm migrate alembic -c backend/alembic.ini upgrade head
	@$(MAKE) --no-print-directory test-migration-matrix

test-migration-matrix: prepare-development-secrets ## Exercise populated PG18 migrations and atomic downgrade refusals.
	@MIGRATION_MATRIX_PROJECT="$(TEST_PROJECT)-migration-matrix" \
	  MIGRATION_MATRIX_ENV_FILE="$(ENV_FILE)" \
	  sh deploy/migration-fixture-matrix.sh

test-admin-web: ## Run independent admin application coverage.
	@cd frontend && npm run test:coverage --workspace=@livepeer/clearinghouse-admin-web

test-user-web: ## Run independent user application coverage.
	@cd frontend && npm run test:coverage --workspace=@livepeer/clearinghouse-user-web

test-shared-web: ## Run independent shared package coverage gates.
	@cd frontend && npm run test:coverage --workspace=@livepeer/clearinghouse-contracts
	@cd frontend && npm run test:coverage --workspace=@livepeer/clearinghouse-platform
	@cd frontend && npm run test:coverage --workspace=@livepeer/clearinghouse-ui

test-browser-e2e: ## Run production-build Chromium application journeys.
	@cd frontend && npm run test:e2e

test-browser-accessibility: ## Run Chromium axe and composed-shadow semantic checks.
	@cd frontend && npm run test:accessibility

test-browser-smoke: ## Run Firefox and WebKit production-build smoke checks.
	@cd frontend && npm run test:smoke

build: ## Build the backend, both web apps, and pinned signer images.
	@$(COMPOSE) --env-file $(ENV_FILE) build

signer-preflight: prepare-development-secrets deployment-preflight ## Validate required real-signer key, RPC, broker, and funding inputs.
	@test -f "$(ENV_FILE)" || { echo "Run make init-env and configure $(ENV_FILE)" >&2; exit 1; }
	@uv run python deploy/signer/preflight.py --env-file "$(ENV_FILE)" --rpc

up: signer-preflight ## Build and start the default stack, including the real remote signer.
	@$(COMPOSE) --env-file $(ENV_FILE) up -d --build --wait

up-core: prepare-development-secrets deployment-preflight ## Start the deterministic keyless core stack used by synthetic smoke.
	@$(COMPOSE) --env-file $(ENV_FILE) up -d --build --wait $(CORE_SERVICES)

down: ## Stop containers while preserving named volumes.
	@$(COMPOSE) --env-file $(ENV_FILE) down --remove-orphans

destroy: ## Delete containers and volumes only when DESTROY=1 is explicit.
	@test "$(DESTROY)" = 1 || { echo "Refusing volume deletion; rerun with DESTROY=1" >&2; exit 1; }
	@$(COMPOSE) --env-file $(ENV_FILE) down --volumes --remove-orphans

ps: ## Show stack containers and health.
	@$(COMPOSE) --env-file $(ENV_FILE) ps

logs: ## Follow stack logs without printing resolved configuration.
	@$(COMPOSE) --env-file $(ENV_FILE) logs --follow --tail=200

test: validate ## Run every required local quality and contract gate.
	@$(MAKE) --no-print-directory quality-repository quality-python test-backend-unit
	@cd frontend && npm ci --ignore-scripts
	@$(MAKE) --no-print-directory quality-contracts quality-frontend
	@$(MAKE) --no-print-directory test-admin-web test-user-web test-shared-web
	@$(MAKE) --no-print-directory test-browser-e2e test-browser-accessibility test-browser-smoke
	@$(MAKE) --no-print-directory test-migrations test-backend-live

smoke: up-core ## Run deterministic HTTP, authorization, Kafka, and database smoke.
	@$(COMPOSE) --env-file $(ENV_FILE) exec -T api sh -ec 'awk '\''$$1 == "Uid:" {exit !($$2 == 10001 && $$3 == 10001 && $$4 == 10001)}'\'' /proc/1/status; grep -Eq '\''^CapEff:[[:space:]]+0+$$'\'' /proc/1/status; grep -Eq '\''^CapBnd:[[:space:]]+0+$$'\'' /proc/1/status; grep -Eq '\''^NoNewPrivs:[[:space:]]+1$$'\'' /proc/1/status'
	@$(COMPOSE) --env-file $(ENV_FILE) exec -T consumer sh -ec 'awk '\''$$1 == "Uid:" {exit !($$2 == 10001 && $$3 == 10001 && $$4 == 10001)}'\'' /proc/1/status; grep -Eq '\''^CapEff:[[:space:]]+0+$$'\'' /proc/1/status; grep -Eq '\''^CapBnd:[[:space:]]+0+$$'\'' /proc/1/status; grep -Eq '\''^NoNewPrivs:[[:space:]]+1$$'\'' /proc/1/status'
	@$(COMPOSE) --env-file $(ENV_FILE) --profile smoke run --rm smoke

qualification-harness: ## Build and qualify an isolated keyless distribution fixture.
	@uv run python deploy/qualification/run.py --evidence "$${QUALIFICATION_EVIDENCE:-tmp/qualification/harness.json}"

qualification-journey: ## Prove OTP-to-charge behavior and both production web applications.
	@uv run python -m deploy.qualification.journey --evidence "$${QUALIFICATION_EVIDENCE:-tmp/qualification/journey.json}"

qualification-recovery: ## Exercise disposable restart, poison, gap, and signer boundaries.
	@uv run python -m deploy.qualification.recovery --evidence "$${QUALIFICATION_RECOVERY_EVIDENCE:-tmp/qualification/recovery.json}"

qualification-evidence: ## Emit the bounded release qualification manifests.
	@uv run python -m deploy.qualification.evidence --evidence "$${QUALIFICATION_EVIDENCE:-tmp/qualification/release.json}" --report "$${QUALIFICATION_REPORT:-tmp/qualification/release.md}"

signer-smoke: signer-preflight ## Start the default stack and run read-only funded signer checks.
	@$(COMPOSE) --env-file $(ENV_FILE) up -d --build --wait
	@$(COMPOSE) --env-file $(ENV_FILE) exec -T remote-signer /usr/bin/bash -ec 'awk '\''$$1 == "Uid:" {exit !($$2 == 10001 && $$3 == 10001 && $$4 == 10001)}'\'' /proc/1/status; grep -Eq '\''^CapEff:[[:space:]]+0+$$'\'' /proc/1/status; grep -Eq '\''^CapBnd:[[:space:]]+0+$$'\'' /proc/1/status; grep -Eq '\''^NoNewPrivs:[[:space:]]+1$$'\'' /proc/1/status'
	@$(COMPOSE) --env-file $(ENV_FILE) --profile signer-check run --rm signer-diagnostics

observability-up: ## Start the private, opt-in local telemetry stack (no host port).
	@CLEARINGHOUSE_OTEL_EXPORTER_OTLP_ENDPOINT="$${CLEARINGHOUSE_OTEL_EXPORTER_OTLP_ENDPOINT:-http://observability:4318}" CLEARINGHOUSE_OTEL_METRIC_EXPORT_INTERVAL_MILLIS="$${CLEARINGHOUSE_OTEL_METRIC_EXPORT_INTERVAL_MILLIS:-1000}" $(COMPOSE) --env-file $(ENV_FILE) --profile observability up -d --wait --force-recreate observability api consumer

observability-down: ## Stop the opt-in local telemetry service while preserving its data.
	@$(COMPOSE) --env-file $(ENV_FILE) --profile observability stop observability

observability-check: ## Prove application traces, logs, and metrics are queryable privately.
	@$(COMPOSE) --env-file $(ENV_FILE) --profile observability exec -T observability /usr/local/bin/clearinghouse-observability-check

ops-status: ## Emit machine-readable operational status (exit 0/2/3/4).
	@$(COMPOSE) --env-file $(ENV_FILE) --profile ops run --rm ops-control /app/deploy/ops/control.sh status

reconcile-check: ## Check ledger/projection reconciliation without mutation.
	@OPS_REASON="$${REASON:-operator check}" $(COMPOSE) --env-file $(ENV_FILE) --profile ops run --rm -e OPS_REASON ops-control /app/deploy/ops/control.sh reconcile --mode check

reconcile-repair: ## Repair reconciliation only with CONFIRM=repair and a REASON.
	@test "$${CONFIRM:-}" = repair && test -n "$${REASON:-}" && test -n "$${IDEMPOTENCY_KEY:-}" || { echo "Set CONFIRM=repair REASON='...' IDEMPOTENCY_KEY=..." >&2; exit 3; }
	@OPS_MUTATION_CONFIRM=repair OPS_REASON="$${REASON}" OPS_IDEMPOTENCY_KEY="$${IDEMPOTENCY_KEY}" $(COMPOSE) --env-file $(ENV_FILE) --profile ops run --rm -e OPS_MUTATION_CONFIRM -e OPS_REASON -e OPS_IDEMPOTENCY_KEY ops-control /app/deploy/ops/control.sh reconcile --mode repair

retention-dry-run: ## Report retention candidates without deleting data.
	@OPS_REASON="$${REASON:-operator dry run}" OPS_BATCH_SIZE="$${BATCH_SIZE:-200}" OPS_CATEGORY="$${CATEGORY:-operational_detail}" OPS_RETENTION_DAYS="$${RETENTION_DAYS:-30}" $(COMPOSE) --env-file $(ENV_FILE) --profile ops run --rm -e OPS_REASON -e OPS_BATCH_SIZE -e OPS_CATEGORY -e OPS_RETENTION_DAYS ops-control /app/deploy/ops/control.sh retention --mode dry-run

retention-apply: ## Apply bounded retention only with CONFIRM=retention and a REASON.
	@test "$${CONFIRM:-}" = retention && test -n "$${REASON:-}" && test -n "$${IDEMPOTENCY_KEY:-}" || { echo "Set CONFIRM=retention REASON='...' IDEMPOTENCY_KEY=..." >&2; exit 3; }
	@OPS_MUTATION_CONFIRM=retention OPS_REASON="$${REASON}" OPS_IDEMPOTENCY_KEY="$${IDEMPOTENCY_KEY}" OPS_BATCH_SIZE="$${BATCH_SIZE:-200}" OPS_CATEGORY="$${CATEGORY:-operational_detail}" OPS_RETENTION_DAYS="$${RETENTION_DAYS:-30}" $(COMPOSE) --env-file $(ENV_FILE) --profile ops run --rm -e OPS_MUTATION_CONFIRM -e OPS_REASON -e OPS_IDEMPOTENCY_KEY -e OPS_BATCH_SIZE -e OPS_CATEGORY -e OPS_RETENTION_DAYS ops-control /app/deploy/ops/control.sh retention --mode apply

backup: ## Create an encrypted database backup (CONFIRM=backup).
	@test "$${CONFIRM:-}" = backup && test -n "$${REASON:-}" && test -n "$${IDEMPOTENCY_KEY:-}" || { echo "Set CONFIRM=backup REASON='...' IDEMPOTENCY_KEY=..." >&2; exit 3; }
	@set -eu; \
	  retention="$$(uv run python -c 'from datetime import UTC,datetime,timedelta; import sys; days=int(sys.argv[1]); assert 1 <= days <= 3650; print((datetime.now(UTC)+timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ"))' "$${BACKUP_RETENTION_DAYS:-30}")"; \
	  backup_name="$${BACKUP_NAME:-clearinghouse-$$(date -u +%Y%m%dT%H%M%SZ).dump.age}"; \
	  output="$$(OPS_BACKUP_CONFIRM=backup OPS_BACKUP_NAME="$$backup_name" OPS_BACKUP_RETENTION_UNTIL="$$retention" $(COMPOSE) --env-file $(ENV_FILE) --profile ops run --rm --build -e OPS_BACKUP_CONFIRM -e OPS_BACKUP_NAME -e OPS_BACKUP_RETENTION_UNTIL ops-database backup)"; \
	  printf '%s\n' "$$output"; \
	  artifact="$$(printf '%s' "$$output" | uv run python deploy/ops/json_field.py artifact)"; \
	  checksum="$$(printf '%s' "$$output" | uv run python deploy/ops/json_field.py sha256)"; \
	  key_id="$$(printf '%s' "$$output" | uv run python deploy/ops/json_field.py key_id)"; \
	  high_watermark="$$(printf '%s' "$$output" | uv run python deploy/ops/json_field.py source_lsn)"; \
	  location="$$(printf '%s' "$$artifact" | sha256sum | cut -d ' ' -f 1)"; \
	  child_key="$$(uv run python deploy/ops/idempotency_key.py "$${IDEMPOTENCY_KEY}" created)"; \
	  OPS_MUTATION_CONFIRM=backup-record OPS_BACKUP_ACTION=created OPS_BACKUP_ARTIFACT_ID="$$artifact" OPS_BACKUP_LOCATION_SHA256="$$location" OPS_BACKUP_CHECKSUM_SHA256="$$checksum" OPS_BACKUP_KEY_ID="$$key_id" OPS_BACKUP_HIGH_WATERMARK="$$high_watermark" OPS_BACKUP_RETENTION_UNTIL="$$retention" OPS_REASON="$${REASON}" OPS_IDEMPOTENCY_KEY="$$child_key" $(COMPOSE) --env-file $(ENV_FILE) --profile ops run --rm -e OPS_MUTATION_CONFIRM -e OPS_BACKUP_ACTION -e OPS_BACKUP_ARTIFACT_ID -e OPS_BACKUP_LOCATION_SHA256 -e OPS_BACKUP_CHECKSUM_SHA256 -e OPS_BACKUP_KEY_ID -e OPS_BACKUP_HIGH_WATERMARK -e OPS_BACKUP_RETENTION_UNTIL -e OPS_REASON -e OPS_IDEMPOTENCY_KEY ops-control /app/deploy/ops/control.sh backup

backup-record: ## Record an existing completed encrypted bundle after a catalog outage.
	@test "$${CONFIRM:-}" = backup-record && test -n "$${BACKUP_NAME:-}" && test -n "$${REASON:-}" && test -n "$${IDEMPOTENCY_KEY:-}" || { echo "Set CONFIRM=backup-record BACKUP_NAME=... REASON='...' IDEMPOTENCY_KEY=..." >&2; exit 3; }
	@set -eu; output="$$(OPS_BACKUP_NAME="$${BACKUP_NAME}" $(COMPOSE) --env-file $(ENV_FILE) --profile ops run --rm -e OPS_BACKUP_NAME ops-database inspect)"; \
	  artifact="$$(printf '%s' "$$output" | uv run python deploy/ops/json_field.py artifact)"; checksum="$$(printf '%s' "$$output" | uv run python deploy/ops/json_field.py sha256)"; key_id="$$(printf '%s' "$$output" | uv run python deploy/ops/json_field.py key_id)"; high_watermark="$$(printf '%s' "$$output" | uv run python deploy/ops/json_field.py source_lsn)"; retention="$$(printf '%s' "$$output" | uv run python deploy/ops/json_field.py retention_until)"; location="$$(printf '%s' "$$artifact" | sha256sum | cut -d ' ' -f 1)"; \
	  child_key="$$(uv run python deploy/ops/idempotency_key.py "$${IDEMPOTENCY_KEY}" created)"; \
	  OPS_MUTATION_CONFIRM=backup-record OPS_BACKUP_ACTION=created OPS_BACKUP_ARTIFACT_ID="$$artifact" OPS_BACKUP_LOCATION_SHA256="$$location" OPS_BACKUP_CHECKSUM_SHA256="$$checksum" OPS_BACKUP_KEY_ID="$$key_id" OPS_BACKUP_HIGH_WATERMARK="$$high_watermark" OPS_BACKUP_RETENTION_UNTIL="$$retention" OPS_REASON="$${REASON}" OPS_IDEMPOTENCY_KEY="$$child_key" $(COMPOSE) --env-file $(ENV_FILE) --profile ops run --rm -e OPS_MUTATION_CONFIRM -e OPS_BACKUP_ACTION -e OPS_BACKUP_ARTIFACT_ID -e OPS_BACKUP_LOCATION_SHA256 -e OPS_BACKUP_CHECKSUM_SHA256 -e OPS_BACKUP_KEY_ID -e OPS_BACKUP_HIGH_WATERMARK -e OPS_BACKUP_RETENTION_UNTIL -e OPS_REASON -e OPS_IDEMPOTENCY_KEY ops-control /app/deploy/ops/control.sh backup

restore-verify: ## Restore an encrypted backup into an isolated ephemeral database.
	@test "$${CONFIRM:-}" = isolated-restore && test -n "$${BACKUP_NAME:-}" && test -n "$${REASON:-}" && test -n "$${IDEMPOTENCY_KEY:-}" || { echo "Set CONFIRM=isolated-restore BACKUP_NAME=... REASON='...' IDEMPOTENCY_KEY=..." >&2; exit 3; }
	@set -eu; project="och-ops-test-restore-$$(date +%s)-$$$$"; restore='$(COMPOSE) --project-directory . -f deploy/ops/restore.compose.yaml'; \
	  trap '$$restore -p "$$project" --env-file $(ENV_FILE) down --volumes --remove-orphans >/dev/null 2>&1 || true' EXIT INT TERM; \
	  $$restore -p "$$project" --env-file $(ENV_FILE) up -d --wait restore-postgres; \
	  output="$$(OPS_RESTORE_CONFIRM=isolated-restore OPS_BACKUP_NAME="$${BACKUP_NAME}" $$restore -p "$$project" --env-file $(ENV_FILE) run --rm --build -e OPS_RESTORE_CONFIRM -e OPS_BACKUP_NAME ops-restore restore-verify)"; printf '%s\n' "$$output"; \
	  artifact="$$(printf '%s' "$$output" | uv run python deploy/ops/json_field.py artifact)"; checksum="$$(printf '%s' "$$output" | uv run python deploy/ops/json_field.py sha256)"; key_id="$$(printf '%s' "$$output" | uv run python deploy/ops/json_field.py key_id)"; high_watermark="$$(printf '%s' "$$output" | uv run python deploy/ops/json_field.py source_lsn)"; retention="$$(printf '%s' "$$output" | uv run python deploy/ops/json_field.py retention_until)"; location="$$(printf '%s' "$$artifact" | sha256sum | cut -d ' ' -f 1)"; \
	  for action in verified restore-verified; do suffix="$$action"; [ "$$action" != restore-verified ] || suffix=restore; child_key="$$(uv run python deploy/ops/idempotency_key.py "$${IDEMPOTENCY_KEY}" "$$suffix")"; OPS_MUTATION_CONFIRM=backup-record OPS_BACKUP_ACTION="$$action" OPS_BACKUP_ARTIFACT_ID="$$artifact" OPS_BACKUP_LOCATION_SHA256="$$location" OPS_BACKUP_CHECKSUM_SHA256="$$checksum" OPS_BACKUP_KEY_ID="$$key_id" OPS_BACKUP_HIGH_WATERMARK="$$high_watermark" OPS_BACKUP_RETENTION_UNTIL="$$retention" OPS_REASON="$${REASON}" OPS_IDEMPOTENCY_KEY="$$child_key" $(COMPOSE) --env-file $(ENV_FILE) --profile ops run --rm -e OPS_MUTATION_CONFIRM -e OPS_BACKUP_ACTION -e OPS_BACKUP_ARTIFACT_ID -e OPS_BACKUP_LOCATION_SHA256 -e OPS_BACKUP_CHECKSUM_SHA256 -e OPS_BACKUP_KEY_ID -e OPS_BACKUP_HIGH_WATERMARK -e OPS_BACKUP_RETENTION_UNTIL -e OPS_REASON -e OPS_IDEMPOTENCY_KEY ops-control /app/deploy/ops/control.sh backup; done

rotation-record: ## Append an audited secret/key rotation lifecycle event.
	@test "$${CONFIRM:-}" = rotation-record && test -n "$${ROTATION_ID:-}" && test -n "$${PURPOSE:-}" && test -n "$${ACTION:-}" && test -n "$${KEY_ID:-}" && test -n "$${REASON:-}" && test -n "$${IDEMPOTENCY_KEY:-}" || { echo "Set CONFIRM=rotation-record ROTATION_ID=... PURPOSE=... ACTION=... KEY_ID=... REASON='...' IDEMPOTENCY_KEY=..." >&2; exit 3; }
	@OPS_MUTATION_CONFIRM=rotation-record OPS_ROTATION_ID="$${ROTATION_ID}" OPS_ROTATION_PURPOSE="$${PURPOSE}" OPS_ROTATION_ACTION="$${ACTION}" OPS_ROTATION_KEY_ID="$${KEY_ID}" OPS_ROTATION_PRIOR_KEY_ID="$${PRIOR_KEY_ID:-}" OPS_REASON="$${REASON}" OPS_IDEMPOTENCY_KEY="$${IDEMPOTENCY_KEY}" $(COMPOSE) --env-file $(ENV_FILE) --profile ops run --rm -e OPS_MUTATION_CONFIRM -e OPS_ROTATION_ID -e OPS_ROTATION_PURPOSE -e OPS_ROTATION_ACTION -e OPS_ROTATION_KEY_ID -e OPS_ROTATION_PRIOR_KEY_ID -e OPS_REASON -e OPS_IDEMPOTENCY_KEY ops-control /app/deploy/ops/control.sh rotation

capacity: ## Emit database and broker capacity signals without mutation.
	@$(COMPOSE) --env-file $(ENV_FILE) --profile ops run --rm ops-database capacity
	@$(COMPOSE) --env-file $(ENV_FILE) exec -T redpanda sh -ec 'rpk topic describe "$${TOPIC:-livepeer-gateway-events}" -p'
	@$(COMPOSE) --env-file $(ENV_FILE) exec -T redpanda sh -ec 'rpk topic describe "$${TOPIC:-livepeer-gateway-events}" -c'

broker-recovery-test: ## Stop/restart a broker only in a guarded disposable project.
	@test "$${CONFIRM:-}" = disposable-only || { echo "Set CONFIRM=disposable-only" >&2; exit 3; }
	@set -eu; project="och-ops-test-broker-$$(date +%s)-$$$$"; export CLEARINGHOUSE_EDGE_PORT=0; \
	  COMPOSE_PROJECT_NAME="$$project" OPS_FAULT_CONFIRM=disposable-only OPS_FAULT_TARGET=redpanda sh deploy/ops/fault-guard.sh; \
	  trap '$(COMPOSE) -p "$$project" --env-file $(ENV_FILE) down --volumes --remove-orphans >/dev/null 2>&1 || true' EXIT INT TERM; \
	  $(COMPOSE) -p "$$project" --env-file $(ENV_FILE) up -d --build --wait $(CORE_SERVICES); \
	  $(COMPOSE) -p "$$project" --env-file $(ENV_FILE) --profile smoke run --rm smoke; \
	  before="$$( $(COMPOSE) -p "$$project" --env-file $(ENV_FILE) exec -T postgres psql -XAt -U "$${POSTGRES_USER:-clearinghouse}" -d "$${POSTGRES_DB:-clearinghouse}" -c 'select coalesce(max(next_offset),0) from metering_checkpoints' )"; \
	  observations_before="$$( $(COMPOSE) -p "$$project" --env-file $(ENV_FILE) exec -T postgres psql -XAt -U "$${POSTGRES_USER:-clearinghouse}" -d "$${POSTGRES_DB:-clearinghouse}" -c 'select count(*) from metering_observations' )"; \
	  $(COMPOSE) -p "$$project" --env-file $(ENV_FILE) exec -T postgres psql -X -v ON_ERROR_STOP=1 -U "$${POSTGRES_USER:-clearinghouse}" -d "$${POSTGRES_DB:-clearinghouse}" -c "insert into principals(id,display_name,status) values ('principal_ops_exercise','Operations exercise','active') on conflict do nothing; insert into principal_roles(principal_id,role) values ('principal_ops_exercise','operator') on conflict do nothing" >/dev/null; \
	  $(COMPOSE) -p "$$project" --env-file $(ENV_FILE) stop consumer; \
	  SMOKE_PUBLISH_ONLY=1 $(COMPOSE) -p "$$project" --env-file $(ENV_FILE) --profile smoke run --rm --no-deps -e SMOKE_PUBLISH_ONLY smoke; \
	  checkpoint_while_stopped="$$( $(COMPOSE) -p "$$project" --env-file $(ENV_FILE) exec -T postgres psql -XAt -U "$${POSTGRES_USER:-clearinghouse}" -d "$${POSTGRES_DB:-clearinghouse}" -c 'select coalesce(max(next_offset),0) from metering_checkpoints' )"; \
	  observations_while_stopped="$$( $(COMPOSE) -p "$$project" --env-file $(ENV_FILE) exec -T postgres psql -XAt -U "$${POSTGRES_USER:-clearinghouse}" -d "$${POSTGRES_DB:-clearinghouse}" -c 'select count(*) from metering_observations' )"; \
	  test "$$checkpoint_while_stopped" -eq "$$before"; test "$$observations_while_stopped" -eq "$$observations_before"; \
	  seek_output="$$( $(COMPOSE) -p "$$project" --env-file $(ENV_FILE) exec -T redpanda rpk group seek "$${CLEARINGHOUSE_KAFKA_METERING_GROUP_ID:-clearinghouse-metering-v1}" --to end --topics "$${CLEARINGHOUSE_KAFKA_METERING_TOPIC:-livepeer-gateway-events}" )"; \
	  case "$$seek_output" in *ERROR*|*INVALID_*) printf '%s\n' "$$seek_output" >&2; exit 1;; esac; \
	  $(COMPOSE) -p "$$project" --env-file $(ENV_FILE) stop redpanda; \
	  test "$$( $(COMPOSE) -p "$$project" --env-file $(ENV_FILE) ps --status exited --services redpanda )" = redpanda; \
	  $(COMPOSE) -p "$$project" --env-file $(ENV_FILE) up -d --wait redpanda; \
	  $(COMPOSE) -p "$$project" --env-file $(ENV_FILE) up -d --force-recreate --wait consumer; \
	  replayed=0; for attempt in $$(seq 1 30); do replayed="$$( $(COMPOSE) -p "$$project" --env-file $(ENV_FILE) exec -T postgres psql -XAt -U "$${POSTGRES_USER:-clearinghouse}" -d "$${POSTGRES_DB:-clearinghouse}" -c 'select coalesce(max(next_offset),0) from metering_checkpoints' )"; [ "$$replayed" -gt "$$before" ] && break; sleep 1; done; \
	  observations_replayed="$$( $(COMPOSE) -p "$$project" --env-file $(ENV_FILE) exec -T postgres psql -XAt -U "$${POSTGRES_USER:-clearinghouse}" -d "$${POSTGRES_DB:-clearinghouse}" -c 'select count(*) from metering_observations' )"; \
	  test "$$replayed" -gt "$$before"; test "$$observations_replayed" -gt "$$observations_before"; \
	  $(COMPOSE) -p "$$project" --env-file $(ENV_FILE) --profile smoke run --rm smoke; \
	  after="$$( $(COMPOSE) -p "$$project" --env-file $(ENV_FILE) exec -T postgres psql -XAt -U "$${POSTGRES_USER:-clearinghouse}" -d "$${POSTGRES_DB:-clearinghouse}" -c 'select coalesce(max(next_offset),0) from metering_checkpoints' )"; \
	  test "$$after" -gt "$$before"; \
	  OPS_REASON='post-broker-recovery check' CLEARINGHOUSE_OPERATIONS_ACTOR_ID=principal_ops_exercise $(COMPOSE) -p "$$project" --env-file $(ENV_FILE) --profile ops run --rm -e OPS_REASON -e CLEARINGHOUSE_OPERATIONS_ACTOR_ID ops-control /app/deploy/ops/control.sh reconcile --mode check; \
	  $(COMPOSE) -p "$$project" --env-file $(ENV_FILE) exec -T redpanda rpk cluster health --exit-when-healthy; \
	  printf '{"status":"recovered","checkpoint_before":%s,"checkpoint_replayed":%s,"checkpoint_after":%s,"observations_after_replay":%s}\n' "$$before" "$$replayed" "$$after" "$$observations_replayed"
