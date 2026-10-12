"""LOCUS-433 increment 11: mandatory trusted executable digest policy."""
from __future__ import annotations

import hashlib
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from locus_runtime.action_receipts import ActionReplayDenied
from locus_runtime.pod_tool_broker import BrokerTool, SandboxedPodToolBroker
from tests.unit.test_pod_tool_broker import setup


def _pinned(tmp_path, monkeypatch):
    _, receipts, lease, _, catalog, launches = setup(tmp_path, monkeypatch)
    descriptor = catalog["fixed_tool"]
    exe = Path(descriptor.argv[0])
    expected = hashlib.sha256(exe.read_bytes()).hexdigest()
    pinned = BrokerTool(
        descriptor.name, descriptor.argv, descriptor.cwd,
        descriptor.max_seconds, expected,
    )
    return receipts, lease, pinned, exe, launches


def _receipt_count(receipts):
    with receipts.reservations._connect() as db:
        return db.execute("SELECT count(*) FROM pod_action_receipt").fetchone()[0]


def test_strict_catalog_rejects_missing_pin_before_any_execution(tmp_path, monkeypatch):
    receipts, lease, descriptor, _, launches = _pinned(tmp_path, monkeypatch)
    unpinned = BrokerTool(
        descriptor.name, descriptor.argv, descriptor.cwd,
        descriptor.max_seconds,
    )
    with pytest.raises(ValueError, match="requires all executable digests"):
        SandboxedPodToolBroker(
            receipts, {"fixed_tool": unpinned},
            require_pinned_executables=True,
        )
    assert not launches
    assert _receipt_count(receipts) == 0


@pytest.mark.parametrize("flag", [None, 1, 0, "true", [], object()])
def test_strict_mode_requires_exact_bool(tmp_path, monkeypatch, flag):
    receipts, _, descriptor, _, launches = _pinned(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="must be a boolean"):
        SandboxedPodToolBroker(
            receipts, {"fixed_tool": descriptor},
            require_pinned_executables=flag,
        )
    assert not launches
    assert _receipt_count(receipts) == 0


def test_strict_mode_still_uses_single_fenced_execution(tmp_path, monkeypatch):
    receipts, lease, descriptor, _, launches = _pinned(tmp_path, monkeypatch)
    catalog = SandboxedPodToolBroker(
        receipts, {"fixed_tool": descriptor}, require_pinned_executables=True,
    )
    assert catalog.requires_pinned_executables is True
    result = catalog.execute_status(
        lease, tool_name="fixed_tool", action_idempotency_key="strict-one",
    )
    assert result.receipt.state == "succeeded"
    assert result.status.ok is True
    assert len(launches) == 1
    assert launches[0][1]["shell"] is False
    assert [e for e, _ in receipts.audit(result.receipt.action_id)] == [
        "prepared", "dispatching", "fence_guarded", "succeeded",
    ]
    with pytest.raises(ActionReplayDenied):
        catalog.execute(lease, tool_name="fixed_tool",
                        action_idempotency_key="strict-one")
    assert len(launches) == 1


def test_strict_mode_denies_substituted_file_without_receipt(tmp_path, monkeypatch):
    receipts, lease, descriptor, binary, launches = _pinned(tmp_path, monkeypatch)
    catalog = SandboxedPodToolBroker(
        receipts, {"fixed_tool": descriptor}, require_pinned_executables=True,
    )
    binary.write_bytes(b"X" * len(binary.read_bytes()))
    with pytest.raises(Exception, match="registered executable digest mismatch"):
        catalog.execute(lease, tool_name="fixed_tool",
                        action_idempotency_key="changed-binary")
    assert not launches
    assert _receipt_count(receipts) == 0


def test_strict_requirement_changes_catalog_identity(tmp_path, monkeypatch):
    receipts, _, descriptor, _, _ = _pinned(tmp_path, monkeypatch)
    tools = {"fixed_tool": descriptor}
    optional = SandboxedPodToolBroker(receipts, tools)
    mandatory = SandboxedPodToolBroker(
        receipts, tools, require_pinned_executables=True,
    )
    assert optional.catalog_digest != mandatory.catalog_digest
    assert optional.requires_pinned_executables is False
    assert mandatory.requires_pinned_executables is True
    with pytest.raises(FrozenInstanceError):
        mandatory._require_pinned_executables = False
    with pytest.raises(FrozenInstanceError):
        mandatory._tools = {}


def test_legacy_optional_catalog_accepts_unpinned_executable(tmp_path, monkeypatch):
    app, receipts, lease, _, mapping, launches = setup(tmp_path, monkeypatch)
    assert app.requires_pinned_executables is False
    assert mapping["fixed_tool"].executable_sha256 is None
    result = app.execute(lease, tool_name="fixed_tool",
                         action_idempotency_key="legacy-optional")
    assert result.receipt.state == "succeeded"
    assert len(launches) == 1
    assert _receipt_count(receipts) == 1


def test_source_map_mutation_cannot_disable_requirement(tmp_path, monkeypatch):
    receipts, lease, descriptor, _, launches = _pinned(tmp_path, monkeypatch)
    tools = {"fixed_tool": descriptor}
    catalog = SandboxedPodToolBroker(
        receipts, tools, require_pinned_executables=True,
    )
    tools.clear()
    assert catalog.requires_pinned_executables is True
    assert catalog._tools["fixed_tool"] is descriptor
    catalog.execute(lease, tool_name="fixed_tool",
                    action_idempotency_key="strict-immutable")
    assert len(launches) == 1
