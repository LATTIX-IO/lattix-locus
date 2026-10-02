from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Any

from locus_runtime.envelope import Envelope
from locus_runtime.security import (
    CapabilityEvaluationRequest,
    CapabilityVerifier,
    build_default_keypair,
)


@dataclass(frozen=True)
class FilterContext:
    classification: str = "internal"


@dataclass
class FilterResult:
    action: str
    envelope: Envelope
    reason: str = ""


class DLPFilter:
    async def evaluate(self, envelope: Envelope, context: FilterContext) -> FilterResult:
        mutated = copy.deepcopy(envelope)
        mutated.payload, findings = _redact_payload_value(mutated.payload)
        if findings:
            restricted_findings = {"api_key", "bearer_token", "credit_card", "private_key", "ssn"}
            mutated.metadata["classification"] = (
                "restricted" if restricted_findings.intersection(findings) else "confidential"
            )
            mutated.metadata["dlp_findings"] = findings
            return FilterResult(action="modify", envelope=mutated)
        mutated.metadata.setdefault("classification", context.classification)
        return FilterResult(action="pass", envelope=mutated)


class PromptRenderFilter:
    async def evaluate(self, envelope: Envelope, context: FilterContext) -> FilterResult:
        mutated = copy.deepcopy(envelope)
        mutated.payload["locus_policy_context"] = {"classification": context.classification}
        return FilterResult(action="modify", envelope=mutated)


class _DefaultFilterChain:
    async def run(self, envelope: Envelope, context: FilterContext) -> FilterResult:
        if _requires_capability_enforcement(envelope):
            if not envelope.target_agent:
                return FilterResult(
                    action="block",
                    envelope=envelope,
                    reason="target agent required for capability-scoped action",
                )
            token = envelope.capability_token
            if not token:
                return FilterResult(
                    action="block", envelope=envelope, reason="capability token required"
                )
            verifier = CapabilityVerifier(build_default_keypair())
            metadata = envelope.metadata if isinstance(envelope.metadata, dict) else {}
            capability_request = CapabilityEvaluationRequest(
                action=envelope.action,
                agent_id=envelope.target_agent,
                tool_call_count=_safe_int(metadata.get("tool_call_count")),
                resource_path=str(
                    metadata.get("resource_path") or metadata.get("path") or ""
                ).strip()
                or None,
            )
            if not verifier.verify_request(token, capability_request):
                return FilterResult(
                    action="block", envelope=envelope, reason="invalid capability token"
                )
        return FilterResult(action="pass", envelope=envelope)


def default_filter_chain() -> _DefaultFilterChain:
    return _DefaultFilterChain()


def _safe_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _requires_capability_enforcement(envelope: Envelope) -> bool:
    action = str(envelope.action or "").strip().lower()
    guarded_actions = {
        "execute_step",
        "read_file",
        "write_file",
        "network_egress",
        "llm_call",
    }
    if envelope.target_agent or envelope.capability_token:
        return True
    if action in guarded_actions:
        return True
    metadata = envelope.metadata if isinstance(envelope.metadata, dict) else {}
    return any(key in metadata for key in ("tool_call_count", "resource_path", "path"))


def _redact_sensitive_text(text: str) -> tuple[str, list[str]]:
    redacted = text
    findings: list[str] = []
    patterns = [
        ("email", r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", "[REDACTED_EMAIL]"),
        ("phone", r"\b(?:\+?\d{1,2}\s*)?(?:\(?\d{3}\)?[-.\s]?){2}\d{4}\b", "[REDACTED_PHONE]"),
        ("credit_card", r"\b(?:\d[ -]*?){13,16}\b", "[REDACTED_CREDIT_CARD]"),
        ("ssn", r"\b\d{3}-?\d{2}-?\d{4}\b", "[REDACTED_SSN]"),
        (
            "api_key",
            r"\b(?:sk-[A-Za-z0-9_-]{10,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16})\b",
            "[REDACTED_API_KEY]",
        ),
        ("api_key", r"(?i)(api[_-]?key\s*[=:]\s*)([^\s,;]+)", r"\1[REDACTED_API_KEY]"),
        ("bearer_token", r"(?i)(bearer\s+)[A-Za-z0-9._\-]+", r"\1[REDACTED_TOKEN]"),
        ("password", r"(?i)(password\s*[=:]\s*)([^\s,;]+)", r"\1[REDACTED_PASSWORD]"),
        (
            "private_key",
            r"-----BEGIN[^-]*PRIVATE KEY-----.*?-----END[^-]*PRIVATE KEY-----",
            "[REDACTED_PRIVATE_KEY]",
        ),
    ]
    for finding, pattern, replacement in patterns:
        updated = re.sub(pattern, replacement, redacted, flags=re.DOTALL)
        if updated != redacted:
            if finding not in findings:
                findings.append(finding)
            redacted = updated
    return redacted, findings


def _redact_payload_value(value: Any) -> tuple[Any, list[str]]:
    if isinstance(value, str):
        return _redact_sensitive_text(value)
    if isinstance(value, dict):
        dict_findings: list[str] = []
        redacted_map: dict[str, Any] = {}
        for key, item in value.items():
            redacted_item, item_findings = _redact_payload_value(item)
            redacted_map[key] = redacted_item
            for finding in item_findings:
                if finding not in dict_findings:
                    dict_findings.append(finding)
        return redacted_map, dict_findings
    if isinstance(value, list):
        list_findings: list[str] = []
        redacted_items: list[Any] = []
        for item in value:
            redacted_item, item_findings = _redact_payload_value(item)
            redacted_items.append(redacted_item)
            for finding in item_findings:
                if finding not in list_findings:
                    list_findings.append(finding)
        return redacted_items, list_findings
    return value, []
