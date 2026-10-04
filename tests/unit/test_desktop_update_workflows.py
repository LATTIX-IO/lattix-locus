"""LOCUS-349 (D-26): contract for the desktop update-channel workflows and endpoints.

Parses the workflows as YAML and checks the security properties:
* no updater metadata (latest.json) is produced or uploaded unless the
  TAURI_SIGNING_PRIVATE_KEY secret is present, and the manual/tag path never
  publishes any;
* the app only knows the two allow-listed channel URLs;
* least-privilege permissions, serialized channel pointers, pinned actions.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
TAURI = ROOT / "apps" / "desktop-tauri" / "src-tauri"

DEV_URL = "https://github.com/LATTIX-IO/lattix-locus/releases/download/channel-dev/latest.json"
STABLE_URL = (
    "https://github.com/LATTIX-IO/lattix-locus/releases/download/channel-stable/latest.json"
)
ALLOWED_URLS = {DEV_URL, STABLE_URL}
KEY_GATE = "env.HAS_UPDATER_KEY == 'true'"
KEY_EXPR = "${{ secrets.TAURI_SIGNING_PRIVATE_KEY != '' }}"
# Major-tag pins already used across this repository's workflows.
PINNED_ACTIONS = {
    "actions/checkout@v4",
    "actions/setup-python@v5",
    "actions/setup-node@v4",
    "actions/upload-artifact@v4",
    "actions/download-artifact@v4",
    "dtolnay/rust-toolchain@stable",
    "tauri-apps/tauri-action@v0",
}


def _load(name: str) -> dict[str, Any]:
    data = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    # PyYAML reads the bare `on:` key as boolean True.
    if True in data:
        data["on"] = data.pop(True)
    return data


def _steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    return list(job.get("steps") or [])


def _run(step: dict[str, Any]) -> str:
    return str(step.get("run") or "")


def _uses(workflow: dict[str, Any]) -> set[str]:
    return {
        str(step["uses"])
        for job in workflow["jobs"].values()
        for step in _steps(job)
        if "uses" in step
    }


# --------------------------------------------------------------------------- #
# Dev channel
# --------------------------------------------------------------------------- #
def test_dev_workflow_triggers_on_main_and_skips_docs() -> None:
    wf = _load("desktop-dev.yml")
    push = wf["on"]["push"]
    assert push["branches"] == ["main"]
    assert "docs/**" in push["paths-ignore"] and "**/*.md" in push["paths-ignore"]
    assert set(wf["on"]) == {"push"}


def test_dev_workflow_is_serialized_and_least_privilege() -> None:
    wf = _load("desktop-dev.yml")
    assert wf["concurrency"] == {"group": "desktop-dev-channel", "cancel-in-progress": False}
    assert wf["permissions"] == {"contents": "read"}
    writers = {name for name, job in wf["jobs"].items() if job.get("permissions")}
    assert writers == {"publish"}
    assert wf["jobs"]["publish"]["permissions"] == {"contents": "write"}


def test_dev_latest_json_only_with_the_signing_secret() -> None:
    wf = _load("desktop-dev.yml")
    build, publish = wf["jobs"]["build"], wf["jobs"]["publish"]
    assert build["env"]["HAS_UPDATER_KEY"] == KEY_EXPR
    assert publish["env"]["HAS_UPDATER_KEY"] == KEY_EXPR

    # Updater artifacts are only requested when the key exists.
    overlay = next(s for s in _steps(build) if s.get("name") == "Write the Dev build overlay")
    assert '"createUpdaterArtifacts": os.environ["HAS_UPDATER_KEY"] == "true"' in _run(overlay)

    for step in _steps(publish):
        body = _run(step)
        if "latest.json" in body and ("manifest" in body or "gh release upload" in body):
            assert step.get("if") == KEY_GATE, step.get("name")
    manifest = next(s for s in _steps(publish) if "desktop_channel.py manifest" in _run(s))
    assert manifest.get("if") == KEY_GATE
    # Signatures must verify against the app's pubkey before anything is published.
    assert "--pubkey-conf" in _run(manifest) and "--require-crypto" in _run(manifest)
    channel = next(s for s in _steps(publish) if s.get("name") == "Advance the Dev channel")
    assert channel.get("if") == KEY_GATE
    assert "should-advance" in _run(channel)
    # The step that always runs never uploads a manifest of its own.
    release = next(s for s in _steps(publish) if s.get("name") == "Publish the Dev prerelease")
    assert "manifest" not in _run(release) and "channel-" not in _run(release)
    assert "--prerelease" in _run(release) and "--latest=false" in _run(release)


def test_dev_summary_says_when_no_metadata_was_published() -> None:
    wf = _load("desktop-dev.yml")
    summary = next(s for s in _steps(wf["jobs"]["publish"]) if s.get("name") == "Summary")
    assert summary.get("if") == "always()"
    assert "no updater metadata was published" in _run(summary)


def test_dev_build_stamps_the_backend_and_skips_msi() -> None:
    wf = _load("desktop-dev.yml")
    build = wf["jobs"]["build"]
    sidecar = next(s for s in _steps(build) if s.get("name") == "Build backend sidecar")
    assert 'python -m locus_tooling.build_info "$VERSION"' in _run(sidecar)
    assert _run(sidecar).index("build_info") < _run(sidecar).index("pyinstaller packaging")
    matrix_step = next(s for s in _steps(wf["jobs"]["plan"]) if s.get("id") == "matrix")
    assert '"bundles": "nsis"' in _run(matrix_step)
    # No post-build Authenticode signing (it would invalidate the updater signature).
    assert not any("signtool" in _run(s) for s in _steps(build))


# --------------------------------------------------------------------------- #
# Stable promotion
# --------------------------------------------------------------------------- #
def test_promote_is_manual_serialized_and_least_privilege() -> None:
    wf = _load("desktop-promote.yml")
    assert set(wf["on"]) == {"workflow_dispatch"}
    assert wf["on"]["workflow_dispatch"]["inputs"]["version"]["required"] is True
    assert wf["concurrency"] == {"group": "desktop-stable-channel", "cancel-in-progress": False}
    assert wf["permissions"] == {"contents": "read"}
    assert wf["jobs"]["promote"]["permissions"] == {"contents": "write"}
    # The input reaches scripts only through env, never by interpolation.
    assert wf["jobs"]["promote"]["env"]["VERSION"] == "${{ inputs.version }}"
    for step in _steps(wf["jobs"]["promote"]):
        assert "inputs." not in _run(step)


def test_promote_reuses_dev_artifacts_and_never_publishes_unsigned_metadata() -> None:
    wf = _load("desktop-promote.yml")
    steps = _steps(wf["jobs"]["promote"])
    joined = "\n".join(_run(s) for s in steps)
    assert "cargo" not in joined and "pyinstaller" not in joined  # no rebuild
    assert 'gh release download "dev-v$VERSION"' in joined
    manifest = next(s for s in steps if s.get("name") == "Write the Stable manifest")
    assert "if [ -f dev/latest.json ]" in _run(manifest)
    channel = next(s for s in steps if s.get("name") == "Advance the Stable channel")
    assert channel.get("if") == "steps.manifest.outputs.signed == 'true'"
    publish = next(s for s in steps if s.get("name") == "Publish the Stable release")
    assert "! -name 'latest.json'" in _run(publish)  # the Dev manifest is never re-published
    order = [s.get("name") for s in steps]
    assert order.index("Check the Stable channel order") < order.index("Publish the Stable release")


# --------------------------------------------------------------------------- #
# Manual / tag installer path
# --------------------------------------------------------------------------- #
def test_manual_release_path_publishes_no_updater_metadata() -> None:
    wf = _load("desktop-release.yml")
    assert wf["on"]["workflow_dispatch"]["inputs"]["create_release"]["default"] is False
    assert wf["permissions"] == {"contents": "read"}
    build = wf["jobs"]["build"]
    assert build["permissions"] == {"contents": "write"}
    tauri_steps = [s for s in _steps(build) if str(s.get("uses", "")).startswith("tauri-apps/")]
    assert len(tauri_steps) == 2
    release = next(s for s in tauri_steps if "releaseDraft" in (s.get("with") or {}))
    assert release["with"]["includeUpdaterJson"] is False
    for step in tauri_steps:
        assert "TAURI_SIGNING_PRIVATE_KEY" not in (step.get("env") or {})


# --------------------------------------------------------------------------- #
# All three workflows
# --------------------------------------------------------------------------- #
def test_actions_are_pinned_like_the_rest_of_the_repo() -> None:
    for name in ("desktop-dev.yml", "desktop-promote.yml", "desktop-release.yml"):
        used = _uses(_load(name))
        assert used <= PINNED_ACTIONS, (name, used - PINNED_ACTIONS)


def test_no_workflow_grants_more_than_contents() -> None:
    for name in ("desktop-dev.yml", "desktop-promote.yml", "desktop-release.yml"):
        wf = _load(name)
        scopes = [wf.get("permissions") or {}] + [
            job.get("permissions") or {} for job in wf["jobs"].values()
        ]
        for scope in scopes:
            assert set(scope) <= {"contents"}, (name, scope)


# --------------------------------------------------------------------------- #
# Endpoints: only the two allow-listed URLs
# --------------------------------------------------------------------------- #
def test_tauri_conf_endpoints_are_allow_listed() -> None:
    conf = json.loads((TAURI / "tauri.conf.json").read_text(encoding="utf-8"))
    updater = conf["plugins"]["updater"]
    assert updater["pubkey"]
    assert set(updater["endpoints"]) <= ALLOWED_URLS
    # Updater artifacts are opted into per build (desktop-dev.yml), never by default,
    # so a keyless build cannot be asked to sign.
    assert "createUpdaterArtifacts" not in conf["bundle"]


def test_rust_updater_uses_only_the_allow_listed_urls() -> None:
    # Production code only (a unit test feeds a foreign URL to prove it is rejected).
    sources = {
        p.name: p.read_text(encoding="utf-8").split("#[cfg(test)]", 1)[0]
        for p in (TAURI / "src").glob("*.rs")
    }
    urls = {
        url
        for text in sources.values()
        for url in re.findall(r"https://[^\"\s]+latest\.json", text)
    }
    assert urls == ALLOWED_URLS
    updates = sources["updates.rs"]
    assert f'pub const DEV_ENDPOINT: &str =\n    "{DEV_URL}";' in updates
    assert f'pub const STABLE_ENDPOINT: &str =\n    "{STABLE_URL}";' in updates
    # The endpoint is chosen from the parsed channel enum, never a caller string.
    assert ".endpoints(vec![url])" in updates
    assert "Url::parse(channel.endpoint())" in updates
    main = sources["main.rs"]
    assert "updates::set_update_channel" in main and "updates::install_update_and_restart" in main
    # The handshake runs before the UI loads, and a mismatch stops the backend.
    assert "updates::backend_handshake(&expected)" in main
    assert "updates::Handshake::Mismatch(detail)" in main


# --------------------------------------------------------------------------- #
# D-22: the update trust chain is never auto-merged by the loop
# --------------------------------------------------------------------------- #
def test_update_trust_chain_is_protected_from_auto_merge() -> None:
    from locus_runtime.loop_runner.merge_guard import (
        ChangedFile,
        GateCheck,
        evaluate_auto_merge,
    )

    codeowners = (ROOT / ".github" / "CODEOWNERS").read_text(encoding="utf-8")
    green = [GateCheck("ci / test", "completed", "success")]
    for path in (
        "apps/desktop-tauri/src-tauri/tauri.conf.json",
        "apps/desktop-tauri/src-tauri/src/updates.rs",
        "scripts/desktop_channel.py",
        "locus_tooling/update_contract.py",
        "locus_tooling/desktop_update.py",
        "locus_tooling/build_info.py",
        ".github/workflows/desktop-dev.yml",
    ):
        for text in (codeowners, None):  # CODEOWNERS and the built-in baseline
            decision = evaluate_auto_merge(
                [ChangedFile(path, patch="+a\n")], green, codeowners_text=text
            )
            assert decision.action == "hold", path
            assert any("protected path changed" in r for r in decision.reasons), path


def test_manual_release_path_builds_the_version_it_names() -> None:
    # The dispatch input / tag used to name only the artifact and the tag, so
    # every build from this path reported tauri.conf.json's base 0.1.0 (app,
    # /platform/version, installer) and an unstamped backend.
    wf = _load("desktop-release.yml")
    plan = next(s for s in _steps(wf["jobs"]["plan"]) if s.get("id") == "m")
    assert wf["jobs"]["plan"]["outputs"]["version"] == "${{ steps.m.outputs.version }}"
    # The input reaches the script only through env, never by interpolation.
    assert "inputs.version" not in _run(plan)
    assert plan["env"]["VERSION_INPUT"] == "${{ github.event.inputs.version }}"
    # D-31: same version rules as every build; Windows is NSIS only (MSI caps PATCH at 65535).
    assert '"locus_tooling/versioning.py", "validate"' in _run(plan)
    assert '"bundles": "nsis"}' in _run(plan)
    build = wf["jobs"]["build"]
    assert build["env"]["VERSION"] == "${{ needs.plan.outputs.version }}"
    sidecar = next(s for s in _steps(build) if s.get("name") == "Build backend sidecar")
    assert 'python -m locus_tooling.build_info "$VERSION"' in _run(sidecar)
    assert _run(sidecar).index("build_info") < _run(sidecar).index("pyinstaller packaging")
    overlay = next(s for s in _steps(build) if s.get("name") == "Write the release version overlay")
    assert 'json.dump({"version": os.environ["VERSION"]}, fh)' in _run(overlay)
    tauri_steps = [s for s in _steps(build) if str(s.get("uses", "")).startswith("tauri-apps/")]
    for step in tauri_steps:
        args = step["with"]["args"]
        assert (
            "--config ${{ github.workspace }}/apps/desktop-tauri/src-tauri/release.conf.json"
            in args
        )
        assert "format(' --bundles {0}', matrix.bundles)" in args
    order = [s.get("name") for s in _steps(build)]
    assert order.index("Write the release version overlay") < order.index(
        "Build installers (release)"
    )


def test_dev_build_sets_the_full_dev_version_in_the_app() -> None:
    # The Dev version reaches the compiled app (package_info, hence the
    # LOCUS_APP_VERSION the shell hands the backend) through the --config
    # overlay; tauri-codegen merges TAURI_CONFIG over tauri.conf.json.
    wf = _load("desktop-dev.yml")
    build = wf["jobs"]["build"]
    overlay = next(s for s in _steps(build) if s.get("name") == "Write the Dev build overlay")
    assert '"version": os.environ["VERSION"]' in _run(overlay)
    installers = next(s for s in _steps(build) if s.get("name") == "Build installers")
    assert "--config dev-channel.conf.json" in _run(installers)
    main_rs = (TAURI / "src" / "main.rs").read_text(encoding="utf-8")
    assert '.env("LOCUS_APP_VERSION", app.package_info().version.to_string())' in main_rs


# --------------------------------------------------------------------------- #
# D-31: MAJOR.MINOR from VERSION, PATCH = build counter, immutable releases
# --------------------------------------------------------------------------- #
def test_dev_version_is_the_next_patch_of_version_from_the_remote_tags() -> None:
    wf = _load("desktop-dev.yml")
    step = next(s for s in _steps(wf["jobs"]["plan"]) if s.get("id") == "version")
    body = _run(step)
    assert "git ls-remote --tags --refs origin" in body
    assert "python locus_tooling/versioning.py next" in body
    assert "--version-file VERSION" in body
    # The run number no longer names builds.
    assert "run_number" not in json.dumps(wf) and "-dev." not in body


def test_dev_publish_refuses_an_existing_version_and_never_clobbers_it() -> None:
    wf = _load("desktop-dev.yml")
    steps = _steps(wf["jobs"]["publish"])
    names = [s.get("name") for s in steps]
    refuse = next(s for s in steps if s.get("name") == "Refuse an already published version")
    body = _run(refuse)
    for tag in (
        '"refs/tags/dev-v$VERSION"',
        '"refs/tags/stable-v$VERSION"',
        '"refs/tags/v$VERSION"',
    ):
        assert tag in body
    assert "validate" in body and "exit 1" in body
    assert names.index("Refuse an already published version") < names.index(
        "Write the signed Dev manifest"
    )
    release = next(s for s in steps if s.get("name") == "Publish the Dev prerelease")
    assert "--clobber" not in _run(release) and "gh release upload" not in _run(release)
    assert 'gh release create "$tag"' in _run(release)
    assert '--title "Lattix Locus $VERSION (Dev)"' in _run(release)
    # The rolling channel pointer is the only thing ever replaced.
    channel = next(s for s in steps if s.get("name") == "Advance the Dev channel")
    assert "gh release upload channel-dev assets/latest.json --clobber" in _run(channel)


def test_promote_validates_the_patch_counter_version() -> None:
    wf = _load("desktop-promote.yml")
    validate = next(
        s for s in _steps(wf["jobs"]["promote"]) if s.get("name") == "Validate the version"
    )
    body = _run(validate)
    assert r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]{0,4})$" in body
    assert 'python locus_tooling/versioning.py validate "$VERSION"' in body
    assert 'gh release view "stable-v$VERSION"' in body
    pattern = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]{0,4})$")
    for good in ("0.2.0", "0.2.42", "1.0.99999"):
        assert pattern.match(good)
    for bad in ("0.2.100000", "0.2.01", "0.1.0-dev.42", "v0.2.1", "0.2"):
        assert not pattern.match(bad)


def test_windows_ships_nsis_only_everywhere() -> None:
    # Windows Installer caps the third version field at 65535, below the 99999 PATCH cap.
    conf = json.loads((TAURI / "tauri.conf.json").read_text(encoding="utf-8"))
    targets = conf["bundle"]["targets"]
    assert "msi" not in targets and "nsis" in targets
    for name in ("desktop-dev.yml", "desktop-release.yml"):
        text = (WORKFLOWS / name).read_text(encoding="utf-8")
        assert '"bundles": "nsis"' in text, name
        assert "*.msi" not in text, name


def test_ci_runs_the_release_version_check_from_env_only() -> None:
    wf = _load("ci.yml")
    job = wf["jobs"]["release-version"]
    assert "release-version" in wf["jobs"]["required-gates"]["needs"]
    assert job.get("permissions") is None and wf["permissions"] == {"contents": "read"}
    step = next(s for s in _steps(job) if s.get("name") == "Check VERSION and the release impact")
    body = _run(step)
    assert step["env"]["PR_BODY"] == "${{ github.event.pull_request.body }}"
    assert "${{" not in body  # the untrusted PR body reaches the script only through env
    assert "python locus_tooling/versioning.py check --base-ref 'HEAD^1'" in body
    assert "python locus_tooling/versioning.py check" in body
