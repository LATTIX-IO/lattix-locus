.PHONY: up down update remove local-up local-down stack-up stack-down test unit-test integration-test performance-test lint typecheck policy-test helm-validate release-bundle bootstrap health ps logs smoke install-opa frontend-serve resource-baseline

# Canonical public install path: install/bootstrap.sh (or install/bootstrap.ps1 on Windows).
# This Makefile is kept as a source-checkout convenience wrapper for contributors.

ifeq ($(OS),Windows_NT)
VENV_PYTHON := .venv/Scripts/python.exe
DEFAULT_PYTHON := python
DEV_NULL := NUL
else
VENV_PYTHON := .venv/bin/python
DEFAULT_PYTHON := python3
DEV_NULL := /dev/null
endif

PYTHON ?= $(if $(wildcard $(VENV_PYTHON)),$(VENV_PYTHON),$(DEFAULT_PYTHON))
PYTEST ?= pytest
CLI_RUNNER ?= $(PYTHON) -m frontier_tooling.cli
OPA_RUNNER ?= $(PYTHON) scripts/run_opa.py
SECURE_ENV_FILE := $(strip $(shell "$(PYTHON)" -c "from frontier_tooling.common import ensure_compose_env_file; print(ensure_compose_env_file(local_profile=False))"))
LIGHTWEIGHT_ENV_FILE := $(strip $(shell "$(PYTHON)" -c "from frontier_tooling.common import ensure_compose_env_file; print(ensure_compose_env_file(local_profile=True))"))
LOCAL_COMPOSE ?= docker compose --env-file $(LIGHTWEIGHT_ENV_FILE) -f docker-compose.local.yml
FULL_COMPOSE ?= docker compose --env-file $(SECURE_ENV_FILE)

up:             ## Start all services
	$(CLI_RUNNER) up

down:           ## Stop all services
	$(CLI_RUNNER) down

update:         ## Refresh a local install in place without deleting workflows or settings
	$(CLI_RUNNER) update

remove:         ## Tear down local install and delete installer-managed env files
	$(CLI_RUNNER) remove

local-up:       ## Start the lightweight local-first stack
	$(CLI_RUNNER) local-up

local-down:     ## Stop the lightweight local-first stack
	$(CLI_RUNNER) local-down

stack-up:       ## Start the full platform stack (gateway, sandbox, policy infra)
	$(CLI_RUNNER) stack-up

stack-down:     ## Stop the full platform stack
	$(CLI_RUNNER) stack-down

test:           ## Run all tests
	$(PYTEST) apps/backend/tests tests -v --cov=app --cov=frontier_runtime --cov-report=term-missing

unit-test:      ## Run deterministic backend/runtime unit and harness tests
	$(PYTEST) apps/backend/tests tests/backend tests/unit tests/harness tests/evals -v

integration-test: ## Run integration and end-to-end tests
	$(PYTEST) tests/integration tests/e2e -v

performance-test: ## Run bounded performance regression tests
	$(PYTEST) tests/performance -v

lint:           ## Lint and format
	$(PYTHON) -m ruff check . --fix
	$(PYTHON) -m ruff format .

typecheck:      ## Type check
	$(PYTHON) -m mypy frontier_tooling/ frontier_runtime/

policy-test:    ## Test OPA policies
	$(OPA_RUNNER) test policies/ -v

helm-validate:  ## Validate Helm chart manifests (requires helm)
	helm lint ./helm/lattix-frontier
	helm template lattix ./helm/lattix-frontier -f helm/lattix-frontier/values-prod.yaml > $(DEV_NULL)

release-bundle: ## Build a local release bundle (requires VERSION and helm)
	@test -n "$(VERSION)" || (echo "VERSION is required, e.g. make release-bundle VERSION=v0.1.0" && exit 1)
	mkdir -p dist/chart dist/installer
	helm package helm/lattix-frontier --destination dist/chart
	cp install/bootstrap.sh dist/installer/
	cp install/bootstrap.ps1 dist/installer/
	cp install/frontier-installer.py dist/installer/
	cp install/manifest.json dist/installer/
	$(PYTHON) scripts/build_release_bundle.py --version "$(VERSION)" --repo "local-worktree" --chart-dist dist/chart --installer-dist dist/installer --output-root dist/release

install-opa:    ## Install repo-local OPA binary (Windows helper remains available too)
	@echo "Install OPA with .\\scripts\\frontier.ps1 install-opa on Windows, or place the binary at .tools/opa/opa(.exe)."

bootstrap:      ## First-time setup
	$(CLI_RUNNER) bootstrap

health:         ## Check API health endpoint
	$(CLI_RUNNER) health

ps:
	$(CLI_RUNNER) ps

logs:
	$(CLI_RUNNER) logs

smoke:
	$(CLI_RUNNER) smoke

frontend-serve: ## Build and serve the frontend production bundle (far lighter than `next dev`)
	cd apps/frontend && npm run build && npm run start

resource-baseline: ## Capture per-process and per-container memory baseline to docs/perf/baselines/
ifeq ($(OS),Windows_NT)
	powershell -NoProfile -ExecutionPolicy Bypass -File scripts/resource-baseline.ps1
else
	bash scripts/resource-baseline.sh
endif

# --- OpenAI Symphony orchestration targets ---
SYMPHONY_ROOT ?= $(abspath $(CURDIR)/../symphony)
SYMPHONY_ELIXIR_ROOT ?= $(SYMPHONY_ROOT)/elixir
SYMPHONY_RUST_ROOT ?= $(SYMPHONY_ROOT)/rust
SYMPHONY_WORKFLOW ?= $(CURDIR)/WORKFLOW.md
SYMPHONY_SHARED_DOTENV_FILE ?= $(abspath $(CURDIR)/../lattix-monorepo/.env.symphony.local)
SYMPHONY_DOTENV_FILE ?= $(firstword $(wildcard $(CURDIR)/.env.symphony.local) $(wildcard $(SYMPHONY_SHARED_DOTENV_FILE)))
SYMPHONY_CODEX_WRAPPER ?= $(CURDIR)/scripts/symphony-codex.sh
SYMPHONY_PORT ?= 4057
MISE_WINGET ?= $(subst \\,/,$(USERPROFILE))/AppData/Local/Microsoft/WinGet/Packages/jdx.mise_Microsoft.Winget.Source_8wekyb3d8bbwe/mise/bin/mise.exe
MISE ?= $(if $(wildcard $(MISE_WINGET)),$(MISE_WINGET),mise)
GIT_SH_DIR ?= C:/Program Files/Git/usr/bin
SYMPHONY_RUNNER ?= "$(MISE)" exec -- escript ./bin/symphony
SYMPHONY_NO_GUARDS ?=
SYMPHONY_GITHUB_COPILOT_MODEL ?= gpt-5.4
SYMPHONY_ROUTE ?= $(firstword $(filter native omniroute --native --omniroute,$(MAKECMDGOALS)))
SYMPHONY_ROUTE := $(patsubst --%,%,$(SYMPHONY_ROUTE))
SYMPHONY_ROUTE := $(if $(SYMPHONY_ROUTE),$(SYMPHONY_ROUTE),native)
SYMPHONY_NATIVE_MODEL ?= gpt-5.6-sol
SYMPHONY_OMNIROUTE_MODEL ?= auto
SYMPHONY_REASONING_EFFORT ?= high
OMNIROUTE_VERSION ?= 3.8.48
OMNIROUTE_RUN_ROOT ?= $(abspath $(CURDIR)/../.omniroute)
SYMPHONY_MANAGEMENT_PLANE_URL ?= $(if $(wildcard $(SYMPHONY_ROOT)/management-plane/package.json),http://127.0.0.1:4173,)
SYMPHONY_WINDOWS_ERL_AFLAGS ?= -noinput
SYMPHONY_GUARD_ACK_FLAG ?= --i-understand-that-this-will-be-running-without-the-usual-guardrails
SYMPHONY_LANGUAGE ?= $(firstword $(filter elixir elixer rust all --elixir --elixer --rust --all,$(MAKECMDGOALS)))
SYMPHONY_LANGUAGE := $(patsubst --%,%,$(SYMPHONY_LANGUAGE))
SYMPHONY_LANGUAGE := $(subst elixer,elixir,$(SYMPHONY_LANGUAGE))
SYMPHONY_LANGUAGE := $(if $(SYMPHONY_LANGUAGE),$(SYMPHONY_LANGUAGE),elixir)
SYMPHONY_INFERENCE_SOURCE ?= $(firstword $(filter github-copilot github_copilot copilot codex --github-copilot --github_copilot --copilot --codex,$(MAKECMDGOALS)))
SYMPHONY_INFERENCE_SOURCE := $(patsubst --%,%,$(SYMPHONY_INFERENCE_SOURCE))
SYMPHONY_INFERENCE_SOURCE := $(subst github_copilot,github-copilot,$(SYMPHONY_INFERENCE_SOURCE))
SYMPHONY_INFERENCE_SOURCE := $(if $(filter copilot,$(SYMPHONY_INFERENCE_SOURCE)),github-copilot,$(SYMPHONY_INFERENCE_SOURCE))
SYMPHONY_INFERENCE_SOURCE := $(if $(SYMPHONY_INFERENCE_SOURCE),$(SYMPHONY_INFERENCE_SOURCE),github-copilot)
SYMPHONY_GUARD_SELECTOR := $(firstword $(filter no-guards --no-guards,$(MAKECMDGOALS)))
SYMPHONY_GUARD_ACK := $(if $(SYMPHONY_GUARD_SELECTOR),$(SYMPHONY_GUARD_ACK_FLAG),$(if $(filter 1 true yes on,$(SYMPHONY_NO_GUARDS)),$(SYMPHONY_GUARD_ACK_FLAG),))

.PHONY: symphony symphpny symphony-install symphony-preflight omniroute-install omniroute-start elixir elixer rust all --elixir --elixer --rust --all github-copilot github_copilot copilot codex native omniroute --github-copilot --github_copilot --copilot --codex --native --omniroute no-guards --no-guards
symphony:
ifneq ($(filter install,$(MAKECMDGOALS)),)
	@$(MAKE) --no-print-directory symphony-install SYMPHONY_LANGUAGE="$(SYMPHONY_LANGUAGE)"
else
	@echo "Starting Symphony for $(SYMPHONY_WORKFLOW) on port $(SYMPHONY_PORT) using route $(SYMPHONY_ROUTE)"
ifeq ($(OS),Windows_NT)
	@powershell -NoProfile -ExecutionPolicy Bypass -Command "$$dotenv = '$(SYMPHONY_DOTENV_FILE)'; if ($$dotenv -and (Test-Path -LiteralPath $$dotenv)) { foreach ($$line in Get-Content -LiteralPath $$dotenv) { $$trimmed = $$line.Trim(); if ($$trimmed -eq '' -or $$trimmed.StartsWith('#')) { continue }; $$parts = $$trimmed -split '=', 2; if ($$parts.Length -ne 2) { continue }; $$name = $$parts[0].Trim(); $$value = $$parts[1].Trim(); if (($$value.StartsWith('\"') -and $$value.EndsWith('\"')) -or ($$value.StartsWith(\"'\") -and $$value.EndsWith(\"'\"))) { $$value = $$value.Substring(1, $$value.Length - 2) }; Set-Item -Path ('Env:' + $$name) -Value $$value } }; $$env:PATH = '$(GIT_SH_DIR);' + $$env:PATH; $$env:SYMPHONY_AGENT_PROVIDER = '$(SYMPHONY_INFERENCE_SOURCE)'; $$env:SYMPHONY_INFERENCE_ROUTE = '$(SYMPHONY_ROUTE)'; $$env:SYMPHONY_CODEX_WRAPPER = '$(SYMPHONY_CODEX_WRAPPER)'; if ([string]::IsNullOrWhiteSpace($$env:SYMPHONY_NATIVE_MODEL)) { $$env:SYMPHONY_NATIVE_MODEL = '$(SYMPHONY_NATIVE_MODEL)' }; if ([string]::IsNullOrWhiteSpace($$env:SYMPHONY_OMNIROUTE_MODEL)) { $$env:SYMPHONY_OMNIROUTE_MODEL = '$(SYMPHONY_OMNIROUTE_MODEL)' }; if ([string]::IsNullOrWhiteSpace($$env:SYMPHONY_REASONING_EFFORT)) { $$env:SYMPHONY_REASONING_EFFORT = '$(SYMPHONY_REASONING_EFFORT)' }; if ([string]::IsNullOrWhiteSpace($$env:SYMPHONY_GH_COPILOT_MODEL)) { $$env:SYMPHONY_GH_COPILOT_MODEL = '$(SYMPHONY_GITHUB_COPILOT_MODEL)' }; if ([string]::IsNullOrWhiteSpace($$env:SYMPHONY_MANAGEMENT_PLANE_URL) -and -not [string]::IsNullOrWhiteSpace('$(SYMPHONY_MANAGEMENT_PLANE_URL)')) { $$env:SYMPHONY_MANAGEMENT_PLANE_URL = '$(SYMPHONY_MANAGEMENT_PLANE_URL)' }; $$env:ERL_AFLAGS = '$(SYMPHONY_WINDOWS_ERL_AFLAGS) ' + $$env:ERL_AFLAGS; Set-Location '$(SYMPHONY_ELIXIR_ROOT)'; & '$(MISE)' exec -- escript ./bin/symphony $(SYMPHONY_GUARD_ACK) '$(SYMPHONY_WORKFLOW)' --port '$(SYMPHONY_PORT)'"
else
	@bash -lc 'if [ -n "$(SYMPHONY_DOTENV_FILE)" ] && [ -f "$(SYMPHONY_DOTENV_FILE)" ]; then set -a; . "$(SYMPHONY_DOTENV_FILE)"; set +a; fi; if [ -z "$${SYMPHONY_MANAGEMENT_PLANE_URL}" ] && [ -n "$(SYMPHONY_MANAGEMENT_PLANE_URL)" ]; then export SYMPHONY_MANAGEMENT_PLANE_URL="$(SYMPHONY_MANAGEMENT_PLANE_URL)"; fi; cd "$(SYMPHONY_ELIXIR_ROOT)" && SYMPHONY_AGENT_PROVIDER="$(SYMPHONY_INFERENCE_SOURCE)" SYMPHONY_INFERENCE_ROUTE="$(SYMPHONY_ROUTE)" SYMPHONY_CODEX_WRAPPER="$(SYMPHONY_CODEX_WRAPPER)" SYMPHONY_NATIVE_MODEL="$${SYMPHONY_NATIVE_MODEL:-$(SYMPHONY_NATIVE_MODEL)}" SYMPHONY_OMNIROUTE_MODEL="$${SYMPHONY_OMNIROUTE_MODEL:-$(SYMPHONY_OMNIROUTE_MODEL)}" SYMPHONY_REASONING_EFFORT="$${SYMPHONY_REASONING_EFFORT:-$(SYMPHONY_REASONING_EFFORT)}" SYMPHONY_GH_COPILOT_MODEL="$${SYMPHONY_GH_COPILOT_MODEL:-$(SYMPHONY_GITHUB_COPILOT_MODEL)}" $(SYMPHONY_RUNNER) $(SYMPHONY_GUARD_ACK) "$(SYMPHONY_WORKFLOW)" --port "$(SYMPHONY_PORT)"'
endif
endif

symphpny: symphony

symphony-install:
ifeq ($(SYMPHONY_LANGUAGE),elixir)
	@cd "$(SYMPHONY_ELIXIR_ROOT)" && "$(MISE)" install && "$(MISE)" exec -- mix deps.get && "$(MISE)" exec -- mix escript.build
else ifeq ($(SYMPHONY_LANGUAGE),rust)
	@cd "$(SYMPHONY_RUST_ROOT)" && cargo fetch
else ifeq ($(SYMPHONY_LANGUAGE),all)
	@$(MAKE) --no-print-directory symphony-install SYMPHONY_LANGUAGE=elixir
	@$(MAKE) --no-print-directory symphony-install SYMPHONY_LANGUAGE=rust
else
	@echo "Unsupported Symphony language: $(SYMPHONY_LANGUAGE). Use elixir, rust, or all."
	@exit 2
endif

symphony-preflight:
	@powershell -NoProfile -ExecutionPolicy Bypass -File "$(CURDIR)/scripts/Test-SymphonyOrchestration.ps1" -Route "$(SYMPHONY_ROUTE)" -SymphonyRoot "$(SYMPHONY_ROOT)" -DotenvFile "$(SYMPHONY_DOTENV_FILE)"

omniroute-install:
	@npm install --global "omniroute@$(OMNIROUTE_VERSION)"

omniroute-start:
ifeq ($(OS),Windows_NT)
	@powershell -NoProfile -ExecutionPolicy Bypass -Command "$$runRoot = '$(OMNIROUTE_RUN_ROOT)'; New-Item -ItemType Directory -Force -Path $$runRoot | Out-Null; Set-Location -LiteralPath $$runRoot; & omniroute serve"
else
	@mkdir -p "$(OMNIROUTE_RUN_ROOT)" && cd "$(OMNIROUTE_RUN_ROOT)" && omniroute serve
endif

elixir elixer rust all --elixir --elixer --rust --all github-copilot github_copilot copilot codex native omniroute --github-copilot --github_copilot --copilot --codex --native --omniroute no-guards --no-guards:
	@:

ifneq ($(filter symphony symphpny,$(MAKECMDGOALS)),)
ifneq ($(filter install,$(MAKECMDGOALS)),)
.PHONY: install
install:
	@:
endif
endif
# --- End OpenAI Symphony orchestration targets ---

