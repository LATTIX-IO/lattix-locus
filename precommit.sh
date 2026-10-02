#!/usr/bin/env bash

set -u -o pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FRONTEND_ROOT="$REPO_ROOT/apps/frontend"
STEP_RESULTS=()

echo "Lattix xFrontier pre-commit checks"

add_step_result() {
  local name="$1"
  local status="$2"
  local detail="${3:-}"
  STEP_RESULTS+=("$status|$name|$detail")
}

write_step_summary() {
  local final="${1:-0}"
  echo
  echo "Pre-commit status summary"
  local result status name detail suffix
  for result in "${STEP_RESULTS[@]:-}"; do
    IFS='|' read -r status name detail <<< "$result"
    suffix=""
    if [[ -n "$detail" ]]; then
      suffix=" - $detail"
    fi
    echo "[$status] $name$suffix"
  done
  if [[ "$final" == "1" ]]; then
    echo
  fi
}

get_python_command() {
  if [[ -x "$REPO_ROOT/.venv/bin/python" ]]; then
    PYTHON_CMD=("$REPO_ROOT/.venv/bin/python")
    return 0
  fi

  if command -v python3 >/dev/null 2>&1; then
    PYTHON_CMD=("$(command -v python3)")
    return 0
  fi

  if command -v python >/dev/null 2>&1; then
    PYTHON_CMD=("$(command -v python)")
    return 0
  fi

  echo "Python 3 is required but was not found in PATH." >&2
  exit 1
}

get_opa_command() {
  local candidates=(
    "$REPO_ROOT/.tools/opa/opa"
    "$REPO_ROOT/.tools/opa/darwin-arm64/opa"
    "$REPO_ROOT/.tools/opa/darwin-amd64/opa"
    "$REPO_ROOT/.tools/opa/linux-amd64/opa"
  )
  local candidate
  for candidate in "${candidates[@]}"; do
    if [[ -x "$candidate" ]]; then
      OPA_CMD="$candidate"
      return 0
    fi
  done
  if command -v opa >/dev/null 2>&1; then
    OPA_CMD="$(command -v opa)"
    return 0
  fi
  OPA_CMD=""
}

get_helm_command() {
  local candidates=(
    "$REPO_ROOT/.tools/helm/darwin-arm64/helm"
    "$REPO_ROOT/.tools/helm/darwin-amd64/helm"
    "$REPO_ROOT/.tools/helm/linux-amd64/helm"
    "$REPO_ROOT/.tools/helm/helm"
  )
  local candidate
  for candidate in "${candidates[@]}"; do
    if [[ -x "$candidate" ]]; then
      HELM_CMD="$candidate"
      return 0
    fi
  done
  if command -v helm >/dev/null 2>&1; then
    HELM_CMD="$(command -v helm)"
    return 0
  fi
  HELM_CMD=""
}

get_syft_command() {
  local candidates=(
    "$REPO_ROOT/.tools/syft/darwin-arm64/syft"
    "$REPO_ROOT/.tools/syft/darwin-amd64/syft"
    "$REPO_ROOT/.tools/syft/linux-amd64/syft"
    "$REPO_ROOT/.tools/syft/syft"
  )
  local candidate
  for candidate in "${candidates[@]}"; do
    if [[ -x "$candidate" ]]; then
      SYFT_CMD="$candidate"
      return 0
    fi
  done
  if command -v syft >/dev/null 2>&1; then
    SYFT_CMD="$(command -v syft)"
    return 0
  fi
  SYFT_CMD=""
}

invoke_python() {
  "${PYTHON_CMD[@]}" "$@"
}

invoke_step() {
  local name="$1"
  local working_directory="$2"
  shift 2

  echo "==> $name"
  pushd "$working_directory" >/dev/null || exit 1
  "$@"
  local exit_code=$?
  popd >/dev/null || exit 1

  if [[ $exit_code -ne 0 ]]; then
    add_step_result "$name" "FAIL" "exit code $exit_code"
    write_step_summary 0
    exit "$exit_code"
  fi

  add_step_result "$name" "PASS"
  echo "PASS: $name"
}

invoke_if_available() {
  local command_name="$1"
  local description="$2"
  local working_directory="$3"
  shift 3

  if command -v "$command_name" >/dev/null 2>&1; then
    invoke_step "$description" "$working_directory" "$@"
  else
    local detail="missing $command_name"
    echo "SKIP: $description ($detail)"
    add_step_result "$description" "SKIP" "$detail"
  fi
}

run_install_python_dependencies() {
  invoke_python -m pip install -e ".[dev]"
}

run_install_frontend_dependencies() {
  npm ci
}

run_python_lint() {
  invoke_python -m ruff check .
}

run_python_format_check() {
  invoke_python -m ruff format . --check
}

run_python_typecheck() {
  invoke_python -m mypy frontier_tooling/ frontier_runtime/
}

run_python_tests() {
  invoke_python -m pytest apps/backend/tests tests -v --cov=app --cov=frontier_runtime --cov-report=term-missing
}

run_policy_tests() {
  invoke_python scripts/run_opa.py test policies/ -v
}

run_frontend_lint() {
  npm run lint
}

run_frontend_tests() {
  npm test
}

run_frontend_build() {
  npm run build
}

run_compose_config_validation() {
  docker compose config --quiet
  docker compose -f docker-compose.local.yml config --quiet
}

run_semgrep() {
  semgrep --config=auto --exclude .venv --exclude .next --exclude node_modules --exclude dist .
}

run_gitleaks() {
  gitleaks detect --source . --no-git --redact --config .gitleaks.toml
}

run_trivy() {
  trivy fs --scanners vuln,misconfig --severity HIGH,CRITICAL --exit-code 1 --skip-dirs .venv,.next,node_modules,dist,.claude/worktrees .
}

run_syft_sbom() {
  local sbom_dir="$REPO_ROOT/.artifacts/sbom"
  mkdir -p "$sbom_dir"
  "$SYFT_CMD" dir:. -o "cyclonedx-json=$sbom_dir/repository.cdx.json"
}

run_helm_validation() {
  "$HELM_CMD" lint ./helm/lattix-frontier
  "$HELM_CMD" template lattix ./helm/lattix-frontier -f helm/lattix-frontier/values-prod.yaml >/dev/null
}

get_python_command
get_opa_command
get_helm_command
get_syft_command
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8

invoke_step "Install Python dependencies" "$REPO_ROOT" run_install_python_dependencies
if command -v npm >/dev/null 2>&1; then
  invoke_step "Install frontend dependencies" "$FRONTEND_ROOT" run_install_frontend_dependencies
else
  npm_detail="missing npm"
  echo "SKIP: Install frontend dependencies ($npm_detail)"
  add_step_result "Install frontend dependencies" "SKIP" "$npm_detail"
fi
invoke_step "Python lint" "$REPO_ROOT" run_python_lint
invoke_step "Python format check" "$REPO_ROOT" run_python_format_check
invoke_step "Python typecheck" "$REPO_ROOT" run_python_typecheck
invoke_step "Python tests" "$REPO_ROOT" run_python_tests

if [[ -n "$OPA_CMD" ]]; then
  invoke_step "Policy tests" "$REPO_ROOT" run_policy_tests
else
  policy_detail="OPA missing; install to .tools/opa/opa or add 'opa' to PATH"
  echo "SKIP: Policy tests ($policy_detail)"
  add_step_result "Policy tests" "SKIP" "$policy_detail"
fi

if command -v npm >/dev/null 2>&1; then
  invoke_step "Frontend lint" "$FRONTEND_ROOT" run_frontend_lint
  invoke_step "Frontend tests" "$FRONTEND_ROOT" run_frontend_tests
  invoke_step "Frontend build" "$FRONTEND_ROOT" run_frontend_build
else
  npm_detail="missing npm"
  echo "SKIP: Frontend lint ($npm_detail)"
  add_step_result "Frontend lint" "SKIP" "$npm_detail"
  echo "SKIP: Frontend tests ($npm_detail)"
  add_step_result "Frontend tests" "SKIP" "$npm_detail"
  echo "SKIP: Frontend build ($npm_detail)"
  add_step_result "Frontend build" "SKIP" "$npm_detail"
fi

invoke_if_available docker "Compose config validation" "$REPO_ROOT" run_compose_config_validation
invoke_if_available semgrep "SAST via Semgrep" "$REPO_ROOT" run_semgrep
invoke_if_available gitleaks "Secret scanning via Gitleaks" "$REPO_ROOT" run_gitleaks
invoke_if_available trivy "SCA/config via Trivy" "$REPO_ROOT" run_trivy

if [[ -n "$SYFT_CMD" ]]; then
  invoke_step "SBOM generation via Syft" "$REPO_ROOT" run_syft_sbom
else
  sbom_detail="missing syft (install Syft to .tools/syft/syft or add syft to PATH)"
  echo "SKIP: SBOM generation via Syft ($sbom_detail)"
  add_step_result "SBOM generation via Syft" "SKIP" "$sbom_detail"
fi

if [[ -n "$HELM_CMD" ]]; then
  invoke_step "Helm chart validation" "$REPO_ROOT" run_helm_validation
else
  helm_detail="missing helm (install Helm to .tools/helm/helm or add helm to PATH)"
  echo "SKIP: Helm chart validation ($helm_detail)"
  add_step_result "Helm chart validation" "SKIP" "$helm_detail"
fi

write_step_summary 1
echo "All pre-commit checks passed."