"""LOCUS-433: reject executable and workspace symlink-ancestor aliases."""
from __future__ import annotations

from pathlib import Path

import pytest

from locus_runtime.pod_reservations import AdmissionDenied
from locus_runtime.pod_tool_broker import BrokerTool, SandboxedPodToolBroker
from tests.unit.test_pod_tool_broker import setup


def _link(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink creation unavailable: {type(exc).__name__}")


def test_parent_symlink_of_executable_is_denied_without_receipt(tmp_path, monkeypatch):
    actual = tmp_path / "actual"
    actual.mkdir()
    executable = actual / "approved_tool"
    executable.write_text("fixed-test-tool\n", encoding="utf-8")
    alias = tmp_path / "alias"
    _link(alias, actual)
    app, receipts, lease, _, _, launches = setup(
        tmp_path, monkeypatch, exec_path=alias / "approved_tool",
    )
    assert (alias / "approved_tool").is_file()
    assert not (alias / "approved_tool").is_symlink()
    with pytest.raises(AdmissionDenied, match="missing or linked"):
        app.execute(lease, tool_name="fixed_tool", action_idempotency_key="alias-exe")
    assert not launches
    with receipts.reservations._connect() as db:
        assert db.execute("SELECT count(*) FROM pod_action_receipt").fetchone()[0] == 0


def test_parent_symlink_of_workspace_is_denied_without_receipt(tmp_path, monkeypatch):
    app, receipts, lease, _, registry, launches = setup(tmp_path, monkeypatch)
    real_parent = tmp_path / "workspace_parent"
    work = real_parent / "work"
    work.mkdir(parents=True)
    alias = tmp_path / "workspace_alias"
    _link(alias, real_parent)
    descriptor = registry["fixed_tool"]
    redirected = SandboxedPodToolBroker(receipts, {
        "fixed_tool": BrokerTool(
            "fixed_tool", descriptor.argv, str(alias / "work"), descriptor.max_seconds,
        ),
    })
    assert (alias / "work").is_dir()
    assert not (alias / "work").is_symlink()
    with pytest.raises(AdmissionDenied, match="missing or linked"):
        redirected.execute(
            lease, tool_name="fixed_tool", action_idempotency_key="alias-work",
        )
    assert not launches
    with receipts.reservations._connect() as db:
        assert db.execute("SELECT count(*) FROM pod_action_receipt").fetchone()[0] == 0


def test_lexical_parent_traversal_is_denied_without_dispatch(tmp_path, monkeypatch):
    app, receipts, lease, _, registry, launches = setup(tmp_path, monkeypatch)
    directory = tmp_path / "path_segment"
    directory.mkdir()
    descriptor = registry["fixed_tool"]
    noncanonical = SandboxedPodToolBroker(receipts, {
        "fixed_tool": BrokerTool(
            "fixed_tool", descriptor.argv,
            str(directory / ".."), descriptor.max_seconds,
        ),
    })
    with pytest.raises(AdmissionDenied, match="missing or linked"):
        noncanonical.execute(
            lease, tool_name="fixed_tool", action_idempotency_key="dot-dot",
        )
    assert not launches
    with receipts.reservations._connect() as db:
        assert db.execute("SELECT count(*) FROM pod_action_receipt").fetchone()[0] == 0


def test_unaliased_administrator_path_remains_executable(tmp_path, monkeypatch):
    app, receipts, lease, _, _, launches = setup(tmp_path, monkeypatch)
    result = app.execute(lease, tool_name="fixed_tool",
                         action_idempotency_key="canonical-pass")
    assert result.receipt.state == "succeeded"
    assert len(launches) == 1
    assert [name for name, _ in receipts.audit(result.receipt.action_id)] == [
        "prepared", "dispatching", "fence_guarded", "succeeded",
    ]
