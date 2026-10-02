import asyncio

from locus_runtime.security import OPAClient, PolicyEvaluationRequest


def test_opa_client_local_fallback_denies_budget_overrun() -> None:
    client = OPAClient(base_url="http://127.0.0.1:9")
    decision = asyncio.run(
        client.evaluate(
            "agent_policy",
            {
                "budget": {"tokens_used": 2, "max_tokens": 1},
                "classification": "internal",
                "provider": "local",
            },
        )
    )
    assert decision.allowed is False


def test_opa_client_local_agent_policy_allows_backend_execute_step() -> None:
    client = OPAClient(base_url="http://127.0.0.1:9")
    decision = asyncio.run(
        client.evaluate(
            "agent_policy",
            {
                "agent_id": "backend",
                "tool": "execute_step",
                "allowed_tools": ["execute_step"],
                "resource": "research",
                "budget": {"tokens_used": 0, "max_tokens": 10},
                "classification": "internal",
                "provider": "local",
            },
        )
    )
    assert decision.allowed is True


def test_opa_client_local_agent_policy_honors_dynamic_allowed_tools() -> None:
    client = OPAClient(base_url="http://127.0.0.1:9")
    decision = asyncio.run(
        client.evaluate(
            "agent_policy",
            {
                "agent_id": "custom-agent",
                "tool": "generate_code",
                "allowed_tools": ["generate_code"],
                "resource": "artifact.py",
                "budget": {"tokens_used": 0, "max_tokens": 10},
                "classification": "internal",
                "provider": "local",
            },
        )
    )
    assert decision.allowed is True


def test_opa_client_local_agent_policy_denies_missing_explicit_allowed_tools() -> None:
    client = OPAClient(base_url="http://127.0.0.1:9")
    decision = asyncio.run(
        client.evaluate(
            "agent_policy",
            {
                "agent_id": "backend",
                "tool": "execute_step",
                "resource": "research",
                "budget": {"tokens_used": 0, "max_tokens": 10},
                "classification": "internal",
                "provider": "local",
            },
        )
    )
    assert decision.allowed is False
    assert decision.details["control"] == "tool_allowlist_missing"


def test_opa_client_local_agent_policy_uses_action_when_tool_is_missing() -> None:
    client = OPAClient(base_url="http://127.0.0.1:9")
    decision = asyncio.run(
        client.evaluate(
            "agent_policy",
            {
                "agent_id": "custom-agent",
                "allowed_tools": ["generate_code"],
                "action": "generate_code",
                "resource": "artifact.py",
                "budget": {"tokens_used": 0, "max_tokens": 10},
                "classification": "internal",
                "provider": "local",
            },
        )
    )
    assert decision.allowed is True


def test_opa_client_local_agent_policy_denies_tool_call_budget_exceeded() -> None:
    client = OPAClient(base_url="http://127.0.0.1:9")
    decision = asyncio.run(
        client.evaluate(
            "agent_policy",
            {
                "agent_id": "backend",
                "tool": "execute_step",
                "allowed_tools": ["execute_step"],
                "resource": "workflow",
                "budget": {"tokens_used": 0, "max_tokens": 10},
                "classification": "internal",
                "provider": "local",
                "max_tool_calls": 1,
                "tool_calls_used": 2,
            },
        )
    )
    assert decision.allowed is False


def test_opa_client_local_agent_policy_denies_credential_like_read_file() -> None:
    client = OPAClient(base_url="http://127.0.0.1:9")
    decision = asyncio.run(
        client.evaluate(
            "agent_policy",
            {
                "agent_id": "research",
                "tool": "read_file",
                "resource": ".env",
                "budget": {"tokens_used": 0, "max_tokens": 10},
                "action": "read_file",
                "classification": "internal",
                "provider": "local",
            },
        )
    )
    assert decision.allowed is False


def test_opa_client_local_agent_policy_denies_secret_like_json_filename() -> None:
    client = OPAClient(base_url="http://127.0.0.1:9")
    decision = asyncio.run(
        client.evaluate(
            "agent_policy",
            {
                "agent_id": "research",
                "tool": "read_file",
                "resource": "backup/service-account-prod.json",
                "allowed_tools": ["read_file"],
                "budget": {"tokens_used": 0, "max_tokens": 10},
                "action": "read_file",
                "classification": "internal",
                "provider": "local",
            },
        )
    )
    assert decision.allowed is False


def test_opa_client_local_agent_policy_denies_keystore_and_bearer_like_paths() -> None:
    client = OPAClient(base_url="http://127.0.0.1:9")

    keystore = asyncio.run(
        client.evaluate(
            "agent_policy",
            {
                "agent_id": "research",
                "tool": "read_file",
                "resource": "secrets/prod.jks",
                "allowed_tools": ["read_file"],
                "budget": {"tokens_used": 0, "max_tokens": 10},
                "action": "read_file",
                "classification": "internal",
                "provider": "local",
            },
        )
    )
    bearer = asyncio.run(
        client.evaluate(
            "agent_policy",
            {
                "agent_id": "research",
                "tool": "read_file",
                "resource": "tmp/bearer_token_backup.txt",
                "allowed_tools": ["read_file"],
                "budget": {"tokens_used": 0, "max_tokens": 10},
                "action": "read_file",
                "classification": "internal",
                "provider": "local",
            },
        )
    )

    assert keystore.allowed is False
    assert bearer.allowed is False


def test_opa_client_local_agent_policy_denies_access_and_refresh_token_like_paths() -> None:
    client = OPAClient(base_url="http://127.0.0.1:9")

    access = asyncio.run(
        client.evaluate(
            "agent_policy",
            {
                "agent_id": "research",
                "tool": "read_file",
                "resource": "tmp/access_token_cache.txt",
                "allowed_tools": ["read_file"],
                "budget": {"tokens_used": 0, "max_tokens": 10},
                "action": "read_file",
                "classification": "internal",
                "provider": "local",
            },
        )
    )
    refresh = asyncio.run(
        client.evaluate(
            "agent_policy",
            {
                "agent_id": "research",
                "tool": "read_file",
                "resource": "tmp/refresh-token-backup.txt",
                "allowed_tools": ["read_file"],
                "budget": {"tokens_used": 0, "max_tokens": 10},
                "action": "read_file",
                "classification": "internal",
                "provider": "local",
            },
        )
    )

    assert access.allowed is False
    assert refresh.allowed is False


def test_opa_client_local_agent_policy_denies_restricted_external_llm() -> None:
    client = OPAClient(base_url="http://127.0.0.1:9")
    decision = asyncio.run(
        client.evaluate(
            "agent_policy",
            {
                "agent_id": "orchestrator",
                "tool": "llm_call",
                "resource": "workflow",
                "budget": {"tokens_used": 0, "max_tokens": 10},
                "action": "llm_call",
                "classification": "restricted",
                "provider": "openai",
            },
        )
    )
    assert decision.allowed is False


def test_opa_client_local_fallback_denies_unallowlisted_egress() -> None:
    client = OPAClient(base_url="http://127.0.0.1:9")
    decision = asyncio.run(
        client.evaluate(
            "network_egress",
            {
                "action": "network_egress",
                "target": "evil.example.com",
                "allowed_targets": ["api.example.com"],
            },
        )
    )
    assert decision.allowed is False


def test_opa_client_local_fallback_denies_root_tool_jail() -> None:
    client = OPAClient(base_url="http://127.0.0.1:9")
    decision = asyncio.run(
        client.evaluate(
            "tool_jail",
            {
                "readonly_rootfs": True,
                "require_egress_mediation": True,
                "allow_network": False,
                "run_as_user": "0:0",
                "command": ["python", "-c", "1+1"],
                "allowed_executables": ["python"],
            },
        )
    )
    assert decision.allowed is False


def test_opa_client_local_fallback_allows_safe_tool_jail() -> None:
    client = OPAClient(base_url="http://127.0.0.1:9")
    decision = asyncio.run(
        client.evaluate(
            "tool_jail",
            {
                "readonly_rootfs": True,
                "require_egress_mediation": True,
                "allow_network": True,
                "run_as_user": "1000:1000",
                "command": ["python", "-c", "1+1"],
                "allowed_executables": ["python"],
                "allowed_hosts": ["api.example.com"],
                "requested_hosts": ["api.example.com"],
            },
        )
    )
    assert decision.allowed is True


def test_opa_client_local_fallback_denies_invalid_run_as_user() -> None:
    client = OPAClient(base_url="http://127.0.0.1:9")
    decision = asyncio.run(
        client.evaluate(
            "tool_jail",
            {
                "readonly_rootfs": True,
                "require_egress_mediation": True,
                "allow_network": False,
                "run_as_user": "nobody:1000",
                "command": ["python", "-c", "1+1"],
                "allowed_executables": ["python"],
            },
        )
    )
    assert decision.allowed is False


def test_opa_client_local_fallback_denies_network_without_mediation() -> None:
    client = OPAClient(base_url="http://127.0.0.1:9")
    decision = asyncio.run(
        client.evaluate(
            "tool_jail",
            {
                "readonly_rootfs": True,
                "require_egress_mediation": False,
                "allow_network": True,
                "run_as_user": "1000:1000",
                "command": ["python", "-c", "1+1"],
                "allowed_executables": ["python"],
                "allowed_hosts": ["api.example.com"],
                "requested_hosts": ["api.example.com"],
            },
        )
    )
    assert decision.allowed is False


def test_opa_client_local_fallback_denies_tool_jail_without_allowed_executables() -> None:
    client = OPAClient(base_url="http://127.0.0.1:9")
    decision = asyncio.run(
        client.evaluate(
            "tool_jail",
            {
                "readonly_rootfs": True,
                "require_egress_mediation": True,
                "allow_network": False,
                "run_as_user": "1000:1000",
                "command": ["python", "-c", "1+1"],
            },
        )
    )
    assert decision.allowed is False
    assert decision.details["control"] == "allowed_executables"


def test_opa_client_local_fallback_denies_tool_jail_unallowlisted_hosts() -> None:
    client = OPAClient(base_url="http://127.0.0.1:9")
    decision = asyncio.run(
        client.evaluate(
            "tool_jail",
            {
                "readonly_rootfs": True,
                "require_egress_mediation": True,
                "allow_network": True,
                "run_as_user": "1000:1000",
                "command": ["python", "-c", "1+1"],
                "allowed_executables": ["python"],
                "allowed_hosts": ["api.example.com"],
                "requested_hosts": ["evil.example.com"],
            },
        )
    )
    assert decision.allowed is False
    assert decision.details["control"] == "requested_hosts"


def test_opa_client_local_fallback_denies_requested_hosts_when_network_disabled() -> None:
    client = OPAClient(base_url="http://127.0.0.1:9")
    decision = asyncio.run(
        client.evaluate(
            "tool_jail",
            {
                "readonly_rootfs": True,
                "require_egress_mediation": True,
                "allow_network": False,
                "run_as_user": "1000:1000",
                "command": ["python", "-c", "1+1"],
                "allowed_executables": ["python"],
                "requested_hosts": ["api.example.com"],
            },
        )
    )
    assert decision.allowed is False
    assert decision.details["control"] == "requested_hosts"


def test_opa_client_filesystem_path_policy_uses_canonical_containment(tmp_path) -> None:
    client = OPAClient(base_url="http://127.0.0.1:9")
    allowed_root = tmp_path / "allowed"
    allowed_root.mkdir()
    target = allowed_root / "nested" / "artifact.txt"
    target.parent.mkdir()
    target.write_text("ok", encoding="utf-8")

    decision = asyncio.run(
        client.evaluate(
            "filesystem_path",
            {
                "action": "read",
                "target_path": str(target),
                "allowed_read_paths": [str(allowed_root)],
            },
        )
    )

    assert decision.allowed is True


def test_opa_client_filesystem_path_policy_denies_prefix_bypass(tmp_path) -> None:
    client = OPAClient(base_url="http://127.0.0.1:9")
    allowed_root = tmp_path / "allowed"
    allowed_root.mkdir()
    target = tmp_path / "allowed-evil" / "artifact.txt"
    target.parent.mkdir()
    target.write_text("nope", encoding="utf-8")

    decision = asyncio.run(
        client.evaluate(
            "filesystem_path",
            {
                "action": "read",
                "target_path": str(target),
                "allowed_read_paths": [str(allowed_root)],
            },
        )
    )

    assert decision.allowed is False


def test_opa_client_evaluate_request_returns_structured_details() -> None:
    client = OPAClient(base_url="http://127.0.0.1:9")
    request = PolicyEvaluationRequest(
        policy_name="agent_policy",
        agent_id="backend",
        tool="execute_step",
        action="execute_step",
        allowed_tools=("execute_step",),
        classification="internal",
        provider="local",
        budget_tokens_used=0,
        budget_max_tokens=10,
    )

    decision = asyncio.run(client.evaluate_request(request))

    assert decision.allowed is True
    assert decision.details["policy_name"] == "agent_policy"
    assert decision.details["control"] == "allowlisted_tool"


def test_parse_run_as_user_uid_rejects_invalid_values() -> None:
    assert OPAClient._safe_int("123") == 123
    from locus_runtime.security import _parse_run_as_user_uid

    assert _parse_run_as_user_uid("1000:1000") == 1000
    assert _parse_run_as_user_uid("0:0") == 0
    assert _parse_run_as_user_uid("nobody") is None
    assert _parse_run_as_user_uid("-1:0") is None
