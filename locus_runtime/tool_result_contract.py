"""Deterministic deny-first contract for bounded, typed local tool results.

The executor still discards stdout/stderr. This independent boundary is for a
future trusted, output-limited collector, NOT a path to release arbitrary agent
stdout or model-generated data. Only a small immutable structured vocabulary
can pass; raw diagnostic content is rejected and never persisted by this module.
A content digest is evidence of normalization, NOT remote attestation.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Literal

MAX_RESULT_BYTES = 65536
MAX_COUNTER = 1_000_000_000_000
_MAX_METRICS = 32
_METRIC_NAME = re.compile(r"[a-z][a-z0-9_.-]{0,47}\Z", re.ASCII)
Schema = Literal["status/v1", "counter/v1"]


class ToolResultRejected(ValueError):
    """Untrusted result did not satisfy the administrator's typed contract."""


@dataclass(frozen=True, slots=True)
class ToolResultPolicy:
    """Pinned by a trusted supervisor; a model cannot modify this contract."""

    schema: Schema
    allowed_metrics: frozenset[str] = frozenset()
    max_bytes: int = MAX_RESULT_BYTES

    def __post_init__(self) -> None:
        if self.schema not in ("status/v1", "counter/v1"):
            raise ValueError("unsupported result schema")
        if type(self.max_bytes) is not int or not 1 <= self.max_bytes <= MAX_RESULT_BYTES:
            raise ValueError("result byte ceiling is invalid")
        if not isinstance(self.allowed_metrics, frozenset):
            raise ValueError("metric names must be a frozen allowlist")
        if len(self.allowed_metrics) > _MAX_METRICS or any(
            not isinstance(name, str) or not _METRIC_NAME.fullmatch(name)
            for name in self.allowed_metrics
        ):
            raise ValueError("invalid counter metric allowlist")
        if self.schema == "counter/v1" and not self.allowed_metrics:
            raise ValueError("counter schema requires an explicit metric allowlist")
        if self.schema == "status/v1" and self.allowed_metrics:
            raise ValueError("status schema cannot authorize arbitrary metric names")


@dataclass(frozen=True, slots=True)
class StatusResult:
    ok: bool
    code: int
    digest: str
    schema: Literal["status/v1"] = "status/v1"


@dataclass(frozen=True, slots=True)
class CounterResult:
    metric: str
    value: int
    digest: str
    schema: Literal["counter/v1"] = "counter/v1"


def _reject_duplicate_fields(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ToolResultRejected("duplicate JSON fields are forbidden")
        value[key] = item
    return value


def _reject_non_integer(_: str) -> object:
    raise ToolResultRejected("non-integer numeric values are forbidden")


def _digest(clean: dict[str, object]) -> str:
    # The digest includes its schema and a domain separator. Never digest raw
    # stdout in place of validating the content first.
    canonical = json.dumps(
        clean, sort_keys=True, ensure_ascii=True,
        separators=(",", ":"), allow_nan=False,
    ).encode("ascii")
    return hashlib.sha256(b"locus.typed-tool-result.v1\x00" + canonical).hexdigest()


def decode_tool_result(
    raw: bytes, policy: ToolResultPolicy,
) -> StatusResult | CounterResult:
    """Admit only schema-exact, non-secret-bearing structured tool output.

    The exception path deliberately excludes any untrusted source bytes or
    JSON parser messages that may contain the source text.
    """
    if not isinstance(policy, ToolResultPolicy):
        raise ToolResultRejected("trusted result policy required")
    if type(raw) is not bytes or not raw or len(raw) > policy.max_bytes:
        raise ToolResultRejected("result input exceeds its admitted binary envelope")
    try:
        text = raw.decode("utf-8", errors="strict")
        root = json.loads(
            text, object_pairs_hook=_reject_duplicate_fields,
            parse_float=_reject_non_integer,
            parse_constant=_reject_non_integer,
        )
    except (UnicodeError, ValueError, TypeError, OverflowError, RecursionError) as exc:
        raise ToolResultRejected("malformed structured tool result") from None
    if type(root) is not dict or root.get("schema") != policy.schema:
        raise ToolResultRejected("result schema is not authorized")

    if policy.schema == "status/v1":
        if set(root) != {"schema", "ok", "code"}:
            raise ToolResultRejected("unexpected status result fields")
        ok, code = root["ok"], root["code"]
        if (type(ok) is not bool or type(code) is not int
            or not 0 <= code <= 255 or (code == 0) != ok):
            raise ToolResultRejected("invalid status result values")
        clean: dict[str, object] = {
            "schema": "status/v1", "ok": ok, "code": code,
        }
        return StatusResult(ok=ok, code=code, digest=_digest(clean))

    if set(root) != {"schema", "metric", "value"}:
        raise ToolResultRejected("unexpected counter result fields")
    metric, value = root["metric"], root["value"]
    if (type(metric) is not str or metric not in policy.allowed_metrics
        or type(value) is not int or not 0 <= value <= MAX_COUNTER):
        raise ToolResultRejected("invalid counter result values")
    counter: dict[str, object] = {
        "schema": "counter/v1", "metric": metric, "value": value,
    }
    return CounterResult(metric=metric, value=value, digest=_digest(counter))
