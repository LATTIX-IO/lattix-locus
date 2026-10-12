"""LOCUS-433: durable gateway/receipt binding to immutable catalog revision."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from locus_runtime.action_receipts import ActionReplayDenied
from locus_runtime.pod_sandbox_broker import BrokerCommand
from locus_runtime.pod_tool_broker import BrokerTool, SandboxedPodToolBroker
from tests.unit.test_pod_tool_broker import setup


@pytest.mark.parametrize("digest", [
    "", "0" * 63, "0" * 65, "A" * 64,
    "xyz" + "0" * 61, 0, b"0" * 64,
])
def test_malformed_catalog_fingerprint_denied_before_execution(digest):
    with pytest.raises(ValueError, match="catalog digest"):
        BrokerCommand(
            tool="fixed_tool", argv=("/bin/true",), working_dir="/tmp",
            idempotency_key="bad-catalog", catalog_digest=digest,
        )


def test_registered_catalog_digest_is_bound_to_action_authorization(
    tmp_path, monkeypatch,
):
    app, receipts, lease, _, _, launches = setup(tmp_path, monkeypatch)
    seen = []
    original = receipts.prepare

    def track(lease_arg, *, idempotency_key, authorization):
        seen.append(dict(authorization))
        return original(
            lease_arg, idempotency_key=idempotency_key,
            authorization=authorization,
        )

    monkeypatch.setattr(receipts, "prepare", track)
    out = app.execute(
        lease, tool_name="fixed_tool", action_idempotency_key="catalog-bound",
    )
    assert out.receipt.state == "succeeded"
    assert len(launches) == 1
    assert len(seen) == 1
    assert seen[0]["args"] == {"catalog_digest": app.catalog_digest}
    assert seen[0]["kind"] == "process_exec"
    assert "argv" not in seen[0]["args"]
    assert len(out.receipt.fingerprint) == 64
    assert app.catalog_digest not in repr(out.receipt)
    with receipts.reservations._connect() as db:
        row = db.execute(
            "SELECT fingerprint, gateway_audit_id FROM pod_action_receipt"
        ).fetchone()
    assert row["fingerprint"] == out.receipt.fingerprint


def test_same_command_new_admin_catalog_revision_replays_as_different_action(
    tmp_path, monkeypatch,
):
    _, receipts, lease, _, descriptors, launches = setup(tmp_path, monkeypatch)
    tool = descriptors["fixed_tool"]
    digest = hashlib.sha256(Path(tool.argv[0]).read_bytes()).hexdigest()
    pinned = BrokerTool(
        tool.name, tool.argv, tool.cwd, tool.max_seconds, digest,
    )
    source = {"fixed_tool": pinned}
    optional = SandboxedPodToolBroker(
        receipts, source, require_pinned_executables=False,
    )
    strict = SandboxedPodToolBroker(
        receipts, source, require_pinned_executables=True,
    )
    assert optional.catalog_digest != strict.catalog_digest
    optional.execute(
        lease, tool_name="fixed_tool", action_idempotency_key="same-action",
    )
    with pytest.raises(ActionReplayDenied, match="different action"):
        strict.execute(
            lease, tool_name="fixed_tool", action_idempotency_key="same-action",
        )
    assert len(launches) == 1
    with receipts.reservations._connect() as db:
        assert db.execute(
            "SELECT count(*) FROM pod_action_receipt"
        ).fetchone()[0] == 1


def test_same_catalog_same_key_keeps_existing_replay_barrier(tmp_path, monkeypatch):
    app, receipts, lease, _, _, launches = setup(tmp_path, monkeypatch)
    app.execute(
        lease, tool_name="fixed_tool", action_idempotency_key="existing-replay",
    )
    with pytest.raises(ActionReplayDenied):
        app.execute(
            lease, tool_name="fixed_tool", action_idempotency_key="existing-replay",
        )
    assert len(launches) == 1


def test_direct_broker_command_remains_compatible_without_catalog_identity(
    tmp_path, monkeypatch,
):
    from locus_runtime.pod_sandbox_broker import PodSandboxBroker

    _, receipts, lease, _, descriptors, launches = setup(tmp_path, monkeypatch)
    tool = descriptors["fixed_tool"]
    command = BrokerCommand(
        tool=tool.name, argv=tool.argv, working_dir=tool.cwd,
        idempotency_key="legacy-without-catalog",
        timeout_seconds=tool.max_seconds,
    )
    assert command.catalog_digest is None
    result = PodSandboxBroker(receipts).execute(lease, command)
    assert result.receipt.state == "succeeded"
    assert len(launches) == 1
