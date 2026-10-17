"""Server-owned, read-only TaskPlan preview adapter (LOCUS-461).

This module never approves a plan, calls the Gateway, grants tools, stores
review receipts, or dispatches work. The registry file is installed by a
privileged server operator and is not selectable or editable by API clients.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from locus_runtime.task_graph_contract import InvalidTaskPlan, TaskAuthority, TaskBudget, TaskIntent
from locus_runtime.task_plan_compiler import MAX_PROPOSAL_BYTES
from locus_runtime.task_plan_review import (
    InvalidPlanReviewContext,
    TrustedReviewContext,
    build_plan_review,
)

REGISTRY_SCHEMA = "locus.task-plan-context-registry/1.0"
MAX_REGISTRY_BYTES = 131_072
MAX_BODY_BYTES = MAX_PROPOSAL_BYTES
_ALLOWED_REQUEST_KEYS = frozenset({"project_id", "goal", "acceptance_criteria", "proposal"})
_ALLOWED_REGISTRY_KEYS = frozenset({"schema_version", "projects"})
_ALLOWED_PROJECT_KEYS = frozenset({
    "tenant_id", "project_id", "allowed_principals", "area",
    "classification_floor", "effective_classification",
    "capabilities", "risk_ceiling", "budget", "policy_epoch", "revision",
})
_BUDGET_FIELDS = frozenset({"actions", "tokens", "wall_seconds", "spend_microunits"})
_ALLOWED_LOGIN_KINDS = frozenset({"jwt", "oidc", "bootstrap"})


class PlanPreviewUnavailable(ValueError):
    """Stable external error code; never return raw registry/config contents."""

    def __init__(self, code: str, *, status_code: int = 503) -> None:
        self.code = code
        self.status_code = status_code
        super().__init__(code)


def _duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            raise PlanPreviewUnavailable("duplicate_json_key", status_code=400)
        output[key] = value
    return output


def _no_special(value: str) -> None:
    raise PlanPreviewUnavailable("invalid_json_constant", status_code=400)


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("utf-8")


def _bounded_text(value: Any, *, label: str, limit: int) -> str:
    if (
        not isinstance(value, str)
        or value != value.strip()
        or not value
        or len(value) > limit
        or any(ord(c) < 32 or ord(c) == 127 for c in value)
    ):
        raise PlanPreviewUnavailable(f"invalid_{label}", status_code=400)
    return value


def parse_preview_request(body: bytes) -> dict[str, Any]:
    """Reject duplicate keys and nonfinite floats before proposal compilation."""
    if not isinstance(body, bytes) or not body or len(body) > MAX_BODY_BYTES:
        raise PlanPreviewUnavailable("invalid_or_oversized_body", status_code=413)
    try:
        payload = json.loads(
            body.decode("utf-8"), object_pairs_hook=_duplicate_keys,
            parse_constant=_no_special,
        )
    except PlanPreviewUnavailable:
        raise
    except (UnicodeError, ValueError, TypeError, RecursionError) as exc:
        raise PlanPreviewUnavailable("invalid_json", status_code=400) from exc
    if not isinstance(payload, dict) or set(payload) != _ALLOWED_REQUEST_KEYS:
        raise PlanPreviewUnavailable("unexpected_request_fields", status_code=400)
    _bounded_text(payload["project_id"], label="project_id", limit=64)
    _bounded_text(payload["goal"], label="goal", limit=4096)
    criteria = payload["acceptance_criteria"]
    if not isinstance(criteria, list) or not 1 <= len(criteria) <= 20:
        raise PlanPreviewUnavailable("invalid_acceptance_criteria", status_code=400)
    for item in criteria:
        _bounded_text(item, label="criterion", limit=1024)
    if not isinstance(payload["proposal"], dict):
        raise PlanPreviewUnavailable("invalid_proposal", status_code=400)
    return payload


def _strict_config_project(row: Any) -> dict[str, Any]:
    if not isinstance(row, dict) or set(row) != _ALLOWED_PROJECT_KEYS:
        raise PlanPreviewUnavailable("invalid_server_project_registry")
    members = row["allowed_principals"]
    if (
        not isinstance(members, list) or not members
        or any(not isinstance(s, str) or not s or len(s) > 255 for s in members)
        or len(members) != len(set(members))
    ):
        raise PlanPreviewUnavailable("invalid_server_memberships")
    try:
        TaskAuthority(
            capabilities=frozenset(row["capabilities"]),
            risk_ceiling=row["risk_ceiling"],
            budget=TaskBudget(**row["budget"]),
        )
    except (TypeError, ValueError, InvalidTaskPlan) as exc:
        raise PlanPreviewUnavailable("invalid_server_envelope") from exc
    if (
        not isinstance(row["capabilities"], list)
        or not row["capabilities"]
        or any(not isinstance(c, str) or not c.strip() for c in row["capabilities"])
        or len(row["capabilities"]) != len(set(row["capabilities"]))
    ):
        raise PlanPreviewUnavailable("invalid_server_capabilities")
    if not isinstance(row["budget"], dict) or set(row["budget"]) != _BUDGET_FIELDS:
        raise PlanPreviewUnavailable("invalid_server_budget")
    for key in ("tenant_id", "project_id", "area", "classification_floor", "effective_classification"):
        _bounded_text(row[key], label=f"server_{key}", limit=128)
    if (
        type(row["policy_epoch"]) is not int or row["policy_epoch"] < 1
        or type(row["revision"]) is not int or row["revision"] < 1
    ):
        raise PlanPreviewUnavailable("invalid_server_epoch")
    return row


def load_project_registry(path: str) -> tuple[dict[str, Any], ...]:
    """Read only from server-selected path; deny missing or untrusted registry."""
    if not isinstance(path, str) or not path.strip():
        raise PlanPreviewUnavailable("project_registry_unavailable")
    source = Path(path)
    try:
        if source.is_symlink() or not source.is_file():
            raise PlanPreviewUnavailable("project_registry_unavailable")
        metadata = source.stat()
        if metadata.st_size <= 0 or metadata.st_size > MAX_REGISTRY_BYTES:
            raise PlanPreviewUnavailable("project_registry_unavailable")
        if os.name == "posix" and metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise PlanPreviewUnavailable("unsafe_registry_permissions")
        payload = json.loads(source.read_text(encoding="utf-8"), object_pairs_hook=_duplicate_keys)
    except PlanPreviewUnavailable:
        raise
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        raise PlanPreviewUnavailable("project_registry_unavailable") from exc
    if not isinstance(payload, dict) or set(payload) != _ALLOWED_REGISTRY_KEYS:
        raise PlanPreviewUnavailable("invalid_server_registry")
    if payload["schema_version"] != REGISTRY_SCHEMA or not isinstance(payload["projects"], list):
        raise PlanPreviewUnavailable("invalid_server_registry")
    if len(payload["projects"]) > 1024:
        raise PlanPreviewUnavailable("invalid_server_registry")
    rows: list[dict[str, Any]] = []
    known: set[tuple[str, str]] = set()
    for entry in payload["projects"]:
        item = _strict_config_project(entry)
        key = (item["tenant_id"], item["project_id"])
        if key in known:
            raise PlanPreviewUnavailable("duplicate_server_project")
        known.add(key)
        rows.append(item)
    return tuple(rows)


def _trusted_actor(auth_context: Mapping[str, Any]) -> tuple[str, str]:
    """Use only the identity attested by the existing backend authn boundary."""
    if (
        not isinstance(auth_context, Mapping)
        or auth_context.get("authenticated") is not True
        or auth_context.get("used_bearer_token") is not True
        or auth_context.get("bearer_auth_kind") not in _ALLOWED_LOGIN_KINDS
        or auth_context.get("principal_type") != "user"
    ):
        raise PlanPreviewUnavailable("signed_user_session_required", status_code=403)
    actor = auth_context.get("principal_id")
    if not isinstance(actor, str) or not actor or len(actor) > 255:
        raise PlanPreviewUnavailable("missing_trusted_principal", status_code=403)
    tenant = auth_context.get("tenant")
    if auth_context.get("bearer_auth_kind") == "bootstrap":
        if actor != "user:local/operator":
            raise PlanPreviewUnavailable("invalid_local_bootstrap", status_code=403)
        tenant = "local"
    if not isinstance(tenant, str) or not tenant or len(tenant) > 128:
        raise PlanPreviewUnavailable("authenticated_tenant_required", status_code=403)
    return actor, tenant


def build_authenticated_preview(
    body: bytes, *, auth_context: Mapping[str, Any],
    registry_path: str, catalog_versions: Mapping[str, tuple[int, str]],
    policy_snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    """Generate a structural preview from an authenticated server context.

    Caller must first invoke _enforce_request_authn(required=True), and supply
    the resulting trusted request.state context, never parsed client claims.
    The policy_snapshot is metadata only, not a PDP authorization verdict.
    """
    actor, tenant = _trusted_actor(auth_context)
    payload = parse_preview_request(body)
    project_id = payload["project_id"]
    selected = None
    for candidate in load_project_registry(registry_path):
        if candidate["tenant_id"] == tenant and candidate["project_id"] == project_id:
            selected = candidate
            break
    if selected is None or actor not in selected["allowed_principals"]:
        # Identical error for absent, foreign and unauthorized project IDs.
        raise PlanPreviewUnavailable("project_not_found", status_code=404)
    project = selected
    try:
        intent = TaskIntent(
            goal=payload["goal"], area=project["area"],
            requester_id=actor,
            acceptance_criteria=tuple(payload["acceptance_criteria"]),
        )
        authority = TaskAuthority(
            frozenset(project["capabilities"]),
            project["risk_ceiling"],
            TaskBudget(**project["budget"]),
        )
        context = TrustedReviewContext(
            intent=intent,
            parent_authority=authority,
            actor_id=actor,
            tenant_id=tenant,
            project_id=project_id,
            project_classification_floor=project["classification_floor"],
            effective_classification=project["effective_classification"],
            policy_digest=hashlib.sha256(_canonical({
                "project": project,
                "policy_snapshot": policy_snapshot,
            })).hexdigest(),
            catalog_digest=hashlib.sha256(_canonical(
                sorted(
                    (name, version, status)
                    for name, (version, status) in catalog_versions.items()
                )
            )).hexdigest(),
            policy_epoch=project["policy_epoch"],
            revision=project["revision"],
        )
        review = build_plan_review(payload["proposal"], context=context).to_dict()
    except (InvalidTaskPlan, InvalidPlanReviewContext, TypeError, ValueError) as exc:
        raise PlanPreviewUnavailable("invalid_plan_or_context", status_code=400) from exc
    return review
