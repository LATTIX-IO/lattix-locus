"""LOCUS-357: on the desktop, capability-widening endpoints need the shell's proof.

Each protected endpoint is exercised on the desktop profile (``local-native``
with the local-operator bootstrap, the real desktop auth path): refused and
audited without a proof, accepted with a valid request-bound proof, the proof
refused on replay, and the narrowing variant accepted without any proof.
Other profiles keep principal auth only.
"""

from __future__ import annotations

import json
import os
import secrets
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if not str(os.environ.get("A2A_JWT_SECRET") or "").strip():
    os.environ["A2A_JWT_SECRET"] = "unit-test-super-secret-value-32bytes"
if not str(os.environ.get("LOCUS_API_BEARER_TOKEN") or "").strip():
    os.environ["LOCUS_API_BEARER_TOKEN"] = "unit-test-bearer"

import app.main as main_module
from app.main import app, store
from app.request_security import (
    CapabilityEffect,
    ShellProofFormat,
    classify_shell_proof,
    shell_proof_rules,
    validate_shell_proof_inventory,
)

from locus_runtime.computer_use import controller as cu
from locus_tooling import shell_confirmation as sc

client = TestClient(app)
SHELL_SECRET = bytes(range(40, 72))
PRINCIPAL = {
    "Authorization": f"Bearer {os.environ['LOCUS_API_BEARER_TOKEN']}",
    "x-locus-actor": "locus-admin",
}


@pytest.fixture()
def desktop(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("LOCUS_RUNTIME_PROFILE", "local-native")
    monkeypatch.setenv("LOCUS_LOCAL_BOOTSTRAP_AUTHENTICATED_OPERATOR", "true")
    sc.install_secret(SHELL_SECRET)
    try:
        yield
    finally:
        sc.install_secret(None)


@pytest.fixture(autouse=True)
def restore_state() -> Iterator[None]:
    names = (
        "user_settings",
        "user_skills",
        "workflow_schedules",
        "workflow_triggers",
        "workflow_definitions",
        "guardrail_rulesets",
        "skills",
        "integrations",
        "user_runtime_provider_configs",
    )
    saved = {name: dict(getattr(store, name)) for name in names}
    settings = store.platform_settings.model_copy(deep=True)
    controller = (cu._DEFAULT, cu._INSTALLED)  # noqa: SLF001
    try:
        yield
    finally:
        for name, value in saved.items():
            setattr(store, name, value)
        store.platform_settings = settings
        cu._DEFAULT, cu._INSTALLED = controller  # noqa: SLF001


# --------------------------------------------------------------------------- #
# What the Tauri shell does: canonical body, request-bound proof, own request
# --------------------------------------------------------------------------- #
def _encode(body: dict[str, Any] | None) -> bytes:
    return b"" if body is None else sc.canonical_body(json.dumps(body)).encode("utf-8")


def shell_proof(
    method: str,
    path: str,
    body: dict[str, Any] | None,
    *,
    action: str | None = None,
    key: bytes = SHELL_SECRET,
    ts: int | None = None,
) -> str:
    rule = classify_shell_proof(method, path)
    assert rule is not None and rule.may_need_proof, (method, path)
    digest = sc.request_digest(method, path, _encode(body))
    return sc.proof_header(
        key,
        lambda nonce, stamp: sc.action_message(
            action=action or rule.action, digest=digest, nonce=nonce, timestamp=stamp
        ),
        nonce=secrets.token_hex(16),
        timestamp=int(time.time()) if ts is None else ts,
    )


def call(
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    proof: str | None = None,
    headers: dict[str, str] | None = None,
) -> Any:
    sent = {"Content-Type": "application/json", **(headers or {})}
    if proof is not None:
        sent[sc.PROOF_HEADER] = proof
    return client.request(method, path, content=_encode(body), headers=sent)


def last_audit(action: str) -> Any:
    return next(event for event in store.audit_events if event.action == action)


def assert_refused(response: Any, action: str, reason: str = "missing_proof") -> None:
    assert response.status_code == 403, response.text
    assert reason in response.text
    event = last_audit(action)
    assert event.outcome == "blocked" and event.metadata.get("reason") == reason


# --------------------------------------------------------------------------- #
# Fixtures for the protected state
# --------------------------------------------------------------------------- #
def _folder_escalation(run_id: str) -> str:
    store.runs[run_id] = main_module.WorkflowRunSummary(
        id=run_id, title="t", status="Running", updatedAt="now", progressLabel="running"
    )
    store.run_details[run_id] = {
        "access": {"actor": "operator"},
        "escalations": [
            {
                "id": "esc-1",
                "kind": "folder",
                "status": "pending",
                "path": "/projects/other",
                "workspace_root": f"/projects/{run_id}",
            }
        ],
    }
    return f"/workflow-runs/{run_id}/escalations/esc-1/approve"


def _workflow_id() -> str:
    workflow = main_module.WorkflowDefinition(
        id=f"wf-{secrets.token_hex(4)}", name="wf", description="", version=1, status="draft"
    )
    store.workflow_definitions[workflow.id] = workflow
    return workflow.id


# --------------------------------------------------------------------------- #
# 1. Escalation approval (highest priority)
# --------------------------------------------------------------------------- #
def test_escalation_approval_needs_the_proof_and_is_single_use(desktop: None) -> None:
    path = _folder_escalation("run-357-esc")
    refused = call("POST", path, {})
    assert_refused(refused, "workflow.run.escalations.approve")
    assert store.run_details["run-357-esc"]["escalations"][0]["status"] == "pending"

    proof = shell_proof("POST", path, {})
    done = call("POST", path, {}, proof)
    assert done.status_code == 200, done.text
    assert done.json()["escalation"]["status"] == "approved"

    assert_refused(
        call("POST", path, {}, proof), "workflow.run.escalations.approve", "replayed_proof"
    )


@pytest.mark.parametrize(
    "kind", ["other_body", "other_path", "other_action", "wrong_key", "expired"]
)
def test_escalation_proof_is_bound_to_the_exact_request(desktop: None, kind: str) -> None:
    path = _folder_escalation("run-357-bound")
    body: dict[str, Any] = {"scope": "once"}
    if kind == "other_body":
        proof = shell_proof("POST", path, {"scope": "standing"})
    elif kind == "other_path":
        proof = shell_proof("POST", _folder_escalation("run-357-other"), body)
    elif kind == "other_action":
        proof = shell_proof("POST", path, body, action="computer_use.reset")
    elif kind == "wrong_key":
        proof = shell_proof("POST", path, body, key=b"\x01" * 32)
    else:
        proof = shell_proof("POST", path, body, ts=int(time.time()) - 600)
    response = call("POST", path, body, proof)
    assert response.status_code == 403, response.text
    assert store.run_details["run-357-bound"]["escalations"][0]["status"] == "pending"


def test_a_local_process_without_credentials_cannot_approve(desktop: None) -> None:
    """The threat: on the desktop every loopback caller is the operator."""
    path = _folder_escalation("run-357-anon")
    assert call("POST", path, {}).status_code == 403
    assert call("POST", path, {}, proof="v1:1:" + "a" * 32 + ":" + "b" * 64).status_code == 403
    assert store.run_details["run-357-anon"]["escalations"][0]["status"] == "pending"


def test_revoking_a_grant_never_needs_the_proof(desktop: None) -> None:
    response = call("POST", "/gateway/grants/no-such-grant/revoke", {})
    assert response.status_code == 404, response.text  # past the proof check


# --------------------------------------------------------------------------- #
# 2. Run approvals: approve needs the proof, requesting changes does not
# --------------------------------------------------------------------------- #
def test_run_approval_needs_the_proof_but_requesting_changes_does_not(desktop: None) -> None:
    run_id = "run-357-approval"
    store.runs[run_id] = main_module.WorkflowRunSummary(
        id=run_id, title="t", status="Needs Review", updatedAt="now", progressLabel="review"
    )
    store.run_details[run_id] = {"access": {"actor": "operator"}, "approvals": {"pending": True}}
    approve = {"run_id": run_id, "decision": "approved"}
    assert_refused(call("POST", "/approvals", approve), "approval.submit")
    assert store.run_details[run_id]["approvals"]["pending"] is True

    changes = call("POST", "/approvals", {"run_id": run_id, "decision": "changes_requested"})
    assert changes.status_code == 200, changes.text

    proof = shell_proof("POST", "/approvals", approve)
    assert call("POST", "/approvals", approve, proof).status_code == 200
    assert store.run_details[run_id]["status"] == "Done"
    assert_refused(call("POST", "/approvals", approve, proof), "approval.submit", "replayed_proof")


# --------------------------------------------------------------------------- #
# 3. Computer use: reset needs the proof, panic never does
# --------------------------------------------------------------------------- #
def test_computer_use_reset_needs_the_proof_but_panic_does_not(desktop: None) -> None:
    controller = cu.ComputerUseController("takeover")
    cu.install_controller(controller)
    assert call("POST", "/computer-use/panic").status_code == 200
    assert controller.panicked

    assert_refused(call("POST", "/computer-use/reset"), "computer_use.reset")
    assert controller.panicked

    proof = shell_proof("POST", "/computer-use/reset", None)
    assert call("POST", "/computer-use/reset", None, proof).status_code == 200
    assert not controller.panicked
    assert call("POST", "/computer-use/panic").status_code == 200
    assert_refused(
        call("POST", "/computer-use/reset", None, proof), "computer_use.reset", "replayed_proof"
    )
    assert controller.panicked


# --------------------------------------------------------------------------- #
# 4. Platform settings: widening needs the proof, narrowing does not
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "change",
    [
        {"allowed_egress_hosts": ["localhost", "127.0.0.1", "::1", "exfil.example.com"]},
        {"require_human_approval_for_high_risk_tools": False},
        {"max_tool_calls_per_run": 500},
        {"foss_guardrail_signal_enforcement": "audit"},
        {"allow_runtime_engine_override": True},
        {"openai_api_key": "sk-test-key"},
    ],
)
def test_widening_platform_settings_needs_the_proof(
    desktop: None, in_memory_keychain: Any, change: dict[str, Any]
) -> None:
    original = store.platform_settings.model_copy(deep=True)
    before = original.model_dump()
    assert_refused(call("POST", "/platform/settings", change), "platform.settings.save")
    assert store.platform_settings.model_dump() == before
    assert not in_memory_keychain.store

    proof = shell_proof("POST", "/platform/settings", change)
    done = call("POST", "/platform/settings", change, proof)
    assert done.status_code == 200, done.text
    assert store.platform_settings.model_dump() != before or in_memory_keychain.store
    # Back to the narrower settings: replaying the proof must not widen again.
    store.platform_settings = original
    assert_refused(
        call("POST", "/platform/settings", change, proof),
        "platform.settings.save",
        "replayed_proof",
    )


@pytest.mark.parametrize(
    "change",
    [
        {"allowed_egress_hosts": ["localhost"]},
        {"require_human_approval": True},
        {"max_tool_calls_per_run": 5},
        {"global_blocked_keywords": ["rm -rf"]},
        {"org_name": "Renamed"},
        {"openai_api_key": "__clear__"},
    ],
)
def test_narrowing_or_neutral_platform_settings_need_no_proof(
    desktop: None, in_memory_keychain: Any, change: dict[str, Any]
) -> None:
    response = call("POST", "/platform/settings", change)
    assert response.status_code == 200, response.text


# --------------------------------------------------------------------------- #
# 5. User settings: only a higher default chat mode widens
# --------------------------------------------------------------------------- #
def test_user_settings_only_a_higher_default_mode_needs_the_proof(desktop: None) -> None:
    assert call("PUT", "/user/settings", {"default_mode": "chat"}).status_code == 200
    neutral = {"default_mode": "chat", "preferred_model": "m", "default_working_folder": "x"}
    assert call("PUT", "/user/settings", neutral).status_code == 200

    widen = {"default_mode": "execute"}
    assert_refused(call("PUT", "/user/settings", widen), "user.settings.save")
    assert store.user_settings["operator"]["default_mode"] == "chat"
    proof = shell_proof("PUT", "/user/settings", widen)
    assert call("PUT", "/user/settings", widen, proof).status_code == 200
    assert store.user_settings["operator"]["default_mode"] == "execute"
    assert call("PUT", "/user/settings", {"default_mode": "plan"}).status_code == 200  # narrows


# --------------------------------------------------------------------------- #
# 6. Guardrails: a draft needs none; anything touching the active rules does
# --------------------------------------------------------------------------- #
def test_guardrail_draft_is_free_but_publish_archive_delete_need_the_proof(desktop: None) -> None:
    saved = call("POST", "/guardrail-rulesets", {"name": "g", "config_json": {}})
    assert saved.status_code == 200, saved.text
    item = saved.json()["id"]
    for method, path, action in (
        ("POST", f"/guardrail-rulesets/{item}/publish", "guardrail.ruleset.publish"),
        ("POST", f"/guardrail-rulesets/{item}/activate", "guardrail.ruleset.activate"),
        ("POST", f"/guardrail-rulesets/{item}/rollback", "guardrail.ruleset.rollback"),
        ("POST", f"/guardrail-rulesets/{item}/archive", "guardrail.ruleset.archive"),
        ("DELETE", f"/guardrail-rulesets/{item}", "guardrail.ruleset.delete"),
    ):
        assert_refused(call(method, path), action)
    assert store.guardrail_rulesets[item].status == "draft"

    publish = f"/guardrail-rulesets/{item}/publish"
    assert call("POST", publish, None, shell_proof("POST", publish, None)).status_code == 200
    assert store.guardrail_rulesets[item].status == "published"
    delete = f"/guardrail-rulesets/{item}"
    assert call("DELETE", delete, None, shell_proof("DELETE", delete, None)).status_code == 200
    assert item not in store.guardrail_rulesets


# --------------------------------------------------------------------------- #
# 7-10. MCP / integrations, skills, provider keys, triggers and schedules
# --------------------------------------------------------------------------- #
WIDENING_REQUESTS: list[tuple[str, str, dict[str, Any] | None]] = [
    ("POST", "/integrations", {"name": "svc", "type": "http", "base_url": "http://localhost:9"}),
    ("POST", "/integrations/mcp", {"name": "m", "starter_id": "none"}),
    ("POST", "/integrations/mcp/conn-1/approve", None),
    ("POST", "/integrations/int-1/oauth/connect", {}),
    ("POST", "/integrations/catalog/cat-1/install", None),
    ("POST", "/skills/import", {"content": "---\nname: x\n---\nbody"}),
    ("POST", "/skills/skill-1/promote", {}),
    ("PUT", "/runtime/user-providers/openai", {"model": "gpt-x", "api_key": "sk-test"}),
    ("PUT", "/models/providers/openai/key", {"api_key": "sk-test"}),
]


@pytest.mark.parametrize(("method", "path", "body"), WIDENING_REQUESTS)
def test_widening_requests_are_refused_without_and_pass_with_a_proof(
    desktop: None, in_memory_keychain: Any, method: str, path: str, body: dict[str, Any] | None
) -> None:
    rule = classify_shell_proof(method, path)
    assert rule is not None and rule.effect == CapabilityEffect.WIDENING
    assert_refused(call(method, path, body), rule.action)
    proof = shell_proof(method, path, body)
    response = call(method, path, body, proof)
    # Past the proof check: the handler answers (it may still refuse on its own).
    assert response.status_code != 403 or "desktop app" not in response.text, response.text
    assert_refused(call(method, path, body, proof), rule.action, "replayed_proof")


NARROWING_REQUESTS: list[tuple[str, str]] = [
    ("DELETE", "/integrations/int-1"),
    ("POST", "/integrations/int-1/oauth/disconnect"),
    ("POST", "/skills/skill-1/revoke"),
    ("DELETE", "/skills/skill-1"),
    ("DELETE", "/runtime/user-providers/openai"),
    ("DELETE", "/models/providers/openai/key"),
    ("DELETE", "/triggers/no-such-token"),
    ("DELETE", "/schedules/no-such-schedule"),
]


@pytest.mark.parametrize(("method", "path"), NARROWING_REQUESTS)
def test_narrowing_requests_never_need_a_proof(
    desktop: None, in_memory_keychain: Any, method: str, path: str
) -> None:
    rule = classify_shell_proof(method, path)
    assert rule is not None and rule.effect == CapabilityEffect.NARROWING
    response = call(method, path)
    assert "desktop app" not in response.text, response.text


def test_skill_save_needs_the_proof_unless_it_disables_the_skill(desktop: None) -> None:
    disabled = {"name": "notes", "content": "Be brief.", "status": "disabled"}
    assert call("POST", "/skills", disabled).status_code == 200
    enabled = {"name": "notes2", "content": "Be brief."}
    assert_refused(call("POST", "/skills", enabled), "skill.save")
    proof = shell_proof("POST", "/skills", enabled)
    done = call("POST", "/skills", enabled, proof)
    assert done.status_code == 200 and done.json()["status"] == "enabled", done.text


def test_user_skills_adding_needs_the_proof_removing_does_not(desktop: None) -> None:
    widen = {"skills": ["/reviewer"]}
    assert_refused(call("PUT", "/skills/user", widen), "skills.user.write")
    proof = shell_proof("PUT", "/skills/user", widen)
    assert call("PUT", "/skills/user", widen, proof).status_code == 200
    assert call("PUT", "/skills/user", {"skills": []}).status_code == 200


def test_provider_key_set_needs_the_proof_clear_does_not(
    desktop: None, in_memory_keychain: Any
) -> None:
    path, body = "/models/providers/openai/key", {"api_key": "sk-test"}
    assert_refused(call("PUT", path, body), "models.provider.key.set")
    assert not in_memory_keychain.store
    assert call("PUT", path, body, shell_proof("PUT", path, body)).status_code == 200
    assert in_memory_keychain.store
    assert call("DELETE", path).status_code == 200


def test_trigger_create_needs_the_proof_and_revoke_does_not(desktop: None) -> None:
    path = f"/workflow-definitions/{_workflow_id()}/triggers"
    assert_refused(call("POST", path, {"label": "hook"}), "workflow.trigger.create")
    assert not store.workflow_triggers
    created = call("POST", path, {"label": "hook"}, shell_proof("POST", path, {"label": "hook"}))
    assert created.status_code == 200, created.text
    assert call("DELETE", f"/triggers/{created.json()['token']}").status_code == 200


def test_schedules_enabling_needs_the_proof_disabling_does_not(desktop: None) -> None:
    path = f"/workflow-definitions/{_workflow_id()}/schedules"
    enabled = {"cron": "0 9 * * 1", "label": "weekly"}
    assert_refused(call("POST", path, enabled), "workflow.schedule.create")
    disabled = {**enabled, "enabled": False}
    created = call("POST", path, disabled)
    assert created.status_code == 200, created.text
    toggle = f"/schedules/{created.json()['id']}/toggle"
    assert_refused(call("POST", toggle, {"enabled": True}), "workflow.schedule.toggle")
    assert_refused(call("POST", toggle, {}), "workflow.schedule.toggle")  # flips: may turn on
    on = call("POST", toggle, {"enabled": True}, shell_proof("POST", toggle, {"enabled": True}))
    assert on.status_code == 200, on.text
    off = call("POST", toggle, {"enabled": False})
    assert off.status_code == 200, off.text
    created_on = call("POST", path, enabled, shell_proof("POST", path, enabled))
    assert created_on.status_code == 200, created_on.text


# --------------------------------------------------------------------------- #
# Profiles, legacy formats, and the route table
# --------------------------------------------------------------------------- #
def test_non_desktop_profiles_keep_principal_auth_only(monkeypatch: pytest.MonkeyPatch) -> None:
    # Default (local-lightweight) profile: unchanged, no proof.
    assert call("POST", _folder_escalation("run-357-light"), {}).status_code == 200
    # Secure profile: principal authentication, no proof.
    monkeypatch.setenv("LOCUS_RUNTIME_PROFILE", "local-secure")
    path = _folder_escalation("run-357-server")
    assert call("POST", path, {}).status_code == 401
    approved = call("POST", path, {}, headers=PRINCIPAL)
    assert approved.status_code == 200, approved.text


def test_desktop_without_a_shell_secret_refuses_widening(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCUS_RUNTIME_PROFILE", "local-native")
    monkeypatch.setenv("LOCUS_LOCAL_BOOTSTRAP_AUTHENTICATED_OPERATOR", "true")
    path = _folder_escalation("run-357-noshell")
    assert_refused(
        call("POST", path, {}, shell_proof("POST", path, {})),
        "workflow.run.escalations.approve",
        "no_shell",
    )


def test_every_mutating_route_is_classified() -> None:
    validate_shell_proof_inventory(app)  # raises on an unclassified or stale route


def test_an_unclassified_route_fails_the_inventory() -> None:
    from fastapi import FastAPI

    probe = FastAPI()

    @probe.post("/brand-new-widening-endpoint")
    def _new() -> dict[str, bool]:  # pragma: no cover - never called
        return {"ok": True}

    with pytest.raises(RuntimeError, match="brand-new-widening-endpoint"):
        validate_shell_proof_inventory(probe)


def test_the_requested_endpoints_are_protected() -> None:
    expected: dict[tuple[str, str], CapabilityEffect] = {
        ("POST", "/workflow-runs/{run_id}/escalations/{escalation_id}/approve"): (
            CapabilityEffect.WIDENING
        ),
        ("POST", "/approvals"): CapabilityEffect.CONDITIONAL,
        ("POST", "/computer-use/reset"): CapabilityEffect.WIDENING,
        ("POST", "/computer-use/panic"): CapabilityEffect.NARROWING,
        ("POST", "/platform/settings"): CapabilityEffect.CONDITIONAL,
        ("PUT", "/user/settings"): CapabilityEffect.CONDITIONAL,
        ("POST", "/guardrail-rulesets/{item_id}/publish"): CapabilityEffect.WIDENING,
        ("POST", "/guardrail-rulesets/{item_id}/activate"): CapabilityEffect.WIDENING,
        ("POST", "/guardrail-rulesets/{item_id}/rollback"): CapabilityEffect.WIDENING,
        ("POST", "/guardrail-rulesets/{item_id}/archive"): CapabilityEffect.WIDENING,
        ("DELETE", "/guardrail-rulesets/{item_id}"): CapabilityEffect.WIDENING,
        ("POST", "/integrations/mcp"): CapabilityEffect.WIDENING,
        ("POST", "/integrations/mcp/{connection_id}/approve"): CapabilityEffect.WIDENING,
        ("POST", "/integrations/{integration_id}/oauth/connect"): CapabilityEffect.WIDENING,
        ("POST", "/integrations/catalog/{catalog_id}/install"): CapabilityEffect.WIDENING,
        ("POST", "/skills/{skill_id}/promote"): CapabilityEffect.WIDENING,
        ("POST", "/skills/import"): CapabilityEffect.WIDENING,
        ("PUT", "/skills/user"): CapabilityEffect.CONDITIONAL,
        ("PUT", "/runtime/user-providers/{provider}"): CapabilityEffect.WIDENING,
        ("PUT", "/models/providers/{provider_id}/key"): CapabilityEffect.WIDENING,
        ("POST", "/workflow-definitions/{item_id}/triggers"): CapabilityEffect.WIDENING,
        ("POST", "/workflow-definitions/{item_id}/schedules"): CapabilityEffect.CONDITIONAL,
        ("POST", "/schedules/{schedule_id}/toggle"): CapabilityEffect.CONDITIONAL,
        ("POST", "/user-browser/pairing"): CapabilityEffect.WIDENING,
        ("PUT", "/user-browser/tier"): CapabilityEffect.CONDITIONAL,
    }
    table = {(rule.method, rule.path_template): rule for rule in shell_proof_rules()}
    for key, effect in expected.items():
        assert table[key].effect == effect, key
    # Narrowing never needs a proof, so it has no action, predicate or dialog.
    for rule in shell_proof_rules():
        if not rule.may_need_proof:
            assert not (rule.action or rule.predicate or rule.title or rule.risk), rule
    legacy = {rule.proof for rule in shell_proof_rules() if rule.path_template.startswith("/user-")}
    assert {ShellProofFormat.BROWSER_PAIR, ShellProofFormat.BROWSER_TIER} <= legacy
