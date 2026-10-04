"""LOCUS-351: the candidate instance is separate and secret-free.

Environment scrub (allowlist, not a copy), no keychain, separate app home, its
own telemetry DB, the candidate's code (not the installed one), and the checks
that turn a violation into an error instead of a result. The probe tests start
a real child Python with the scrubbed environment.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from locus_runtime.rsi.candidate import (
    DEAD_PROXY,
    NULL_KEYRING,
    CandidateError,
    CandidateInstance,
    is_secret_like,
)

REPO = Path(__file__).resolve().parents[2]
PARENT_ENV = {
    "PATH": os.environ.get("PATH", ""),
    "SYSTEMROOT": os.environ.get("SYSTEMROOT", "C:\\Windows"),
    "NVIDIA_API_KEY": "nvapi-real-secret",
    "OPENAI_API_KEY": "sk-real",
    "LINEAR_API_KEY": "lin_api_real",
    "GH_TOKEN": "ghp_real",
    "GITHUB_TOKEN": "ghs_real",
    "AWS_SECRET_ACCESS_KEY": "aws-real",
    "LOCUS_API_BEARER_TOKEN": "bearer-real",
    "A2A_JWT_SECRET": "jwt-real",
    "LOCUS_APP_HOME": "C:\\Users\\principal\\AppData\\Local\\Lattix\\Locus",
    "LOCUS_LOOP_HOME": "C:\\Users\\principal\\.locus\\loop",
    "USERPROFILE": "C:\\Users\\principal",
    "HOME": "/home/principal",
    "LOCALAPPDATA": "C:\\Users\\principal\\AppData\\Local",
    "LANGSMITH_TRACING": "true",
    "LANGSMITH_API_KEY": "ls-real",
    "HTTPS_PROXY": "http://corp-proxy:8080",
    "PYTHONPATH": "C:\\installed\\locus",
}


def _instance(
    tmp_path: Path,
    *,
    provider: str = "ollama",
    opa_bin: str = "",
    policy_dir: str = "",
    toolchain_home: str = "",
) -> CandidateInstance:
    return CandidateInstance(
        REPO,
        model_base_url="http://127.0.0.1:55555/v1",
        model="gpt-oss:20b-ctx32k",
        provider=provider,
        opa_bin=opa_bin,
        policy_dir=policy_dir,
        toolchain_home=toolchain_home,
        home=tmp_path / "cand",
        parent_env=PARENT_ENV,
    )


def test_environment_is_an_allowlist_without_secrets(tmp_path: Path) -> None:
    env = _instance(tmp_path, opa_bin="opa.exe", policy_dir="P", toolchain_home="T").environment()
    leaked = [k for k in env if is_secret_like(k)]
    assert leaked == []
    for name in (
        "NVIDIA_API_KEY",
        "OPENAI_API_KEY",
        "LINEAR_API_KEY",
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "AWS_SECRET_ACCESS_KEY",
        "LOCUS_API_BEARER_TOKEN",
        "A2A_JWT_SECRET",
        "LANGSMITH_API_KEY",
    ):
        assert name not in env, name
    assert all("real" not in value for value in env.values())
    home = (tmp_path / "cand").resolve()
    for name in (
        "LOCUS_APP_HOME",
        "LOCUS_LOOP_HOME",
        "LOCUS_TELEMETRY_DB",
        "HOME",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
        "TEMP",
        "TMP",
    ):
        assert Path(env[name]).resolve().is_relative_to(home), name
    assert env["PYTHONPATH"] == str(REPO)
    assert env["PYTHON_KEYRING_BACKEND"] == NULL_KEYRING
    assert env["HTTPS_PROXY"] == env["HTTP_PROXY"] == env["ALL_PROXY"] == DEAD_PROXY
    assert env["NO_PROXY"] == "127.0.0.1,localhost"
    assert env["LANGSMITH_TRACING"] == "false"
    assert env["OLLAMA_BASE_URL"] == "http://127.0.0.1:55555/v1"
    assert (env["LOCUS_OPA_BIN"], env["LOCUS_POLICY_DIR"], env["LOCUS_TOOLCHAIN_HOME"]) == (
        "opa.exe",
        "P",
        "T",
    )
    assert env["PATH"] == PARENT_ENV["PATH"]


def test_only_keyless_providers_and_real_checkouts(tmp_path: Path) -> None:
    with pytest.raises(CandidateError, match="keyless"):
        _instance(tmp_path, provider="nim")
    with pytest.raises(CandidateError, match="not a Locus checkout"):
        CandidateInstance(tmp_path, model_base_url="http://127.0.0.1:1/v1", model="m")


def _facts(instance: CandidateInstance, **overrides: object) -> dict[str, object]:
    facts: dict[str, object] = {
        "locus_runtime": str(REPO / "locus_runtime"),
        "app_home": str(instance.home / "app"),
        "keyring_backend": "keyring.backends.null",
        "secret_like_env": [],
        "provider_keys": [],
        "secret_store_home": str(instance.home / "localappdata" / "Lattix" / "Locus"),
    }
    facts.update(overrides)
    return facts


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"locus_runtime": "C:/installed/locus_runtime"}, "did not load the candidate's code"),
        ({"app_home": "C:/Users/principal/AppData/Local/Lattix/Locus"}, "own app home"),
        ({"keyring_backend": "keyring.backends.Windows"}, "keychain"),
        ({"secret_like_env": ["NVIDIA_API_KEY"]}, "secret-like"),
        ({"provider_keys": ["nim"]}, "provider keys"),
        ({"secret_store_home": "C:/Users/principal/AppData/Local/Lattix/Locus"}, "secret store"),
    ],
)
def test_isolation_violations_are_errors_not_results(
    tmp_path: Path, override: dict[str, object], message: str
) -> None:
    instance = _instance(tmp_path)
    instance.check_isolation(_facts(instance))
    with pytest.raises(CandidateError, match=message):
        instance.check_isolation(_facts(instance, **override))


def test_probe_runs_the_candidates_code_in_its_own_secret_free_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Secrets in the evaluator's own environment must not reach the child.
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-should-not-leak")
    monkeypatch.setenv("LINEAR_API_KEY", "lin-should-not-leak")
    instance = CandidateInstance(
        REPO,
        model_base_url="http://127.0.0.1:1/v1",
        model="m",
        home=tmp_path / "cand",
        python=sys.executable,
    )
    facts = instance.probe()
    home = (tmp_path / "cand").resolve()
    assert Path(str(facts["locus_runtime"])).resolve() == (REPO / "locus_runtime").resolve()
    assert str(facts["keyring_backend"]).startswith("keyring.backends.null")
    assert facts["secret_like_env"] == []
    assert facts["provider_keys"] == []
    assert Path(str(facts["app_home"])).resolve() == home / "app"
    assert Path(str(facts["home"])).resolve().is_relative_to(home)
    assert Path(str(facts["telemetry_db"])).resolve() == home / "telemetry.db"
    assert Path(str(facts["secret_store_home"])).resolve().is_relative_to(home)


def test_probe_loads_a_different_checkout_when_given_one(tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    (checkout / "locus_runtime").mkdir(parents=True)
    (checkout / "locus_runtime" / "__init__.py").write_text("CANDIDATE = True\n", encoding="utf-8")
    (checkout / "locus_runtime" / "model_client.py").write_text(
        "PROVIDERS = {}\n\n\nclass ProviderKeyStore:\n"
        "    def configured(self, name):\n        return False\n",
        encoding="utf-8",
    )
    (checkout / "locus_tooling").mkdir()
    (checkout / "locus_tooling" / "__init__.py").write_text("", encoding="utf-8")
    (checkout / "locus_tooling" / "common.py").write_text(
        "import os\nfrom pathlib import Path\n\n\ndef default_app_home():\n"
        "    return Path(os.environ['LOCALAPPDATA']) / 'Lattix' / 'Locus'\n",
        encoding="utf-8",
    )
    instance = CandidateInstance(
        checkout,
        model_base_url="http://127.0.0.1:1/v1",
        model="m",
        home=tmp_path / "cand",
        python=sys.executable,
    )
    facts = instance.probe()
    assert Path(str(facts["locus_runtime"])).resolve() == (checkout / "locus_runtime").resolve()


def test_close_removes_only_an_owned_home(tmp_path: Path) -> None:
    owned = CandidateInstance(REPO, model_base_url="http://127.0.0.1:1/v1", model="m")
    owned_home = owned.home
    assert owned_home.is_dir()
    owned.close()
    assert not owned_home.exists()
    given = _instance(tmp_path)
    given.close()
    assert (tmp_path / "cand").is_dir()
