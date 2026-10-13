"""LOCUS-416 B: real SQLite/Gateway lease + measured CLI admission regressions.

A fake regular-file CLI suffices to validate the pre-execution integrity gate.
No subprocess is invoked, no vendor process is trusted, and no fake is called
an executable smoke test.
"""
from __future__ import annotations

import ast
import hashlib
import os
from dataclasses import replace
from pathlib import Path

import pytest

from locus_runtime.harness.opencode_contract import (
    OpenCodeContractDenied,
    compile_opencode_launch,
)
from locus_runtime.harness.opencode_pod_preflight import (
    _validate_observed_version,
    approved_profile_digest,
    preflight_opencode_pod,
)
from locus_runtime.pod_reservations import AdmissionDenied
from tests.unit.test_task_scheduler import REQUEST, build


pytestmark = pytest.mark.skipif(
    os.name == "nt",
    reason="This fixture exercises POSIX binary measurement; Windows path grammar is separately tested",
)


def pod(tmp_path, *, version="1.16.0", tools=()):
    scheduler, ledger, plan, parent = build(tmp_path)
    claim = scheduler.claim_next(plan, parent)
    assert claim is not None
    scheduler.reserve(plan, parent, claim, budget=REQUEST, lease_seconds=30)
    lease = scheduler.admit(plan, parent, claim, engine="trusted/opencode")
    bin_dir = tmp_path / "trusted-bin"
    bin_dir.mkdir()
    exe = bin_dir / "opencode"
    binary = b"\x7fELF mock-cli-integrity-test--no-process-launch"
    exe.write_bytes(binary)
    exe.chmod(0o755)
    profile = compile_opencode_launch(
        version=version,
        executable=str(exe),
        executable_sha256=hashlib.sha256(binary).hexdigest(),
        model="ollama/qwen3:8b",
        workspace=str(tmp_path / "workspace"),
        private_root=str(tmp_path / "private"),
        request_file=str(tmp_path / "private" / "task.md"),
        mcp_python=str(tmp_path / "trusted-bin" / "python3"),
        mcp_config_file=str(tmp_path / "private" / "mcp-run.json"),
        allowed_mcp_tools=tools,
    )
    return profile, lease, ledger, parent


@pytest.mark.skipif(os.name == "nt", reason="POSIX executable fixtures")
@pytest.mark.parametrize("version", ["1.16.0", "2.0.0"])
def test_pinned_engine_preflight_is_bound_to_real_active_child(tmp_path, version):
    profile, lease, ledger, parent = pod(tmp_path, version=version)
    result = preflight_opencode_pod(
        profile, lease,
        expected_profile_sha256=approved_profile_digest(profile),
        observed_version=f"opencode {version}\n",
        observed_effective_config=profile.config(),
    )
    assert result.result == "verified_preflight_unexecuted"
    assert result.child_run_id == lease.run_id
    assert result.reservation_id == lease.reservation_id
    assert result.parent_run_id == parent.caller.run_id
    assert result.accepted_plan_digest == ledger.get(lease.reservation_id).plan_digest
    assert result.executable_sha256 == profile.executable_sha256
    assert result.allowed_mcp_tools == ()
    assert len(result.binding_sha256) == 64
    assert "ELF mock" not in repr(result)


@pytest.mark.skipif(os.name == "nt", reason="POSIX executable fixtures")
def test_binary_hash_mismatch_denied_before_any_model_or_worker_execution(tmp_path):
    profile, lease, *_ = pod(tmp_path)
    exe = Path(profile.executable)
    exe.write_bytes(b"poisoned-executable")
    with pytest.raises(AdmissionDenied, match="digest mismatch"):
        preflight_opencode_pod(
            profile, lease,
            expected_profile_sha256=approved_profile_digest(profile),
            observed_version="1.16.0",
            observed_effective_config=profile.config(),
        )


@pytest.mark.skipif(os.name == "nt", reason="POSIX executable fixtures")
@pytest.mark.parametrize("mode", [0o777, 0o775, 0o600])
def test_world_or_group_writable_and_nonexecuting_binary_denied(tmp_path, mode):
    profile, lease, *_ = pod(tmp_path)
    Path(profile.executable).chmod(mode)
    with pytest.raises(AdmissionDenied, match="writable by group/other|nonexecuting"):
        preflight_opencode_pod(
            profile, lease,
            expected_profile_sha256=approved_profile_digest(profile),
            observed_version="1.16.0",
            observed_effective_config=profile.config(),
        )


@pytest.mark.skipif(os.name == "nt", reason="POSIX executable fixtures")
def test_symlinked_binary_and_symlinked_parent_denied(tmp_path):
    profile, lease, *_ = pod(tmp_path)
    original = Path(profile.executable)
    original.rename(original.with_name("target-opencode"))
    original.symlink_to(original.with_name("target-opencode"))
    with pytest.raises(AdmissionDenied, match="symlink"):
        preflight_opencode_pod(
            profile, lease,
            expected_profile_sha256=approved_profile_digest(profile),
            observed_version="1.16.0",
            observed_effective_config=profile.config(),
        )
    original.unlink()
    original.with_name("target-opencode").rename(original)
    parent = original.parent
    alias = tmp_path / "alias-bin"
    alias.symlink_to(parent, target_is_directory=True)
    aliased = compile_opencode_launch(
        version="1.16.0",
        executable=str(alias / "opencode"),
        executable_sha256=profile.executable_sha256,
        model=profile.model,
        workspace=profile.workspace,
        private_root=profile.private_root,
        request_file=profile.request_file,
        mcp_python=profile.mcp_python,
        mcp_config_file=profile.mcp_config_file,
    )
    with pytest.raises(AdmissionDenied, match="symlink"):
        preflight_opencode_pod(
            aliased, lease,
            expected_profile_sha256=approved_profile_digest(aliased),
            observed_version="1.16.0",
            observed_effective_config=aliased.config(),
        )


@pytest.mark.parametrize("observed", [
    "", "opencode v1.17.0", "1.16.1", "2.0.0", "1.0.0",
    "1.16.0\nmalicious", "opencode 1.16.0\r\ninjection",
    "latest", 42,
])
def test_unknown_or_spoofed_banner_denied(tmp_path, observed):
    profile, lease, *_ = pod(tmp_path)
    with pytest.raises(OpenCodeContractDenied):
        preflight_opencode_pod(
            profile, lease,
            expected_profile_sha256=approved_profile_digest(profile),
            observed_version=observed,
            observed_effective_config=profile.config(),
        )


@pytest.mark.skipif(os.name == "nt", reason="POSIX executable fixtures")
def test_gateway_revocation_invalidates_preflight_before_fs_access(tmp_path):
    profile, lease, ledger, parent = pod(tmp_path)
    ledger.revoke_parent(parent)
    with pytest.raises(AdmissionDenied, match="revoked|not active"):
        preflight_opencode_pod(
            profile, lease,
            expected_profile_sha256=approved_profile_digest(profile),
            observed_version="1.16.0",
            observed_effective_config=profile.config(),
        )


@pytest.mark.skipif(os.name == "nt", reason="POSIX executable fixtures")
def test_unauthorized_mcp_capability_denied_before_executable_measurement(tmp_path):
    # This graph only delegates list_issues. A model-proposed search or
    # execute_bash MCP permission must never widen the actual PodLease.
    for index, tool in enumerate(("locus_search", "locus_execute_bash",
                                  "locus_run_tests", "locus_str_replace_editor",
                                  "locus_invented")):
        location = tmp_path / str(index)
        location.mkdir()
        profile, lease, *_ = pod(location, tools=(tool,))
        with pytest.raises(AdmissionDenied, match="grant exceeds"):
            preflight_opencode_pod(
                profile, lease,
                expected_profile_sha256=approved_profile_digest(profile),
                observed_version="1.16.0",
                observed_effective_config=profile.config(),
            )


@pytest.mark.skipif(os.name == "nt", reason="POSIX executable fixtures")
@pytest.mark.parametrize("substitution", [
    ("argv", ("opencode", "run", "--auto")),
    ("config_json", "{}"),
    ("config_sha256", "a" * 64),
    ("model", "openai/gpt-6"),
    ("allowed_mcp_tools", ("locus_search",)),
    ("env", ()),
    ("executable_sha256", "f" * 64),
])
def test_profile_dataclass_substitution_denied_before_binary_read(tmp_path, substitution):
    profile, lease, *_ = pod(tmp_path)
    modified = replace(profile, **dict([substitution]))
    with pytest.raises(OpenCodeContractDenied, match="not approved|substituted"):
        preflight_opencode_pod(
            modified, lease,
            expected_profile_sha256=approved_profile_digest(profile),
            observed_version="1.16.0",
            observed_effective_config=profile.config(),
        )


@pytest.mark.skipif(os.name == "nt", reason="POSIX executable fixtures")
def test_effective_config_plugin_and_provider_widening_denied(tmp_path):
    profile, lease, *_ = pod(tmp_path)
    for field, payload in (
        ("plugin", ["arbitrary"]),
        ("enabled_providers", ["ollama", "openai"]),
        ("snapshot", True),
    ):
        config = profile.config()
        config[field] = payload
        with pytest.raises(OpenCodeContractDenied):
            preflight_opencode_pod(
                profile, lease,
                expected_profile_sha256=approved_profile_digest(profile),
                observed_version="1.16.0",
                observed_effective_config=config,
            )


def test_version_parser_accepts_only_expected_bounded_banner():
    from locus_runtime.harness.opencode_contract import OpenCodeVersion

    v = OpenCodeVersion.parse("1.16.0")
    assert _validate_observed_version("opencode 1.16.0\n", v) == "1.16.0"
    for raw in ("1.16.0\n2.0.0", "1.16.0\x00evil", "1.9999.0", b"1.16.0"):
        with pytest.raises(OpenCodeContractDenied):
            _validate_observed_version(raw, v)


def test_preflight_does_not_dispatch_host_process_or_provider_call():
    source = (
        Path(__file__).resolve().parents[2] /
        "locus_runtime/harness/opencode_pod_preflight.py"
    ).read_text(encoding="utf-8")
    for sink in ("subprocess.", "os.system(", "shell=True",
                 "requests.", "urllib.request", "Gateway.open_session("):
        assert sink not in source



def test_external_mcp_catalog_stays_equal_to_existing_broker_exports():
    # Drift in the only approved MCP server must not silently expand the
    # external engine's tool authority.
    from locus_runtime.harness.opencode_pod_preflight import _AUTHORIZED_MCP_EXPORTS

    path = (
        Path(__file__).resolve().parents[2] /
        "locus_runtime/harness/codex_mcp_server.py"
    )
    module = ast.parse(path.read_text(encoding="utf-8"))
    assignments = [
        n for n in module.body
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "_SUPPORTED_TOOLS" for t in n.targets)
    ]
    assert len(assignments) == 1
    expression = assignments[0].value
    assert isinstance(expression, ast.Call)
    assert isinstance(expression.func, ast.Name)
    assert expression.func.id == "frozenset"
    actual_tools = set(ast.literal_eval(expression.args[0]))
    assert {original for _, original in _AUTHORIZED_MCP_EXPORTS} == actual_tools
    assert {external for external, _ in _AUTHORIZED_MCP_EXPORTS} == {
        "locus_" + name for name in actual_tools
    }


def test_empty_or_unapproved_registry_fingerprint_denied(tmp_path):
    profile, lease, *_ = pod(tmp_path)
    for forged in ("", "f" * 64, "xyz", "A" * 64, 42):
        with pytest.raises(OpenCodeContractDenied, match="not approved"):
            preflight_opencode_pod(
                profile, lease,
                expected_profile_sha256=forged,
                observed_version="1.16.0",
                observed_effective_config=profile.config(),
            )
