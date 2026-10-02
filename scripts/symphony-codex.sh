#!/usr/bin/env bash
set -euo pipefail

route="${SYMPHONY_INFERENCE_ROUTE:-native}"
reasoning_effort="${SYMPHONY_REASONING_EFFORT:-high}"

case "${reasoning_effort}" in
  minimal|low|medium|high|xhigh) ;;
  *)
    echo "Unsupported SYMPHONY_REASONING_EFFORT: ${reasoning_effort}" >&2
    exit 2
    ;;
esac

codex_args=(
  --disable apps
  -c 'service_tier="fast"'
  -c "model_reasoning_effort=\"${reasoning_effort}\""
)

case "${route}" in
  native)
    model="${SYMPHONY_NATIVE_MODEL:-gpt-5.6-sol}"
    ;;
  omniroute)
    if [[ -z "${OMNIROUTE_API_KEY:-}" ]]; then
      echo "OMNIROUTE_API_KEY is required for the OmniRoute inference lane." >&2
      exit 2
    fi
    model="${SYMPHONY_OMNIROUTE_MODEL:-auto}"
    codex_args+=(
      -c 'model_provider="omniroute"'
      -c 'model_providers.omniroute.name="OmniRoute"'
      -c 'model_providers.omniroute.base_url="http://127.0.0.1:20128/v1"'
      -c 'model_providers.omniroute.env_key="OMNIROUTE_API_KEY"'
      -c 'model_providers.omniroute.wire_api="responses"'
      -c 'model_providers.omniroute.request_max_retries=2'
      -c 'model_providers.omniroute.stream_max_retries=2'
      -c 'model_providers.omniroute.stream_idle_timeout_ms=300000'
    )
    ;;
  *)
    echo "Unsupported SYMPHONY_INFERENCE_ROUTE: ${route}" >&2
    exit 2
    ;;
esac

if [[ -z "${model}" || "${model}" =~ [[:space:]] ]]; then
  echo "The selected model must be a non-empty identifier without whitespace." >&2
  exit 2
fi

exec codex "${codex_args[@]}" -c "model=\"${model}\"" app-server
