"""Policy parity suite (LOCUS-328, THREAT-MODEL T9).

Representative inputs for all seven repository policies, evaluated by the real
Rego engine through ``PolicyEngine``. Expected outcomes come from the
``policies/tests/*.rego`` cases, plus the cases the removed Python rule copy
(``OPAClient``) used to assert, so nothing that copy promised is silently lost.
Requires OPA: skips locally without it, runs in CI (``quality-policy-helm``).
"""

from __future__ import annotations

from typing import Any

import pytest

from locus_runtime.policy_engine import (
    KNOWN_POLICIES,
    OpaSidecarEngine,
    default_policy_dir,
    is_test_module,
    policy_dir_version,
)


def _agent(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "agent_id": "orchestrator",
        "tool": "execute_step",
        "allowed_tools": ["execute_step"],
        "resource": "workflow",
        "budget": {"tokens_used": 0, "max_tokens": 10},
        "action": "execute_step",
        "classification": "internal",
        "provider": "local",
    }
    base.update(overrides)
    return {key: value for key, value in base.items() if value is not None}


def _read(resource: str) -> dict[str, Any]:
    return _agent(
        agent_id="research",
        tool="read_file",
        action="read_file",
        allowed_tools=["read_file"],
        resource=resource,
    )


def _jail(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "readonly_rootfs": True,
        "require_egress_mediation": True,
        "allow_network": False,
        "run_as_user": "1000:1000",
        "command": ["python", "-c", "1+1"],
        "allowed_executables": ["python"],
    }
    base.update(overrides)
    return {key: value for key, value in base.items() if value is not None}


# (case id, policy, input, expected allow)
ALLOW_CASES: list[tuple[str, str, dict[str, Any], bool]] = [
    # --- agent_policy (policies/tests/agent_policy_test.rego) ---
    ("agent.registered_tool", "agent_policy", _agent(), True),
    ("agent.backend_execute_step", "agent_policy", _agent(agent_id="backend"), True),
    (
        "agent.dynamic_allowed_tools",
        "agent_policy",
        _agent(agent_id="custom-agent", tool="generate_code", allowed_tools=["generate_code"]),
        True,
    ),
    (
        "agent.action_when_tool_missing",
        "agent_policy",
        _agent(
            agent_id="custom-agent",
            tool=None,
            action="generate_code",
            allowed_tools=["generate_code"],
        ),
        True,
    ),
    ("agent.deny_dotenv", "agent_policy", _read(".env"), False),
    (
        "agent.deny_service_account_json",
        "agent_policy",
        _read("backup/service-account-prod.json"),
        False,
    ),
    ("agent.deny_bearer_filename", "agent_policy", _read("tmp/bearer_token_backup.txt"), False),
    (
        "agent.deny_restricted_external_llm",
        "agent_policy",
        _agent(
            tool="llm_call",
            action="llm_call",
            allowed_tools=None,
            classification="restricted",
            provider="openai",
        ),
        False,
    ),
    (
        "agent.deny_tool_call_budget",
        "agent_policy",
        _agent(agent_id="backend", allowed_tools=None, max_tool_calls=1, tool_calls_used=2),
        False,
    ),
    # --- agent_policy (cases formerly asserted by the Python copy) ---
    ("agent.deny_missing_allowed_tools", "agent_policy", _agent(allowed_tools=None), False),
    ("agent.deny_tool_not_allowlisted", "agent_policy", _agent(allowed_tools=["read_file"]), False),
    (
        "agent.deny_token_budget_overrun",
        "agent_policy",
        _agent(budget={"tokens_used": 11, "max_tokens": 10}),
        False,
    ),
    (
        "agent.deny_tool_budget_with_allowlist",
        "agent_policy",
        _agent(max_tool_calls=1, tool_calls_used=2),
        False,
    ),
    (
        "agent.allow_within_tool_budget",
        "agent_policy",
        _agent(max_tool_calls=2, tool_calls_used=2),
        True,
    ),
    (
        "agent.deny_restricted_external_llm_even_if_allowlisted",
        "agent_policy",
        _agent(
            tool="llm_call",
            action="llm_call",
            allowed_tools=["llm_call"],
            classification="restricted",
            provider="openai",
        ),
        False,
    ),
    (
        "agent.allow_restricted_local_llm",
        "agent_policy",
        _agent(
            tool="llm_call",
            action="llm_call",
            allowed_tools=["llm_call"],
            classification="restricted",
            provider="local",
        ),
        True,
    ),
    ("agent.deny_keystore", "agent_policy", _read("certs/server.keystore"), False),
    ("agent.deny_pem", "agent_policy", _read("deploy/tls.pem"), False),
    ("agent.deny_ssh_dir", "agent_policy", _read("home/user/.ssh/config"), False),
    ("agent.deny_id_rsa", "agent_policy", _read("id_rsa"), False),
    ("agent.deny_access_token_name", "agent_policy", _read("cache/access_token.txt"), False),
    ("agent.deny_refresh_token_name", "agent_policy", _read("cache/refresh-token.json"), False),
    ("agent.deny_gcloud_config", "agent_policy", _read("/home/u/.config/gcloud/creds.db"), False),
    ("agent.allow_ordinary_read", "agent_policy", _read("docs/report.txt"), True),
    (
        "agent.allow_allowlisted_egress",
        "agent_policy",
        _agent(
            tool="network_egress",
            action="network_egress",
            allowed_tools=["network_egress"],
            target="api.example.com",
            allowed_targets=["api.example.com"],
        ),
        True,
    ),
    (
        "agent.deny_unallowlisted_egress",
        "agent_policy",
        _agent(
            tool="network_egress",
            action="network_egress",
            allowed_tools=["network_egress"],
            target="evil.example.com",
            allowed_targets=["api.example.com"],
        ),
        False,
    ),
    (
        "agent.deny_egress_without_allowlist",
        "agent_policy",
        _agent(
            tool="network_egress",
            action="network_egress",
            allowed_tools=["network_egress"],
            target="api.example.com",
            allowed_targets=[],
        ),
        False,
    ),
    # --- budget_policy (policies/tests/budget_policy_test.rego; deny by default) ---
    (
        "budget.within_limits",
        "budget_policy",
        {
            "tokens_used": 10,
            "max_tokens": 10,
            "duration_used_seconds": 5,
            "max_duration_seconds": 60,
            "cost_used_usd": 0.5,
            "max_cost_usd": 1.0,
        },
        True,
    ),
    ("budget.deny_tokens", "budget_policy", {"tokens_used": 11, "max_tokens": 10}, False),
    (
        "budget.deny_duration",
        "budget_policy",
        {
            "tokens_used": 1,
            "max_tokens": 10,
            "duration_used_seconds": 61,
            "max_duration_seconds": 60,
        },
        False,
    ),
    (
        "budget.deny_cost",
        "budget_policy",
        {"tokens_used": 1, "max_tokens": 10, "cost_used_usd": 1.01, "max_cost_usd": 1.0},
        False,
    ),
    ("budget.deny_empty_input", "budget_policy", {}, False),
    ("budget.deny_missing_token_limit", "budget_policy", {"tokens_used": 1}, False),
    ("budget.deny_non_numeric", "budget_policy", {"tokens_used": "1", "max_tokens": 10}, False),
    # --- filesystem_access (policies/tests/filesystem_access_test.rego) ---
    (
        "fs.allow_under_root",
        "filesystem_access",
        {
            "action": "read",
            "path": "/workspace/project/file.txt",
            "allowed_paths": ["/workspace/project"],
        },
        True,
    ),
    (
        "fs.deny_outside_root",
        "filesystem_access",
        {"action": "read", "path": "/etc/passwd", "allowed_paths": ["/workspace/project"]},
        False,
    ),
    (
        "fs.deny_prefix_bypass",
        "filesystem_access",
        {
            "action": "read",
            "path": "/workspace/project-evil/secrets.txt",
            "allowed_paths": ["/workspace/project"],
        },
        False,
    ),
    (
        "fs.deny_parent_traversal",
        "filesystem_access",
        {
            "action": "read",
            "path": "/workspace/project/../secrets.txt",
            "allowed_paths": ["/workspace/project"],
        },
        False,
    ),
    (
        "fs.allow_dot_segments",
        "filesystem_access",
        {
            "action": "read",
            "path": "/workspace/project/./nested/file.txt",
            "allowed_paths": ["/workspace/project/"],
        },
        True,
    ),
    (
        "fs.deny_write_action",
        "filesystem_access",
        {
            "action": "write",
            "path": "/workspace/project/file.txt",
            "allowed_paths": ["/workspace/project"],
        },
        False,
    ),
    (
        "fs.allow_backslash_path",
        "filesystem_access",
        {
            "action": "read",
            "path": "C:\\workspace\\project\\a.txt",
            "allowed_paths": ["C:/workspace/project"],
        },
        True,
    ),
    # --- network_egress (policies/tests/network_egress_test.rego) ---
    (
        "egress.allow_allowlisted",
        "network_egress",
        {
            "action": "network_egress",
            "target": "api.example.com",
            "allowed_targets": ["api.example.com"],
        },
        True,
    ),
    (
        "egress.deny_unallowlisted",
        "network_egress",
        {
            "action": "network_egress",
            "target": "evil.example.com",
            "allowed_targets": ["api.example.com"],
        },
        False,
    ),
    (
        "egress.deny_other_action",
        "network_egress",
        {
            "action": "read_file",
            "target": "api.example.com",
            "allowed_targets": ["api.example.com"],
        },
        False,
    ),
    # --- network_policy (policies/tests/network_policy_test.rego) ---
    (
        "netpol.orchestrator_to_agent",
        "network_policy",
        {"source": "orchestrator", "target": "agent-research"},
        True,
    ),
    (
        "netpol.deny_agent_to_postgres",
        "network_policy",
        {"source": "agent-research", "target": "postgres"},
        False,
    ),
    ("netpol.agent_to_opa", "network_policy", {"source": "agent-code", "target": "opa"}, True),
    (
        "netpol.envoy_to_openai",
        "network_policy",
        {"source": "envoy", "target": "https://api.openai.com"},
        True,
    ),
    (
        "netpol.deny_unknown_source",
        "network_policy",
        {"source": "browser", "target": "vault"},
        False,
    ),
    # --- tool_jail (policies/tests/tool_jail_test.rego) ---
    (
        "jail.allow_safe",
        "tool_jail",
        _jail(
            allow_network=True,
            allowed_hosts=["api.example.com"],
            requested_hosts=["api.example.com"],
        ),
        True,
    ),
    ("jail.deny_root", "tool_jail", _jail(run_as_user="0:0"), False),
    ("jail.deny_invalid_user", "tool_jail", _jail(run_as_user="nobody:1000"), False),
    ("jail.deny_missing_executables", "tool_jail", _jail(allowed_executables=None), False),
    (
        "jail.deny_hosts_when_network_disabled",
        "tool_jail",
        _jail(requested_hosts=["api.example.com"]),
        False,
    ),
    (
        "jail.deny_unallowlisted_hosts",
        "tool_jail",
        _jail(
            allow_network=True,
            allowed_hosts=["api.example.com"],
            requested_hosts=["evil.example.com"],
        ),
        False,
    ),
    # --- tool_jail (cases formerly asserted by the Python copy) ---
    ("jail.allow_no_network", "tool_jail", _jail(), True),
    ("jail.deny_writable_rootfs", "tool_jail", _jail(readonly_rootfs=False), False),
    (
        "jail.deny_network_without_mediation",
        "tool_jail",
        _jail(
            require_egress_mediation=False,
            allow_network=True,
            allowed_hosts=["a.example"],
            requested_hosts=["a.example"],
        ),
        False,
    ),
    ("jail.deny_unlisted_executable", "tool_jail", _jail(command=["bash", "-c", "id"]), False),
]

# Rules the .rego tests assert on directly (``agent_policy.deny``).
EXPECTED_DENY_RULE = {
    "agent.deny_dotenv",
    "agent.deny_service_account_json",
    "agent.deny_bearer_filename",
    "agent.deny_restricted_external_llm",
    "agent.deny_tool_call_budget",
}

CLASSIFICATION_CASES: list[tuple[str, str]] = [
    ("contains SSN data", "restricted"),
    ("customer escalation", "confidential"),
    ("contains private key material", "restricted"),
    ("password reset email", "confidential"),
    ("rotate the API_KEY tonight", "restricted"),
    ("weekly status update", "internal"),
]


def test_parity_table_covers_all_seven_policies() -> None:
    covered = {policy for _, policy, _, _ in ALLOW_CASES} | {"data_classification"}
    assert covered == KNOWN_POLICIES


@pytest.mark.parametrize(
    ("policy", "payload", "expected"),
    [
        pytest.param(policy, payload, expected, id=case)
        for case, policy, payload, expected in ALLOW_CASES
    ],
)
def test_engine_decision_matches_expected(
    opa_engine: OpaSidecarEngine,
    request: pytest.FixtureRequest,
    policy: str,
    payload: dict[str, Any],
    expected: bool,
) -> None:
    decision = opa_engine.decide(policy, payload)
    assert decision.allow is expected, decision.reasons
    assert decision.backend == "opa-sidecar"
    assert not decision.reasons[0].startswith("policy_engine_"), decision.reasons
    if request.node.callspec.id in EXPECTED_DENY_RULE:
        assert decision.outputs.get("deny") is True


@pytest.mark.parametrize(("text", "expected"), CLASSIFICATION_CASES)
def test_data_classification_matches_expected(
    opa_engine: OpaSidecarEngine, text: str, expected: str
) -> None:
    decision = opa_engine.decide("data_classification", {"text": text})
    assert decision.outputs.get("classification") == expected
    # A classifier never grants anything on its own.
    assert decision.allow is False


def test_overlapping_classification_takes_the_highest_label(opa_engine: OpaSidecarEngine) -> None:
    # "ssn" (restricted) and "customer" (confidential) both match; restricted wins.
    decision = opa_engine.decide("data_classification", {"text": "customer ssn export"})
    assert decision.outputs.get("classification") == "restricted"
    assert decision.allow is False


def test_policy_version_is_the_repo_bundle_hash(opa_engine: OpaSidecarEngine) -> None:
    expected = policy_dir_version(default_policy_dir())
    assert opa_engine.policy_version == expected
    assert opa_engine.decide("network_policy", {}).policy_version == expected


def test_external_sidecar_reports_the_same_policy_version(opa_engine: OpaSidecarEngine) -> None:
    assert opa_engine.base_url is not None
    external = OpaSidecarEngine(base_url=opa_engine.base_url, timeout_seconds=5.0).start()
    try:
        assert external.policy_version == opa_engine.policy_version
        decision = external.decide("network_policy", {"source": "orchestrator", "target": "opa"})
        assert decision.allow is True
    finally:
        external.close()


def test_sidecar_is_loopback_only_and_loads_no_test_packages(opa_engine: OpaSidecarEngine) -> None:
    assert opa_engine.base_url is not None
    assert opa_engine.base_url.startswith("http://127.0.0.1:")
    response = opa_engine._client.get(f"{opa_engine.base_url}/v1/policies")
    raws = [module["raw"] for module in response.json()["result"]]
    assert len(raws) == len(KNOWN_POLICIES)
    assert not any(is_test_module(raw) for raw in raws)


def test_stopped_sidecar_denies(opa_engine: OpaSidecarEngine) -> None:
    from locus_runtime.policy_engine import REASON_UNAVAILABLE, find_opa_binary

    # Depends on opa_engine for the skip-locally / fail-in-CI semantics.
    binary = find_opa_binary()
    assert binary is not None
    engine = OpaSidecarEngine(opa_binary=binary, timeout_seconds=5.0).start()
    assert engine.decide("network_policy", {"source": "orchestrator", "target": "opa"}).allow
    engine._stop_process()
    decision = engine.decide("network_policy", {"source": "orchestrator", "target": "opa"})
    assert decision.allow is False
    assert decision.reasons == [REASON_UNAVAILABLE]
    engine.close()
