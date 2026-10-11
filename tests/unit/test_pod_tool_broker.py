"""LOCUS-433 bounded sandbox broker security and regression tests."""
from __future__ import annotations
import subprocess
from pathlib import Path
from unittest.mock import Mock
import pytest

from locus_runtime.pod_tool_broker import BrokerTool, SandboxedPodToolBroker
from locus_runtime.pod_reservations import AdmissionDenied
from locus_runtime.action_receipts import ActionReplayDenied, PodActionReceipts
from locus_runtime.sandbox import (
    ExecutionPlan, HostPlatform, IsolationStrategy,
)
from tests.unit.test_action_receipts import setup


def fixture(tmp_path, *, strategy=IsolationStrategy.KERNEL_BWRAP, runner=None):
    ledger, receipts, parent, lease, clock = setup(tmp_path)
    exe = tmp_path / "trusted_tool"
    exe.write_text("fixed tool descriptor only", encoding="utf-8")
    descriptor = BrokerTool("list_issues", (str(exe), "--fixed"), str(tmp_path))
    manager = Mock()
    manager.plan.return_value = ExecutionPlan(
        backend=strategy.value, command=["/usr/bin/bwrap", "--", str(exe)],
        strategy=strategy
    )
    run = runner or Mock(return_value=subprocess.CompletedProcess(
        args=[],returncode=0,stdout=b"ok",stderr=b""
    ))
    broker = SandboxedPodToolBroker(
        receipts, {"list_issues":descriptor},manager=manager,
        run=run,platform=HostPlatform.LINUX
    )
    return broker, lease, run, manager


def test_registered_allowed_tool_executes_only_inside_confining_plan(tmp_path):
    broker, lease, run, manager = fixture(tmp_path)
    receipt, result = broker.execute(lease,tool_name="list_issues",
                                     action_idempotency_key="fixed-1")
    assert receipt.state == "succeeded"
    assert result.stdout == "ok"
    call = run.call_args
    assert call.kwargs["shell"] is False
    assert call.kwargs["timeout"] == 30
    assert "SECRET" not in call.kwargs["env"]
    plan = manager.plan.call_args
    assert plan.args[1].allow_network is False
    assert plan.args[0].requested_hosts == []


def test_replay_never_invokes_runner_twice(tmp_path):
    broker, lease, run, _ = fixture(tmp_path)
    broker.execute(lease,tool_name="list_issues",action_idempotency_key="same")
    with pytest.raises(ActionReplayDenied):
        broker.execute(lease,tool_name="list_issues",action_idempotency_key="same")
    assert run.call_count == 1


def test_unknown_tool_fails_before_plan_or_callback(tmp_path):
    broker, lease, run, manager = fixture(tmp_path)
    with pytest.raises(AdmissionDenied):
        broker.execute(lease,tool_name="send_email",action_idempotency_key="forbidden")
    manager.plan.assert_not_called()
    run.assert_not_called()


@pytest.mark.parametrize("strategy", [
    IsolationStrategy.RESTRICTED_PROCESS,
    IsolationStrategy.K8S_GVISOR, IsolationStrategy.K8S_KATA,
])
def test_nonlocal_or_weak_sandbox_never_executes(tmp_path,strategy):
    broker, lease, run, _ = fixture(tmp_path,strategy=strategy)
    with pytest.raises(AdmissionDenied,match="confining"):
        broker.execute(lease,tool_name="list_issues",action_idempotency_key="weak")
    run.assert_not_called()


def test_provider_timeout_is_uncertain_and_replay_denied(tmp_path):
    run = Mock(side_effect=subprocess.TimeoutExpired(cmd=["tool"],timeout=1))
    broker, lease, _, _ = fixture(tmp_path,runner=run)
    with pytest.raises(subprocess.TimeoutExpired):
        broker.execute(lease,tool_name="list_issues",action_idempotency_key="timeout")
    with pytest.raises(ActionReplayDenied):
        broker.execute(lease,tool_name="list_issues",action_idempotency_key="timeout")
    assert run.call_count == 1


def test_bounded_output_does_not_store_raw_secret_in_receipt(tmp_path):
    result = subprocess.CompletedProcess(args=[],returncode=0,
        stdout=b"Bearer secret_token " + b"x"*70000,stderr=b"")
    broker, lease, run, _ = fixture(tmp_path,
        runner=Mock(return_value=result))
    rec, output = broker.execute(lease,tool_name="list_issues",
                                 action_idempotency_key="private")
    assert len(output.stdout) <= 65536
    assert rec.state == "succeeded"
    assert "Bearer" not in repr(broker.receipts.get(rec.action_id))


@pytest.mark.parametrize("tool",[
    ("",("/bin/true",),"/tmp"),
    ("ok",("relative",),"/tmp"),
    ("ok",("/bin/true",),"relative"),
    ("ok",(),"/tmp"),
])
def test_invalid_registered_tool_is_rejected(tool):
    with pytest.raises(ValueError):
        BrokerTool(*tool)


def test_registry_name_mismatch_rejected(tmp_path):
    exe=tmp_path/"trusted";exe.write_text("")
    t=BrokerTool("one",(str(exe),),str(tmp_path))
    with pytest.raises(ValueError):
        SandboxedPodToolBroker(Mock(),{"two":t})


def test_unregistered_child_capability_fails_closed(tmp_path):
    broker, lease, run, manager=fixture(tmp_path)
    broker._tools["not_granted"]=BrokerTool(
        "not_granted",broker._tools["list_issues"].argv,str(tmp_path))
    with pytest.raises(AdmissionDenied,match="capabilities"):
        broker.execute(lease,tool_name="not_granted",
                       action_idempotency_key="no-grant")
    run.assert_not_called()



def test_sandbox_plan_must_not_be_raw_tool_command(tmp_path):
    broker, lease, run, manager = fixture(tmp_path)
    manager.plan.return_value = ExecutionPlan(
        backend="kernel-bwrap",
        command=list(broker._tools["list_issues"].argv),
        strategy=IsolationStrategy.KERNEL_BWRAP,
    )
    with pytest.raises(AdmissionDenied, match="directly invoke"):
        broker.execute(lease, tool_name="list_issues",
                       action_idempotency_key="raw-tool")
    run.assert_not_called()


def test_windows_launcher_cwd_from_confining_plan_is_obeyed(tmp_path):
    broker, lease, run, manager = fixture(tmp_path)
    manager.plan.return_value = ExecutionPlan(
        backend="windows-appcontainer", command=["C:/Windows/isolated-launcher.exe",
                                                    "C:/Temp/trusted_tool"],
        strategy=IsolationStrategy.WINDOWS_APPCONTAINER,
        metadata={"launcher_cwd": str(tmp_path)},
    )
    receipt, _ = broker.execute(lease, tool_name="list_issues",
                                 action_idempotency_key="windows-launch")
    assert receipt.state == "succeeded"
    assert run.call_args.kwargs["cwd"] == str(tmp_path)


def test_invalid_launcher_cwd_denies_before_invocation(tmp_path):
    broker, lease, run, manager = fixture(tmp_path)
    manager.plan.return_value = ExecutionPlan(
        backend="kernel-bwrap", command=["/usr/bin/bwrap", "--", "fixed"],
        strategy=IsolationStrategy.KERNEL_BWRAP,
        metadata={"launcher_cwd": "relative"},
    )
    with pytest.raises(AdmissionDenied, match="launcher working directory"):
        broker.execute(lease, tool_name="list_issues",
                       action_idempotency_key="bad-cwd")
    run.assert_not_called()
