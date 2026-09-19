# AI-Native Data Platform - developer entry points.
#
# Windows: run from WSL2 (make is standard there) or use scripts/dev.ps1,
# which mirrors these targets for PowerShell.
#
# The Makefile never swallows exit codes: every recipe fails loudly.

SHELL := /bin/bash
.DEFAULT_GOAL := help

-include .env
export

UV := uv run --project backend --frozen python
COMPOSE := docker compose -f compose.yaml
COMPOSE_DEV := docker compose -f compose.yaml -f compose.dev.yaml
CORE := --profile core

CONTROL_DB_PORT ?= 55430
SOURCE_DB_PORT ?= 55433
MYSQL_DB_PORT ?= 33060
DORIS_FE_SQL_PORT ?= 19030
TEST_DATABASE_URL ?= postgresql+psycopg://$(CONTROL_DB_USER):$(CONTROL_DB_PASSWORD)@127.0.0.1:$(CONTROL_DB_PORT)/$(CONTROL_DB_NAME)
TEST_SOURCE_URL ?= postgresql+psycopg://$(SOURCE_BOOTSTRAP_USER):$(SOURCE_BOOTSTRAP_PASSWORD)@127.0.0.1:$(SOURCE_DB_PORT)/$(SOURCE_DB_NAME)
TEST_MYSQL_URL ?= mysql://$(MYSQL_ADMIN_USER):$(MYSQL_ADMIN_PASSWORD)@127.0.0.1:$(MYSQL_DB_PORT)/$(MYSQL_SOURCE_DB)
TEST_DORIS_URL ?= mysql://$(DORIS_ADMIN_USER):$(DORIS_ADMIN_PASSWORD)@127.0.0.1:$(DORIS_FE_SQL_PORT)/demo
# M3: host-side tests reach DataHub through the loopback-published GMS port. The
# DataHub-specific integration tests skip (not fail) when the stack is absent.
TEST_DATAHUB_ENV = AIND_DATAHUB_ENABLED=1 AIND_DATAHUB_GMS_URL=http://127.0.0.1:18080 \
	AIND_DATAHUB_FRONTEND_URL=http://127.0.0.1:9002 AIND_CONTAINER_API_URL=http://127.0.0.1:8000

# Demo generator defaults (M4)
SEED ?= 42
AS_OF ?= 2026-09-13
SCALE ?= small
CASES ?= 10
SCENARIO ?= ecpm_drop
DEMO_RUN ?= $(shell ls -td runtime/demo/*/ 2>/dev/null | head -n1 | xargs -r -n1 basename)

.PHONY: help doctor setup-secrets up-core up-full down migrate bootstrap \
        test-core test-unit test-integration test-security smoke-core \
        api-spec types build-core logs ps seed-sources up-full-data \
        demo-generate demo-load demo-lineage metadata-sync demo-verify eval-agent \n        reset-demo \
        smoke-full evaluate-live

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-18s %s\n", $$1, $$2}'

doctor: ## Check host prerequisites and print a machine report
	$(UV) scripts/doctor.py

setup-secrets: ## Generate .env and local secret files (idempotent, never overwrites)
	$(UV) scripts/setup_secrets.py

up-core: network-up ## Start the M1 core stack (control/source PG, API, workers, frontend)
	$(COMPOSE) $(CORE) up -d --build

network-up: ## Create the shared external network used by DataHub (M3)
	@docker network create $(COMPOSE_PROJECT_NAME)-datahub-net >/dev/null 2>&1 || true
	@docker network create datahub-net >/dev/null 2>&1 || true

up-full: network-up ## Start core + MySQL/Doris (full profile; DataHub stack is M3)
	AIND_DATAHUB_ENABLED=1 $(COMPOSE) --profile full up -d --build

datahub-up: network-up ## Start the pinned DataHub stack (v1.7.0.1) on datahub-net
	$(COMPOSE) -f infra/datahub/compose.pinned.yaml -f infra/datahub/compose.ainative.yaml \
		--profile quickstart up -d

datahub-down: ## Stop the DataHub stack (volumes preserved)
	$(COMPOSE) -f infra/datahub/compose.pinned.yaml --profile quickstart down --remove-orphans

datahub-token: ## Optional: verify/store a DataHub token (only needed when GMS auth is enabled)
	$(UV) scripts/datahub_token.py --check

seed-sources: ## Seed the M2 MySQL/Doris demo tables (idempotent)
	uv run --project backend --frozen python scripts/seed_sources.py

metadata-sync: ## Queue DataHub ingestion for all registered datasources (M3)
	$(UV) scripts/metadata_sync.py

down: ## Stop containers, keep volumes
	$(COMPOSE) down --remove-orphans

migrate: ## Apply control-DB migrations
	$(COMPOSE) run --rm --no-deps backend alembic upgrade head

bootstrap: ## Create roles, admin user and capacity rows (idempotent)
	$(COMPOSE) run --rm --no-deps backend python -m app.cli bootstrap

test-core: test-unit test-security test-integration ## Run the M1 test suites (PostgreSQL) against real services


test-unit: ## Unit tests (no services required)
	uv run --project backend --frozen pytest backend/tests/unit -m "not integration"

test-security: ## SQL attack corpus and permission boundary tests
	uv run --project backend --frozen pytest backend/tests/security -m "not integration"

test-integration: db-up-dev ## Integration tests against the real control/source PostgreSQL
	AIND_DATABASE_URL="$(TEST_DATABASE_URL)" AIND_TEST_SOURCE_URL="$(TEST_SOURCE_URL)" \
	AIND_SECRETS_DIR="$(CURDIR)/infra/local-secrets" $(TEST_DATAHUB_ENV) \
	uv run --project backend --frozen pytest backend/tests/integration backend/tests/contract -m integration

test-full: data-up-dev seed-sources ## Integration tests against all three engines (full profile)
	AIND_DATABASE_URL="$(TEST_DATABASE_URL)" AIND_TEST_SOURCE_URL="$(TEST_SOURCE_URL)" \
	AIND_TEST_MYSQL_URL="$(TEST_MYSQL_URL)" AIND_TEST_DORIS_URL="$(TEST_DORIS_URL)" \
	AIND_SECRETS_DIR="$(CURDIR)/infra/local-secrets" $(TEST_DATAHUB_ENV) \
	uv run --project backend --frozen pytest backend/tests/integration backend/tests/contract -m integration

data-up-dev: db-up-dev ## Start MySQL and Doris with loopback ports for tests
	$(COMPOSE_DEV) --profile full up -d source-mysql doris-fe doris-be doris-init

db-up-dev: ## Start only the PostgreSQL databases with loopback ports (host-side tests)
	$(COMPOSE_DEV) $(CORE) up -d control-postgres source-postgres

smoke-core: ## End-to-end HTTP smoke test against the running core stack
	$(UV) scripts/smoke_core.py

api-spec: ## Export backend OpenAPI to frontend/openapi.json
	cd backend && uv run --frozen python -m app.openapi_export --output ../frontend/openapi.json

types: api-spec ## Generate frontend TypeScript types from OpenAPI
	cd frontend && npm run gen:api

build-core: ## Build backend and frontend images
	$(COMPOSE) $(CORE) build

logs: ## Tail core stack logs
	$(COMPOSE) $(CORE) logs -f --tail=100

ps: ## Show core stack status
	$(COMPOSE) $(CORE) ps

# ---------------------------------------------------------------------------
# Demo data pipeline (M4). `docs/todo.md` is the milestone map.
# ---------------------------------------------------------------------------
demo-generate: ## Generate the deterministic demo dataset (SEED/AS_OF/SCALE/SCENARIO)
	$(UV) scripts/demo_generate.py --seed $(SEED) --as-of $(AS_OF) --scale $(SCALE) --scenario $(SCENARIO)

demo-load: ## Load the newest demo run into Doris/MySQL/PostgreSQL
	$(UV) scripts/demo_load.py

demo-lineage: ## Publish declared demo lineage (DataHub SDK in the ingestion image)
	docker compose run --rm --no-deps -v "$(CURDIR)/runtime:/data/runtime:ro" \
		--entrypoint python ingestion /opt/ainative/publish_lineage.py \
		--manifest /data/runtime/$(DEMO_RUN)/pipeline_lineage.json

demo-verify: ## Verify the newest demo run against its ground truth (offline)
	$(UV) scripts/demo_verify.py

eval-agent: ## A09 harness: run the fixed scenarios through the deployed analysis loop
	$(UV) scripts/eval_agent.py --cases $(CASES) --scale $(SCALE) --password "$(AIND_SMOKE_PASSWORD)"

reset-demo: ## Destructive: rebuild the demo data (requires CONFIRM=demo)
	@test "$(CONFIRM)" = "demo" || (echo "set CONFIRM=demo to confirm the destructive reset"; exit 2)
	$(UV) scripts/demo_load.py --reset-demo

smoke-full evaluate-live:
	@echo "target '$@' is planned for a later milestone (see docs/todo.md)"
	@exit 2
