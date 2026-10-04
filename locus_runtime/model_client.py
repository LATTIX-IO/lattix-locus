"""Unified model client: one OpenAI-compatible client for every engine (LOCUS-336).

Every model request in Locus -- backend chat (tool loop and streaming), harness
code/team nodes, skill eval/test -- goes through :class:`ModelClient`:

* **One transport.** Each provider in :data:`PROVIDERS` exposes an
  OpenAI-compatible ``/chat/completions`` endpoint (NVIDIA NIM, local Ollama,
  OpenAI, Azure OpenAI v1, Gemini's OpenAI endpoint, Mistral, xAI, Anthropic's
  compatibility endpoint). The client uses the ``openai`` SDK (already a
  dependency) for chat completions, tool calls and SSE streaming; tests inject
  an ``httpx`` transport, so no server is needed.
* **Keys by reference (P10).** API keys resolve through
  :class:`ProviderKeyStore`: environment → OS keychain → Windows DPAPI
  (:mod:`locus_tooling.native_secrets`). Keys are never logged, never returned by
  an API and never placed in an audit record.
* **Gated (13 §4).** Before *every* request the client asks its
  :class:`ModelCallGate` to authorize a ``model_call`` gateway action (engine
  allowed, egress host allowed, data ceiling, budget figures). After the call it
  records usage (provider, model, tokens in/out, estimated cost) -- never prompt
  or completion text.
* **Typed errors.** Failures raise :class:`ModelProviderError` with the stable
  codes of the backend's ``ProviderUnavailableError`` contract:
  ``provider_not_configured`` (412), ``provider_call_failed`` (424), plus
  ``model_call_denied`` (403) when the gateway refuses the call. Reasons are
  redacted.
* **Tiering (D-21).** :class:`ModelRouter` walks an explicit chain of tiers --
  by default hosted NIM, then local Ollama -- and records every fallback with
  its reason (:class:`FallbackEvent`); a fallback is never silent (P16).
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

from locus_runtime import telemetry
from locus_runtime.gateway import (
    BudgetFigures,
    GatewaySession,
    authorize_action,
    host_of,
    is_loopback_host,
    redact_text,
)

from locus_runtime.telemetry import semconv as telemetry_semconv

logger = logging.getLogger(__name__)

ModelErrorCode = Literal["provider_not_configured", "provider_call_failed", "model_call_denied"]
PROVIDER_NOT_CONFIGURED: ModelErrorCode = "provider_not_configured"
PROVIDER_CALL_FAILED: ModelErrorCode = "provider_call_failed"
MODEL_CALL_DENIED: ModelErrorCode = "model_call_denied"
_HTTP_STATUS: dict[str, int] = {
    PROVIDER_NOT_CONFIGURED: 412,
    PROVIDER_CALL_FAILED: 424,
    MODEL_CALL_DENIED: 403,
}

#: Gateway tool label prefix for model calls (``model:<provider>``).
MODEL_TOOL_PREFIX = "model:"
#: Comma-separated provider-qualified tiers, e.g. ``nim/nvidia/nemotron-3-ultra-550b-a55b,ollama/gpt-oss:20b``.
AGENT_CHAIN_ENV = "LOCUS_AGENT_MODEL_CHAIN"
#: ``0`` disables the implicit local (Ollama) fallback behind an explicit hosted engine.
LOCAL_FALLBACK_ENV = "LOCUS_MODEL_LOCAL_FALLBACK"
#: JSON ``{"provider" | "provider/model": [usd_per_mtok_in, usd_per_mtok_out]}``.
PRICES_ENV = "LOCUS_MODEL_PRICES"
MAX_TOKENS_PER_RUN_ENV = "LOCUS_MODEL_MAX_TOKENS_PER_RUN"
MAX_COST_PER_RUN_ENV = "LOCUS_MODEL_MAX_COST_USD_PER_RUN"
_REASON_MAX = 300


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
_EXTRA_REDACTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)([?&]key=)[^&\s'\"]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{8,}"), r"\1[REDACTED]"),
    (re.compile(r"\bnvapi-[A-Za-z0-9_-]{8,}"), "[REDACTED]"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{12,}"), "[REDACTED]"),
)


def redact_reason(text: Any, *, secrets: Sequence[str] = ()) -> str:
    """Redacted, bounded error text: no keys, tokens or URL credentials (P10)."""
    value = str(text or "")
    for secret in secrets:
        if secret and len(secret) >= 6:
            value = value.replace(secret, "[REDACTED]")
    for pattern, replacement in _EXTRA_REDACTIONS:
        value = pattern.sub(replacement, value)
    return redact_text(value, limit=_REASON_MAX)


#: P28 provenance policy: model families and publishers from a P28-listed origin
#: (Qwen, DeepSeek, Yi, GLM, Kimi and their publishers). Hosted/API inference of
#: these is always refused; on a local engine (loopback) a model is allowed only
#: with a passing local-model provenance attestation (D-29, :func:`provenance_denial`).
_EXCLUDED_MODEL_PATTERN = re.compile(
    r"(^|[/:_.-])(qwen|qwq|deepseek|yi-|yi_|01-ai|glm|chatglm|z-ai|zhipu|kimi|moonshot|"
    r"baichuan|internlm|minimax|ernie|hunyuan|doubao)",
    re.IGNORECASE,
)


def listed_model_lineage(model: str) -> str:
    """The P28-listed family ``model`` belongs to (``"qwen"``, ``"deepseek"``, ...), or ``""``."""
    match = _EXCLUDED_MODEL_PATTERN.search(str(model or "").strip())
    return match.group(2).lower().rstrip("-_") if match else ""


def is_provenance_excluded(model: str) -> bool:
    """True when ``model`` is of a P28-listed lineage (checked on every endpoint resolve).

    Such a model is refused for hosted inference whatever any attestation says; a
    local engine may still serve it with a passing attestation (:func:`provenance_denial`).
    """
    return bool(listed_model_lineage(model))


def _local_model_verdict(provider: str, model: str) -> tuple[bool, str]:
    """D-29 attestation lookup for a listed-lineage model on a local engine (fail closed)."""
    try:
        from locus_tooling.provenance.models import local_model_verdict

        verdict = local_model_verdict(provider, model)
    except Exception as exc:  # noqa: BLE001 - any lookup failure denies
        return False, f"attestation lookup failed ({type(exc).__name__})"
    return verdict.passing, verdict.reason


def provenance_denial(provider: str, model: str, base_url: str) -> str:
    """Why P28/D-29 refuses ``model`` at ``base_url``, or ``""`` when it may run.

    * Not of a listed lineage: allowed (other controls still apply).
    * Listed lineage on a non-loopback endpoint (hosted, API or web inference):
      refused, whatever the attestation says.
    * Listed lineage on a loopback engine: allowed only with a passing local-model
      attestation whose weights digest matches what the engine will load.
    """
    lineage = listed_model_lineage(model)
    if not lineage:
        return ""
    host = host_of(base_url) if base_url else ""
    if not host or not is_loopback_host(host):
        return (
            f"hosted inference of a P28-listed model lineage ('{lineage}') is excluded "
            "by the provenance policy (P28, D-29)"
        )
    allowed, reason = _local_model_verdict(provider, model)
    if allowed:
        return ""
    return (
        f"local model of P28-listed lineage '{lineage}' needs a passing provenance "
        f"attestation (D-29): {reason}"
    )


class ModelProviderError(RuntimeError):
    """A model call could not be served. Same contract as ``ProviderUnavailableError``."""

    def __init__(
        self,
        *,
        code: ModelErrorCode,
        provider: str,
        model: str,
        reason: str,
        audit_id: str = "",
    ) -> None:
        self.code: ModelErrorCode = code
        self.provider = str(provider or "").strip() or "unknown"
        self.model = str(model or "").strip() or "unknown"
        self.reason = redact_reason(reason) or "unavailable"
        self.audit_id = audit_id
        super().__init__(self.message)

    @property
    def http_status(self) -> int:
        return _HTTP_STATUS.get(self.code, 424)

    @property
    def message(self) -> str:
        if self.code == PROVIDER_NOT_CONFIGURED:
            return (
                f"Model provider '{self.provider}' is not configured for model "
                f"'{self.model}': {self.reason}."
            )
        if self.code == MODEL_CALL_DENIED:
            return (
                f"Model call to '{self.provider}' (model '{self.model}') was denied by the "
                f"gateway: {self.reason}"
            )
        return (
            f"Model provider '{self.provider}' call failed for model '{self.model}': {self.reason}"
        )


class ModelCallDenied(ModelProviderError):
    """The gateway did not allow the model call (deny, or ask without approval)."""

    def __init__(
        self, *, provider: str, model: str, reason: str, audit_id: str = "", outcome: str = "deny"
    ) -> None:
        super().__init__(
            code=MODEL_CALL_DENIED, provider=provider, model=model, reason=reason, audit_id=audit_id
        )
        self.outcome = outcome


# --------------------------------------------------------------------------- #
# Provider registry
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ProviderSpec:
    """One engine provider with an OpenAI-compatible chat-completions endpoint."""

    id: str
    label: str
    default_base_url: str
    key_env: tuple[str, ...]
    model_env: str
    default_model: str
    key_required: bool = True
    base_url_required: bool = False
    #: Runs on this machine by default (the risk class is still derived from the host).
    local: bool = False
    #: Hosts the registry vouches for. The gateway also accepts operator egress hosts.
    allowlisted_hosts: tuple[str, ...] = ()
    #: USD per million tokens (in, out). ``None`` = unknown unless configured.
    price_per_mtok: tuple[float, float] | None = None
    #: Server returns usage on streams when asked (``stream_options.include_usage``).
    stream_usage: bool = False
    #: Reasoning effort is a top-level parameter (OpenAI) rather than a body extra.
    top_level_reasoning: bool = False


_LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")

PROVIDERS: dict[str, ProviderSpec] = {
    "openai": ProviderSpec(
        id="openai",
        label="OpenAI",
        default_base_url="https://api.openai.com/v1",
        key_env=("OPENAI_API_KEY",),
        model_env="OPENAI_MODEL",
        default_model="gpt-5.2",
        allowlisted_hosts=("api.openai.com",),
        stream_usage=True,
        top_level_reasoning=True,
    ),
    "anthropic": ProviderSpec(
        id="anthropic",
        label="Anthropic Claude",
        default_base_url="https://api.anthropic.com/v1",
        key_env=("ANTHROPIC_API_KEY",),
        model_env="ANTHROPIC_MODEL",
        default_model="claude-sonnet-4-6",
        allowlisted_hosts=("api.anthropic.com",),
    ),
    "azure": ProviderSpec(
        id="azure",
        label="Microsoft Azure OpenAI",
        # Resource-specific: https://<resource>.openai.azure.com/openai/v1
        default_base_url="",
        key_env=("AZURE_OPENAI_API_KEY",),
        model_env="AZURE_OPENAI_DEPLOYMENT",
        default_model="",
        base_url_required=True,
    ),
    "google": ProviderSpec(
        id="google",
        label="Google Gemini",
        default_base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        key_env=("GOOGLE_API_KEY", "GEMINI_API_KEY"),
        model_env="GEMINI_MODEL",
        default_model="gemini-2.5-pro",
        allowlisted_hosts=("generativelanguage.googleapis.com",),
    ),
    "mistral": ProviderSpec(
        id="mistral",
        label="Mistral",
        default_base_url="https://api.mistral.ai/v1",
        key_env=("MISTRAL_API_KEY",),
        model_env="MISTRAL_MODEL",
        default_model="mistral-large-latest",
        allowlisted_hosts=("api.mistral.ai",),
    ),
    "xai": ProviderSpec(
        id="xai",
        label="xAI Grok",
        default_base_url="https://api.x.ai/v1",
        key_env=("XAI_API_KEY",),
        model_env="XAI_MODEL",
        default_model="grok-4",
        allowlisted_hosts=("api.x.ai",),
    ),
    # D-21: hosted NVIDIA NIM (API catalog). The free catalog tier is metered at 0
    # unless LOCUS_MODEL_PRICES says otherwise. The default model is configurable
    # (NIM_MODEL / settings / LOCUS_AGENT_MODEL_CHAIN); catalog ids change.
    "nim": ProviderSpec(
        id="nim",
        label="NVIDIA NIM",
        default_base_url="https://integrate.api.nvidia.com/v1",
        key_env=("NVIDIA_API_KEY", "NIM_API_KEY"),
        model_env="NIM_MODEL",
        default_model="nvidia/nemotron-3-ultra-550b-a55b",
        allowlisted_hosts=("integrate.api.nvidia.com",),
        price_per_mtok=(0.0, 0.0),
    ),
    "ollama": ProviderSpec(
        id="ollama",
        label="Local (Ollama)",
        default_base_url="http://localhost:11434/v1",
        key_env=(),
        model_env="OLLAMA_MODEL",
        default_model="llama3.2:3b",
        key_required=False,
        local=True,
        allowlisted_hosts=_LOCAL_HOSTS,
        price_per_mtok=(0.0, 0.0),
        stream_usage=True,
    ),
}


def resolve_provider(model: str, *, default: str = "openai") -> tuple[str, str]:
    """Split a provider-qualified model id into ``(provider, bare_model)``.

    ``nim/nvidia/nemotron-3-ultra-550b-a55b`` → ``("nim", "nvidia/nemotron-3-ultra-550b-a55b")``;
    an unqualified id belongs to ``default``.
    """
    candidate = str(model or "").strip()
    lowered = candidate.lower()
    for provider in PROVIDERS:
        token = f"{provider}/"
        if lowered.startswith(token):
            return provider, candidate[len(token) :].strip()
    return default, candidate


def is_placeholder_key(value: str) -> bool:
    lowered = str(value or "").strip().lower()
    return not lowered or "change" in lowered or "your-" in lowered


# --------------------------------------------------------------------------- #
# Configuration (non-secret) and keys (secret, by reference)
# --------------------------------------------------------------------------- #
@runtime_checkable
class ProviderSettings(Protocol):
    """Non-secret provider configuration: ``base_url`` and ``default_model``."""

    def value(self, provider: str, field: str) -> str: ...


class EnvProviderSettings:
    """Environment / registry defaults (``<PROVIDER>_BASE_URL``, the model env var)."""

    def value(self, provider: str, field: str) -> str:
        spec = PROVIDERS.get(provider)
        if spec is None:
            return ""
        if field == "base_url":
            if provider == "ollama":
                native = str(os.getenv("OLLAMA_BASE_URL") or "").strip().rstrip("/")
                if native:
                    return native if native.endswith("/v1") else f"{native}/v1"
                return spec.default_base_url
            return str(os.getenv(f"{provider.upper()}_BASE_URL") or "").strip() or (
                spec.default_base_url
            )
        if field == "default_model":
            env_value = str(os.getenv(spec.model_env) or "").strip() if spec.model_env else ""
            return env_value or spec.default_model
        return ""


KeySource = Literal["env", "keychain", "none"]


class ProviderKeyStore:
    """Provider API keys by reference: env → OS keychain → Windows DPAPI (P10).

    The keychain entry for a provider is named after its first key variable
    (``NVIDIA_API_KEY`` for NIM, ``OPENAI_API_KEY`` for OpenAI, ...), so an
    operator's environment variable and the stored key are interchangeable.
    Values are returned only to the client that sends them; nothing here logs.
    """

    def __init__(self, *, app_home: Any = None) -> None:
        self._app_home = app_home

    @staticmethod
    def secret_name(provider: str) -> str:
        spec = PROVIDERS.get(provider)
        if spec is None or not spec.key_env:
            raise ValueError(f"provider '{provider}' takes no API key")
        return spec.key_env[0]

    def _lookup(self, provider: str) -> tuple[str, KeySource]:
        spec = PROVIDERS.get(provider)
        if spec is None or not spec.key_env:
            return "", "none"
        for name in spec.key_env:
            env_value = str(os.getenv(name) or "").strip()
            if env_value:
                return env_value, "env"
        try:
            from locus_tooling.native_secrets import SecretStorageUnavailable, get_secret
        except ImportError:  # pragma: no cover - tooling always ships with the runtime
            return "", "none"
        try:
            value = get_secret(spec.key_env[0], app_home=self._app_home)
        except SecretStorageUnavailable:
            # No secure store on this host: the key is simply not available.
            return "", "none"
        except Exception as exc:  # noqa: BLE001 - a broken keychain is "not configured"
            logger.warning("provider key lookup failed for %s: %s", provider, type(exc).__name__)
            return "", "none"
        return (str(value or "").strip(), "keychain") if value else ("", "none")

    def get(self, provider: str) -> str:
        return self._lookup(provider)[0]

    def source(self, provider: str) -> KeySource:
        return self._lookup(provider)[1]

    def configured(self, provider: str) -> bool:
        return not is_placeholder_key(self.get(provider))

    def set(self, provider: str, value: str) -> str:
        """Store a key in the strongest secure store; returns the storage mode."""
        from locus_tooling.native_secrets import set_secret

        clean = str(value or "").strip()
        if not clean:
            raise ValueError("an API key value is required")
        return str(set_secret(self.secret_name(provider), clean, app_home=self._app_home))

    def clear(self, provider: str) -> None:
        from locus_tooling.native_secrets import delete_secret

        delete_secret(self.secret_name(provider), app_home=self._app_home)


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ModelEndpoint:
    provider: str
    model: str
    base_url: str
    api_key: str = field(default="", repr=False)

    @property
    def spec(self) -> ProviderSpec | None:
        return PROVIDERS.get(self.provider)

    @property
    def egress_host(self) -> str:
        return host_of(self.base_url)

    @property
    def local(self) -> bool:
        return is_loopback_host(self.egress_host)

    @property
    def qualified_model(self) -> str:
        return f"{self.provider}/{self.model}"


def provider_default_model(provider: str, settings: ProviderSettings | None = None) -> str:
    settings = settings or EnvProviderSettings()
    return str(settings.value(provider, "default_model") or "").strip() or (
        PROVIDERS[provider].default_model if provider in PROVIDERS else ""
    )


def resolve_endpoint(
    provider: str,
    model: str = "",
    *,
    settings: ProviderSettings | None = None,
    keys: ProviderKeyStore | None = None,
    base_url: str = "",
    api_key: str = "",
) -> ModelEndpoint:
    """Resolve a provider + model to a callable endpoint, or raise ``provider_not_configured``.

    ``base_url``/``api_key`` override the registry (a user-scoped runtime); the key
    otherwise resolves through ``keys`` (env → keychain → DPAPI).
    """
    provider_id = str(provider or "").strip().lower()
    spec = PROVIDERS.get(provider_id)
    settings = settings or EnvProviderSettings()
    bare = str(model or "").strip() or provider_default_model(provider_id, settings)
    if spec is None:
        raise ModelProviderError(
            code=PROVIDER_NOT_CONFIGURED,
            provider=provider_id,
            model=bare,
            reason=f"unknown model provider '{provider_id}'",
        )
    url = str(base_url or settings.value(provider_id, "base_url") or "").strip().rstrip("/")
    denial = provenance_denial(provider_id, bare, url)
    if denial:
        raise ModelProviderError(
            code=MODEL_CALL_DENIED,
            provider=provider_id,
            model=bare,
            reason=denial,
        )
    if not url or not host_of(url):
        raise ModelProviderError(
            code=PROVIDER_NOT_CONFIGURED,
            provider=provider_id,
            model=bare,
            reason=f"{spec.label} endpoint is not configured",
        )
    key = str(api_key or "").strip()
    if not key and spec.key_env:
        key = (keys or ProviderKeyStore()).get(provider_id)
    if spec.key_required and is_placeholder_key(key):
        names = " / ".join(spec.key_env)
        raise ModelProviderError(
            code=PROVIDER_NOT_CONFIGURED,
            provider=provider_id,
            model=bare,
            reason=f"{spec.label} API key missing or placeholder (set {names} or store it "
            "in the keychain via Settings)",
        )
    if not bare:
        raise ModelProviderError(
            code=PROVIDER_NOT_CONFIGURED,
            provider=provider_id,
            model="",
            reason=f"no model configured for {spec.label}",
        )
    return ModelEndpoint(provider=provider_id, model=bare, base_url=url, api_key=key)


# --------------------------------------------------------------------------- #
# Usage, cost and budgets
# --------------------------------------------------------------------------- #
def _configured_prices() -> dict[str, tuple[float, float]]:
    raw = str(os.getenv(PRICES_ENV) or "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        logger.warning("%s is not valid JSON; ignoring", PRICES_ENV)
        return {}
    prices: dict[str, tuple[float, float]] = {}
    if isinstance(data, dict):
        for key, value in data.items():
            try:
                prices[str(key).lower()] = (float(value[0]), float(value[1]))
            except (TypeError, ValueError, IndexError):
                continue
    return prices


def estimate_cost(provider: str, model: str, tokens_in: int, tokens_out: int) -> tuple[float, bool]:
    """``(usd, known)``: configured price (model, then provider), else the registry's."""
    prices = _configured_prices()
    price = prices.get(f"{provider}/{model}".lower()) or prices.get(provider.lower())
    if price is None:
        spec = PROVIDERS.get(provider)
        price = spec.price_per_mtok if spec is not None else None
    if price is None:
        return 0.0, False
    cost = (max(0, tokens_in) * price[0] + max(0, tokens_out) * price[1]) / 1_000_000
    return round(cost, 6), True


@dataclass(frozen=True)
class ModelUsage:
    """What a model call consumed. Carries no prompt or completion text."""

    provider: str
    model: str
    tokens_in: int
    tokens_out: int
    est_cost_usd: float
    cost_known: bool
    usage_reported: bool
    audit_id: str
    duration_ms: int
    ok: bool
    run_id: str = ""
    stream: bool = False

    def as_metadata(self) -> dict[str, Any]:
        """Audit/event shape. Counts are ``input_count``/``output_count`` (unit: tokens)
        because audit redaction masks any key containing "token"."""
        return {
            "provider": self.provider,
            "model": self.model,
            "input_count": self.tokens_in,
            "output_count": self.tokens_out,
            "count_unit": "tokens",
            "est_cost_usd": self.est_cost_usd,
            "cost_known": self.cost_known,
            "usage_reported": self.usage_reported,
            "gateway_audit_id": self.audit_id,
            "duration_ms": self.duration_ms,
            "ok": self.ok,
            "run_id": self.run_id,
            "stream": self.stream,
        }


class UsageMeter:
    """Per-run model token and spend totals (feeds the gateway's budget figures)."""

    def __init__(self, *, max_runs: int = 4096) -> None:
        self._totals: dict[str, list[float]] = {}
        self._lock = threading.Lock()
        self._max_runs = max_runs

    def add(self, usage: ModelUsage) -> None:
        key = usage.run_id or "_unscoped"
        with self._lock:
            totals = self._totals.setdefault(key, [0.0, 0.0, 0.0])
            totals[0] += usage.tokens_in
            totals[1] += usage.tokens_out
            totals[2] += usage.est_cost_usd
            while len(self._totals) > self._max_runs:
                self._totals.pop(next(iter(self._totals)))

    def totals(self, run_id: str) -> tuple[int, int, float]:
        with self._lock:
            values = self._totals.get(run_id or "_unscoped", [0.0, 0.0, 0.0])
            return int(values[0]), int(values[1]), float(values[2])

    def budget(
        self, run_id: str, *, max_tokens: float | None, max_cost_usd: float | None = None
    ) -> BudgetFigures | None:
        """Budget figures for ``budget_policy``; ``None`` when no limit is configured."""
        if not max_tokens:
            return None
        tokens_in, tokens_out, cost = self.totals(run_id)
        return BudgetFigures(
            tokens_used=float(tokens_in + tokens_out),
            max_tokens=float(max_tokens),
            cost_used_usd=cost if max_cost_usd else None,
            max_cost_usd=float(max_cost_usd) if max_cost_usd else None,
        )


def _env_float(name: str) -> float | None:
    raw = str(os.getenv(name) or "").strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def run_budget_limits() -> tuple[float | None, float | None]:
    """``(max_tokens, max_cost_usd)`` per run for model calls, from the environment."""
    return _env_float(MAX_TOKENS_PER_RUN_ENV), _env_float(MAX_COST_PER_RUN_ENV)


# --------------------------------------------------------------------------- #
# The gate (gateway ``model_call``)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ModelCall:
    """The facts of one model request, as the gateway sees them (no prompt text)."""

    provider: str
    model: str
    egress_host: str
    stream: bool = False
    tools: int = 0
    run_id: str = ""

    @property
    def local(self) -> bool:
        return is_loopback_host(self.egress_host)

    def gateway_kwargs(self) -> dict[str, Any]:
        return {
            "kind": "model_call",
            "tool": f"{MODEL_TOOL_PREFIX}{self.provider}",
            "target": f"{self.provider}/{self.model}",
            "args": {
                "provider": self.provider,
                "model": self.model,
                "stream": self.stream,
                "tools": self.tools,
            },
            "egress_host": self.egress_host,
        }


@runtime_checkable
class ModelCallGate(Protocol):
    def authorize(self, call: ModelCall) -> str:
        """Return the gateway audit id; raise :class:`ModelCallDenied` when not allowed."""
        ...

    def record(self, call: ModelCall, usage: ModelUsage) -> None: ...


SessionFactory = Callable[[ModelCall], "GatewaySession | None"]


class GatewayModelGate:
    """Authorizes each model call as a gateway ``model_call`` action.

    ``session_factory`` opens a fresh, short-lived session per call so the
    capabilities (egress hosts, budget figures from :class:`UsageMeter`) are
    current; ``session`` reuses one run session instead. With neither, the call
    is made as an unbound caller, which a real gateway denies (fail closed).
    """

    def __init__(
        self,
        *,
        session: GatewaySession | None = None,
        session_factory: SessionFactory | None = None,
        usage_sink: Callable[[ModelUsage], None] | None = None,
        meter: UsageMeter | None = None,
    ) -> None:
        self._session = session
        self._factory = session_factory
        self._usage_sink = usage_sink
        self.meter = meter

    def authorize(self, call: ModelCall) -> str:
        session = self._session
        opened: GatewaySession | None = None
        if session is None and self._factory is not None:
            opened = self._factory(call)
            session = opened
        try:
            decision = authorize_action(session, **call.gateway_kwargs())
        finally:
            if opened is not None:
                try:
                    opened.close()
                except Exception:  # noqa: BLE001 - closing is cleanup
                    logger.exception("model_call.session_close_error")
        if not decision.allowed:
            raise ModelCallDenied(
                provider=call.provider,
                model=call.model,
                reason=f"{decision.outcome}: {', '.join(decision.reasons) or 'no reason given'}",
                audit_id=decision.audit_id,
                outcome=decision.outcome,
            )
        return decision.audit_id

    def record(self, call: ModelCall, usage: ModelUsage) -> None:  # noqa: ARG002
        if self.meter is not None:
            self.meter.add(usage)
        if self._usage_sink is not None:
            try:
                self._usage_sink(usage)
            except Exception:  # noqa: BLE001 - metering must not fail the call it measures
                logger.exception("model_call.usage_sink_error")


# The process's model gate: the backend installs one bound to its gateway, audit
# log and usage meter, so every client built in this process is gated the same way.
_PROCESS_GATE_FACTORY: Callable[[], ModelCallGate] | None = None
_GATE_LOCK = threading.Lock()


def install_model_gate(factory: Callable[[], ModelCallGate] | None) -> None:
    """Install (``None`` removes) the factory for the default gate of new clients."""
    global _PROCESS_GATE_FACTORY
    with _GATE_LOCK:
        _PROCESS_GATE_FACTORY = factory


def model_gate_installed() -> bool:
    """Posture fact: model calls in this process default to an installed gateway gate."""
    return _PROCESS_GATE_FACTORY is not None


def default_gate() -> ModelCallGate:
    """The installed process gate, else an unbound gateway gate (a real gateway denies)."""
    factory = _PROCESS_GATE_FACTORY
    return factory() if factory is not None else GatewayModelGate()


# --------------------------------------------------------------------------- #
# Results
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class FallbackEvent:
    """One explicit tier change: why the previous engine was not used (P16)."""

    from_provider: str
    from_model: str
    to_provider: str
    to_model: str
    reason_code: str
    reason: str

    def as_metadata(self) -> dict[str, str]:
        return {
            "from_provider": self.from_provider,
            "from_model": self.from_model,
            "to_provider": self.to_provider,
            "to_model": self.to_model,
            "reason_code": self.reason_code,
            "reason": self.reason,
        }


@dataclass
class ModelResult:
    text: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)
    provider: str = ""
    model: str = ""
    reasoning: str = ""
    finish_reason: str = ""
    raw: Any = None
    fallbacks: list[FallbackEvent] = field(default_factory=list)

    def meta(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "mode": "live",
            "usage": dict(self.usage),
            "fallback_used": bool(self.fallbacks),
            "fallbacks": [event.as_metadata() for event in self.fallbacks],
        }


def _approx_tokens(messages: Any) -> int:
    try:
        text = json.dumps(messages, default=str)
    except (TypeError, ValueError):
        text = str(messages)
    return max(1, len(text) // 4)


def _usage_counts(usage: Any) -> tuple[int, int] | None:
    if usage is None:
        return None
    tokens_in = getattr(usage, "prompt_tokens", None)
    tokens_out = getattr(usage, "completion_tokens", None)
    if tokens_in is None and tokens_out is None:
        tokens_in = getattr(usage, "input_tokens", None)
        tokens_out = getattr(usage, "output_tokens", None)
    if tokens_in is None and tokens_out is None:
        return None
    return int(tokens_in or 0), int(tokens_out or 0)


def _message_reasoning(message: Any) -> str:
    for attr in ("reasoning", "reasoning_content"):
        value = getattr(message, attr, None)
        if isinstance(value, str) and value.strip():
            return value
    return ""


# --------------------------------------------------------------------------- #
# The client
# --------------------------------------------------------------------------- #
class _Completions:
    def __init__(self, owner: ModelClient) -> None:
        self._owner = owner

    def create(self, **kwargs: Any) -> Any:
        return self._owner.create_chat_completion(**kwargs)


class _Chat:
    def __init__(self, owner: ModelClient) -> None:
        self.completions = _Completions(owner)


class _Responses:
    def __init__(self, owner: ModelClient) -> None:
        self._owner = owner

    def create(self, **kwargs: Any) -> Any:
        return self._owner.create_response(**kwargs)


class ModelClient:
    """One OpenAI-compatible client for one endpoint; every request passes the gate.

    ``client.chat.completions.create(**kwargs)`` keeps the SDK surface the backend
    tool loop already uses (and is gated); :meth:`complete` and :meth:`stream` are
    the typed entry points for new code.
    """

    def __init__(
        self,
        endpoint: ModelEndpoint,
        *,
        gate: ModelCallGate | None = None,
        http_client: Any = None,
        timeout: float = 600.0,
        max_retries: int = 2,
        run_id: str = "",
    ) -> None:
        self.endpoint = endpoint
        self._gate: ModelCallGate = gate or default_gate()
        self._http_client = http_client
        self._timeout = timeout
        self._max_retries = max_retries
        self.run_id = run_id
        self._sdk_client: Any = None
        self._lock = threading.Lock()
        self.chat = _Chat(self)
        self.responses = _Responses(self)

    # -- identity ---------------------------------------------------------------
    @property
    def provider(self) -> str:
        return self.endpoint.provider

    @property
    def model(self) -> str:
        return self.endpoint.model

    @property
    def base_url(self) -> str:
        return self.endpoint.base_url

    def __repr__(self) -> str:
        return f"ModelClient(provider={self.provider!r}, model={self.model!r}, base_url={self.base_url!r})"

    # -- transport --------------------------------------------------------------
    def _sdk(self) -> Any:
        with self._lock:
            if self._sdk_client is None:
                try:
                    from openai import OpenAI
                except ImportError as exc:  # pragma: no cover - dependency of the platform
                    raise ModelProviderError(
                        code=PROVIDER_NOT_CONFIGURED,
                        provider=self.provider,
                        model=self.model,
                        reason="the 'openai' package is not installed",
                    ) from exc
                kwargs: dict[str, Any] = {
                    "api_key": self.endpoint.api_key or "not-needed",
                    "base_url": self.endpoint.base_url,
                    "timeout": self._timeout,
                    "max_retries": self._max_retries,
                }
                if self._http_client is not None:
                    kwargs["http_client"] = self._http_client
                self._sdk_client = OpenAI(**kwargs)
            return self._sdk_client

    @property
    def models(self) -> Any:
        """Model listing (catalog metadata, not a model call; not gated)."""
        return self._sdk().models

    def _call(self, model: str, *, stream: bool, tools: int) -> ModelCall:
        return ModelCall(
            provider=self.provider,
            model=model,
            egress_host=self.endpoint.egress_host,
            stream=stream,
            tools=tools,
            run_id=self.run_id,
        )

    def _failed(self, model: str, exc: BaseException, audit_id: str = "") -> ModelProviderError:
        status = getattr(exc, "status_code", None)
        reason = f"{type(exc).__name__}"
        if status:
            reason += f" (HTTP {status})"
        detail = str(exc)
        if detail:
            reason += f": {detail}"
        return ModelProviderError(
            code=PROVIDER_CALL_FAILED,
            provider=self.provider,
            model=model,
            reason=redact_reason(reason, secrets=(self.endpoint.api_key,)),
            audit_id=audit_id,
        )

    def _record(
        self,
        call: ModelCall,
        audit_id: str,
        started: float,
        *,
        counts: tuple[int, int] | None,
        estimate: tuple[int, int],
        ok: bool,
    ) -> ModelUsage:
        tokens_in, tokens_out = counts if counts is not None else estimate
        cost, known = estimate_cost(call.provider, call.model, tokens_in, tokens_out)
        usage = ModelUsage(
            provider=call.provider,
            model=call.model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            est_cost_usd=cost,
            cost_known=known,
            usage_reported=counts is not None,
            audit_id=audit_id,
            duration_ms=int((time.monotonic() - started) * 1000),
            ok=ok,
            run_id=call.run_id,
            stream=call.stream,
        )
        try:
            self._gate.record(call, usage)
        except Exception:  # noqa: BLE001 - metering never fails a call
            logger.exception("model_call.record_error")
        return usage

    # -- SDK-shaped, gated -----------------------------------------------------
    def create_chat_completion(self, **kwargs: Any) -> Any:
        """Gated ``chat.completions.create``. Streams return a metered iterator.

        Each request is one ``chat`` telemetry span (provider, model, tokens,
        cost, latency, error type; content only when capture is on, redacted)."""
        model = str(kwargs.get("model") or self.model)
        kwargs["model"] = model
        stream = bool(kwargs.get("stream"))
        spec = self.endpoint.spec
        if stream and spec is not None and spec.stream_usage and "stream_options" not in kwargs:
            kwargs["stream_options"] = {"include_usage": True}
        call = self._call(model, stream=stream, tools=len(kwargs.get("tools") or []))
        span = telemetry.start_chat(
            provider=self.provider,
            model=model,
            server_address=call.egress_host,
            stream=stream,
            request=kwargs,
        )
        try:
            with span.activate():
                audit_id = self._gate.authorize(call)
                started = time.monotonic()
                prompt_estimate = _approx_tokens(kwargs.get("messages"))
                try:
                    response = self._sdk().chat.completions.create(**kwargs)
                except ModelProviderError:
                    raise
                except Exception as exc:  # noqa: BLE001 - typed, redacted provider failure
                    usage = self._record(
                        call,
                        audit_id,
                        started,
                        counts=(0, 0),
                        estimate=(prompt_estimate, 0),
                        ok=False,
                    )
                    telemetry.record_usage(span, usage)
                    raise self._failed(model, exc, audit_id) from exc
        except BaseException as exc:
            span.error(exc)
            span.end()
            raise
        if stream:
            return self._metered_stream(response, call, audit_id, started, prompt_estimate, span)
        text = ""
        try:
            text = response.choices[0].message.content or ""
        except Exception:  # noqa: BLE001 - shape varies; usage is what matters here
            pass
        usage = self._record(
            call,
            audit_id,
            started,
            counts=_usage_counts(getattr(response, "usage", None)),
            estimate=(prompt_estimate, max(1, len(text) // 4) if text else 0),
            ok=True,
        )
        _record_response(span, response, usage)
        span.end()
        return response

    def _metered_stream(
        self,
        response: Any,
        call: ModelCall,
        audit_id: str,
        started: float,
        prompt_estimate: int,
        span: telemetry.SpanHandle | None = None,
    ) -> Iterator[Any]:
        counts: tuple[int, int] | None = None
        out_chars = 0
        ok = False
        capture = span is not None and telemetry.content_capture_enabled()
        parts: list[str] = []
        finish = ""
        try:
            for chunk in response:
                found = _usage_counts(getattr(chunk, "usage", None))
                if found is not None:
                    counts = found
                for choice in getattr(chunk, "choices", None) or []:
                    delta = getattr(choice, "delta", None)
                    piece = str(getattr(delta, "content", "") or "")
                    out_chars += len(piece)
                    finish = str(getattr(choice, "finish_reason", "") or finish)
                    if capture and piece:
                        parts.append(piece)
                yield chunk
            ok = True
        except ModelProviderError as exc:
            if span is not None:
                span.error(exc)
            raise
        except Exception as exc:  # noqa: BLE001 - typed, redacted provider failure
            failure = self._failed(call.model, exc, audit_id)
            if span is not None:
                span.error(failure)
            raise failure from exc
        finally:
            usage = self._record(
                call,
                audit_id,
                started,
                counts=counts,
                estimate=(prompt_estimate, out_chars // 4),
                ok=ok,
            )
            if span is not None:
                telemetry.record_usage(span, usage)
                if finish:
                    span.set(telemetry_semconv.GEN_AI_RESPONSE_FINISH_REASONS, [finish])
                if parts:
                    span.content(
                        telemetry_semconv.GEN_AI_OUTPUT_MESSAGES,
                        [{"role": "assistant", "content": "".join(parts)}],
                    )
                span.end()

    def create_response(self, **kwargs: Any) -> Any:
        """Gated OpenAI Responses API call (OpenAI only)."""
        model = str(kwargs.get("model") or self.model)
        kwargs["model"] = model
        call = self._call(model, stream=False, tools=len(kwargs.get("tools") or []))
        span = telemetry.start_chat(
            provider=self.provider, model=model, server_address=call.egress_host
        )
        try:
            with span.activate():
                audit_id = self._gate.authorize(call)
                started = time.monotonic()
                prompt_estimate = _approx_tokens(kwargs.get("input"))
                try:
                    response = self._sdk().responses.create(**kwargs)
                except Exception as exc:  # noqa: BLE001
                    telemetry.record_usage(
                        span,
                        self._record(
                            call,
                            audit_id,
                            started,
                            counts=(0, 0),
                            estimate=(prompt_estimate, 0),
                            ok=False,
                        ),
                    )
                    raise self._failed(model, exc, audit_id) from exc
        except BaseException as exc:
            span.error(exc)
            span.end()
            raise
        usage = self._record(
            call,
            audit_id,
            started,
            counts=_usage_counts(getattr(response, "usage", None)),
            estimate=(prompt_estimate, 0),
            ok=True,
        )
        telemetry.record_usage(span, usage)
        span.end()
        return response

    # -- typed entry points ----------------------------------------------------
    def _request(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None,
        temperature: float | None,
        top_p: float | None,
        max_tokens: int | None,
        reasoning_effort: str | None,
        extra: Mapping[str, Any] | None,
        model: str | None,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {"model": model or self.model, "messages": messages}
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        if temperature is not None:
            kwargs["temperature"] = temperature
        if top_p is not None:
            kwargs["top_p"] = top_p
        if max_tokens:
            kwargs["max_tokens"] = max_tokens
        if reasoning_effort:
            spec = self.endpoint.spec
            if spec is not None and spec.top_level_reasoning:
                kwargs["reasoning_effort"] = reasoning_effort
            else:
                # OpenAI-compatible servers that do not know it ignore a body extra.
                body = dict(kwargs.get("extra_body") or {})
                body["reasoning_effort"] = reasoning_effort
                kwargs["extra_body"] = body
        if extra:
            kwargs.update(extra)
        return kwargs

    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        max_tokens: int | None = None,
        reasoning_effort: str | None = None,
        extra: Mapping[str, Any] | None = None,
        model: str | None = None,
    ) -> ModelResult:
        kwargs = self._request(
            messages,
            tools=tools,
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
            reasoning_effort=reasoning_effort,
            extra=extra,
            model=model,
        )
        completion = self.create_chat_completion(**kwargs)
        try:
            choice = completion.choices[0]
            message = choice.message
        except Exception as exc:  # noqa: BLE001 - an empty/garbled body is a failed call
            raise self._failed(kwargs["model"], ValueError("response had no choices")) from exc
        tool_calls = [
            {
                "id": str(getattr(call, "id", "") or ""),
                "name": str(call.function.name or ""),
                "arguments": call.function.arguments or "{}",
            }
            for call in (getattr(message, "tool_calls", None) or [])
        ]
        counts = _usage_counts(getattr(completion, "usage", None))
        usage = {"prompt_tokens": counts[0], "completion_tokens": counts[1]} if counts else {}
        return ModelResult(
            text=str(message.content or ""),
            tool_calls=tool_calls,
            usage=usage,
            provider=self.provider,
            model=str(kwargs["model"]),
            reasoning=_message_reasoning(message),
            finish_reason=str(getattr(choice, "finish_reason", "") or ""),
            raw=completion,
        )

    def stream(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        max_tokens: int | None = None,
        reasoning_effort: str | None = None,
        extra: Mapping[str, Any] | None = None,
        model: str | None = None,
        on_chunk: Callable[[str], None] | None = None,
    ) -> ModelResult:
        """Stream a completion over SSE; ``on_chunk`` gets each text delta.

        Tool-call deltas are assembled by index into complete calls.
        """
        kwargs = self._request(
            messages,
            tools=tools,
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
            reasoning_effort=reasoning_effort,
            extra=extra,
            model=model,
        )
        kwargs["stream"] = True
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        calls: dict[int, dict[str, Any]] = {}
        usage: dict[str, Any] = {}
        finish = ""
        for chunk in self.create_chat_completion(**kwargs):
            counts = _usage_counts(getattr(chunk, "usage", None))
            if counts is not None:
                usage = {"prompt_tokens": counts[0], "completion_tokens": counts[1]}
            for choice in getattr(chunk, "choices", None) or []:
                finish = str(getattr(choice, "finish_reason", "") or finish)
                delta = getattr(choice, "delta", None)
                if delta is None:
                    continue
                piece = str(getattr(delta, "content", "") or "")
                if piece:
                    text_parts.append(piece)
                    if on_chunk is not None:
                        on_chunk(piece)
                reasoning = _message_reasoning(delta)
                if reasoning:
                    reasoning_parts.append(reasoning)
                for tool_delta in getattr(delta, "tool_calls", None) or []:
                    index = int(getattr(tool_delta, "index", 0) or 0)
                    entry = calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
                    if getattr(tool_delta, "id", None):
                        entry["id"] = str(tool_delta.id)
                    function = getattr(tool_delta, "function", None)
                    if function is not None:
                        if getattr(function, "name", None):
                            entry["name"] += str(function.name)
                        if getattr(function, "arguments", None):
                            entry["arguments"] += str(function.arguments)
        return ModelResult(
            text="".join(text_parts),
            tool_calls=[calls[index] for index in sorted(calls)],
            usage=usage,
            provider=self.provider,
            model=str(kwargs["model"]),
            reasoning="".join(reasoning_parts),
            finish_reason=finish,
        )


def _record_response(span: telemetry.SpanHandle, response: Any, usage: ModelUsage) -> None:
    """Response facts on the ``chat`` span; output content only with capture on."""
    telemetry.record_usage(span, usage)
    if not span.recording:
        return
    span.set(telemetry_semconv.GEN_AI_RESPONSE_MODEL, getattr(response, "model", None))
    span.set(telemetry_semconv.GEN_AI_RESPONSE_ID, getattr(response, "id", None))
    try:
        choices = list(getattr(response, "choices", None) or [])
    except TypeError:
        choices = []
    reasons = [str(getattr(c, "finish_reason", "") or "") for c in choices]
    if any(reasons):
        span.set(telemetry_semconv.GEN_AI_RESPONSE_FINISH_REASONS, [r for r in reasons if r])
    if telemetry.content_capture_enabled() and choices:
        message = getattr(choices[0], "message", None)
        calls = [
            {
                "name": str(getattr(getattr(c, "function", None), "name", "") or ""),
                "arguments": str(getattr(getattr(c, "function", None), "arguments", "") or ""),
            }
            for c in (getattr(message, "tool_calls", None) or [])
        ]
        span.content(
            telemetry_semconv.GEN_AI_OUTPUT_MESSAGES,
            [
                {
                    "role": "assistant",
                    "content": str(getattr(message, "content", "") or ""),
                    "tool_calls": calls,
                }
            ],
        )


# --------------------------------------------------------------------------- #
# Tiers and fallback (D-21)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ModelTier:
    provider: str
    model: str

    @property
    def qualified(self) -> str:
        return f"{self.provider}/{self.model}"


def parse_chain(text: str, settings: ProviderSettings | None = None) -> list[ModelTier]:
    """``"nim/nvidia/nemotron-3-ultra-550b-a55b, ollama/gpt-oss:20b"`` → tiers.

    A bare provider (``"ollama"``) takes that provider's default model; an
    unqualified model id is ignored (a tier must name its engine).
    """
    tiers: list[ModelTier] = []
    for item in str(text or "").split(","):
        token = item.strip()
        if not token:
            continue
        if token.lower() in PROVIDERS:
            provider, model = token.lower(), ""
        else:
            provider, model = resolve_provider(token, default="")
            if not provider:
                logger.warning("model chain entry %r names no provider; ignored", token)
                continue
        tiers.append(ModelTier(provider, model or provider_default_model(provider, settings)))
    return tiers


def default_agent_chain(settings: ProviderSettings | None = None) -> list[ModelTier]:
    """D-21 default for agent and self-improvement work: hosted NIM → local Ollama.

    ``LOCUS_AGENT_MODEL_CHAIN`` replaces it; each provider's default model comes
    from settings / ``NIM_MODEL`` / ``OLLAMA_MODEL``.
    """
    configured = parse_chain(str(os.getenv(AGENT_CHAIN_ENV) or ""), settings)
    if configured:
        return configured
    return [
        ModelTier("nim", provider_default_model("nim", settings)),
        ModelTier("ollama", provider_default_model("ollama", settings)),
    ]


def chain_for(
    provider: str,
    model: str,
    *,
    explicit: bool,
    settings: ProviderSettings | None = None,
) -> list[ModelTier]:
    """Tiers for a resolved engine choice.

    * not explicit → :func:`default_agent_chain`;
    * an explicit local engine → just that engine (honoured, no hosted escalation);
    * an explicit hosted engine → that engine, then the local Ollama tier (P13)
      unless ``LOCUS_MODEL_LOCAL_FALLBACK=0``.
    """
    if not explicit:
        return default_agent_chain(settings)
    provider_id = str(provider or "").strip().lower()
    primary = ModelTier(
        provider_id, str(model or "") or provider_default_model(provider_id, settings)
    )
    spec = PROVIDERS.get(provider_id)
    if spec is not None and spec.local:
        return [primary]
    if str(os.getenv(LOCAL_FALLBACK_ENV) or "1").strip().lower() in {"0", "false", "no", "off"}:
        return [primary]
    return [primary, ModelTier("ollama", provider_default_model("ollama", settings))]


#: Codes after which a tier stays disabled for the router's lifetime.
_STICKY_CODES = frozenset({PROVIDER_NOT_CONFIGURED, MODEL_CALL_DENIED})


class ModelRouter:
    """Walks tiers in order; every move to the next tier is a recorded fallback.

    A tier that is not configured or was denied by the gateway is skipped for
    the rest of this router's life (no flapping); a failed call is retried on
    that tier next time. A stream that already emitted text never falls back.
    """

    def __init__(
        self,
        tiers: Sequence[ModelTier],
        *,
        client_factory: Callable[[ModelTier], ModelClient],
        on_fallback: Callable[[FallbackEvent], None] | None = None,
    ) -> None:
        if not tiers:
            raise ValueError("a model router needs at least one tier")
        self.tiers = list(tiers)
        self._factory = client_factory
        self._on_fallback = on_fallback
        self._clients: dict[int, ModelClient] = {}
        self._disabled: set[int] = set()
        self._active = 0
        self.fallbacks: list[FallbackEvent] = []

    @property
    def provider(self) -> str:
        return self.tiers[self._active].provider

    @property
    def model(self) -> str:
        return self.tiers[self._active].model

    def _client(self, index: int) -> ModelClient:
        if index not in self._clients:
            self._clients[index] = self._factory(self.tiers[index])
        return self._clients[index]

    def _emit(self, event: FallbackEvent) -> None:
        self.fallbacks.append(event)
        telemetry.record_fallback(
            from_tier=f"{event.from_provider}/{event.from_model}",
            to_tier=f"{event.to_provider}/{event.to_model}",
            reason_code=event.reason_code,
        )
        logger.warning(
            "model_call.fallback",
            extra={k: v for k, v in event.as_metadata().items() if k != "reason"},
        )
        if self._on_fallback is not None:
            try:
                self._on_fallback(event)
            except Exception:  # noqa: BLE001 - reporting must not break the run
                logger.exception("model_call.fallback_listener_error")

    def _run(self, operation: Callable[[ModelClient], ModelResult]) -> ModelResult:
        errors: list[ModelProviderError] = []
        events: list[FallbackEvent] = []
        candidates = [i for i in range(len(self.tiers)) if i not in self._disabled]
        for position, index in enumerate(candidates):
            tier = self.tiers[index]
            previous = self.tiers[candidates[position - 1]].qualified if position else ""
            try:
                with telemetry.fallback_hop(position, previous):
                    client = self._client(index)
                    result = operation(client)
            except ModelProviderError as exc:
                errors.append(exc)
                if exc.code in _STICKY_CODES:
                    self._disabled.add(index)
                if getattr(exc, "_stream_started", False):
                    raise
                following = candidates[position + 1 :]
                if following:
                    nxt = self.tiers[following[0]]
                    event = FallbackEvent(
                        from_provider=tier.provider,
                        from_model=tier.model,
                        to_provider=nxt.provider,
                        to_model=nxt.model,
                        reason_code=exc.code,
                        reason=exc.reason,
                    )
                    events.append(event)
                    self._emit(event)
                continue
            self._active = index
            result.fallbacks = events
            return result
        if not errors:
            primary = self.tiers[0]
            raise ModelProviderError(
                code=PROVIDER_NOT_CONFIGURED,
                provider=primary.provider,
                model=primary.model,
                reason="every model tier is disabled for this run",
            )
        last = errors[-1]
        summary = "; ".join(f"{e.provider}: {e.code}" for e in errors)
        raise ModelProviderError(
            code=last.code,
            provider=self.tiers[0].provider if len(errors) > 1 else last.provider,
            model=self.tiers[0].model if len(errors) > 1 else last.model,
            reason=f"all model tiers failed ({summary}); last: {last.reason}",
            audit_id=last.audit_id,
        )

    def complete(self, messages: list[dict[str, Any]], **kwargs: Any) -> ModelResult:
        return self._run(lambda client: client.complete(messages, **kwargs))

    def stream(
        self,
        messages: list[dict[str, Any]],
        *,
        on_chunk: Callable[[str], None] | None = None,
        **kwargs: Any,
    ) -> ModelResult:
        def operation(client: ModelClient) -> ModelResult:
            started = {"value": False}

            def relay(piece: str) -> None:
                started["value"] = True
                if on_chunk is not None:
                    on_chunk(piece)

            try:
                return client.stream(messages, on_chunk=relay, **kwargs)
            except ModelProviderError as exc:
                exc._stream_started = started["value"]  # type: ignore[attr-defined]
                raise

        return self._run(operation)


def build_client(
    tier: ModelTier,
    *,
    settings: ProviderSettings | None = None,
    keys: ProviderKeyStore | None = None,
    gate: ModelCallGate | None = None,
    http_client: Any = None,
    run_id: str = "",
    timeout: float = 600.0,
) -> ModelClient:
    """Resolve ``tier`` and return a gated client (raises ``provider_not_configured``)."""
    endpoint = resolve_endpoint(tier.provider, tier.model, settings=settings, keys=keys)
    return ModelClient(endpoint, gate=gate, http_client=http_client, run_id=run_id, timeout=timeout)
