"""Out-of-band shell confirmation (LOCUS-350): proofs, and the stdin secret hand-off.

The secret must never reach the environment, argv, logs or a child process:
the hand-off test runs a real child that receives the secret on stdin (as the
frozen backend does from the Tauri shell) and then spawns a grandchild with
default handle inheritance, and checks the grandchild's stdin is not the
shell's pipe and that nothing it can see carries the secret.
"""

from __future__ import annotations

import io
import json
import os
import secrets
import subprocess
import sys
import textwrap
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from locus_tooling import shell_confirmation as sc

REPO = Path(__file__).resolve().parents[2]
SECRET = bytes(range(32))


@pytest.fixture(autouse=True)
def clean_secret() -> Iterator[None]:
    sc.install_secret(None)
    try:
        yield
    finally:
        sc.install_secret(None)


def _tier_msg(**overrides: object):  # type: ignore[no-untyped-def]
    fields: dict[str, object] = {
        "tier": "trusted",
        "allowlisted_sites": ["example.com"],
        "granted_sites": ["mail.example.com"],
    }
    fields.update(overrides)
    return lambda nonce, ts: sc.tier_message(nonce=nonce, timestamp=ts, **fields)  # type: ignore[arg-type]


def _header(message_for, *, nonce: str | None = None, ts: int | None = None) -> str:  # type: ignore[no-untyped-def]
    return sc.proof_header(
        SECRET,
        message_for,
        nonce=nonce or secrets.token_hex(16),
        timestamp=int(time.time()) if ts is None else ts,
    )


def test_canonical_messages_match_the_shell_format() -> None:
    assert (
        sc.tier_message(
            tier="trusted",
            allowlisted_sites=["a.com", "b.com"],
            granted_sites=[],
            nonce="ab" * 16,
            timestamp=1700000000,
        )
        == "locus-shell-proof/v1|browser-tier|trusted|a.com,b.com||" + "ab" * 16 + "|1700000000"
    )
    assert sc.pairing_message(nonce="cd" * 16, timestamp=5) == (
        "locus-shell-proof/v1|browser-pair|" + "cd" * 16 + "|5"
    )
    with pytest.raises(sc.ShellProofError):
        sc.tier_message(
            tier="open", allowlisted_sites=["a.com|x"], granted_sites=[], nonce="n", timestamp=1
        )


def test_valid_proof_is_accepted_once() -> None:
    sc.install_secret(SECRET)
    header = _header(_tier_msg())
    sc.verify(header, _tier_msg())
    with pytest.raises(sc.ShellProofError) as replay:
        sc.verify(header, _tier_msg())
    assert replay.value.code == "replayed_proof"


@pytest.mark.parametrize(
    ("header_for", "code"),
    [
        (lambda: None, "missing_proof"),
        (lambda: "", "missing_proof"),
        (lambda: "v2:1:aa:bb", "malformed_proof"),
        (lambda: "v1:notatime:" + "a" * 32 + ":" + "b" * 64, "malformed_proof"),
        # Signed for a different tier / different sites: bound to the request.
        (lambda: _header(_tier_msg(tier="open")), "bad_proof"),
        (lambda: _header(_tier_msg(granted_sites=["bank.com"])), "bad_proof"),
        # Signed with another key.
        (
            lambda: sc.proof_header(
                b"\x01" * 32, _tier_msg(), nonce="ab" * 16, timestamp=int(time.time())
            ),
            "bad_proof",
        ),
        (lambda: _header(_tier_msg(), ts=int(time.time()) - sc.MAX_SKEW_S - 5), "expired_proof"),
        (lambda: _header(_tier_msg(), ts=int(time.time()) + sc.MAX_SKEW_S + 5), "expired_proof"),
    ],
)
def test_invalid_proofs_are_refused(header_for, code: str) -> None:  # type: ignore[no-untyped-def]
    sc.install_secret(SECRET)
    with pytest.raises(sc.ShellProofError) as refused:
        sc.verify(header_for(), _tier_msg())
    assert refused.value.code == code


def test_no_secret_means_no_proof_can_pass() -> None:
    with pytest.raises(sc.ShellProofError) as refused:
        sc.verify(_header(_tier_msg()), _tier_msg())
    assert refused.value.code == "no_shell"
    assert not sc.secret_installed()
    with pytest.raises(ValueError):
        sc.install_secret(b"short")


# --------------------------------------------------------------------------- #
# Generic request-bound proofs (LOCUS-357)
# --------------------------------------------------------------------------- #
APPROVE = "/workflow-runs/r1/escalations/e1/approve"
VECTOR = {"scope": "run", "pin": False, "note": 'café \n\t"q"\x01', "n": [3, {"b": None, "a": -1}]}


def _request_header(
    action: str, method: str, path: str, body: bytes | str, *, ts: int | None = None
) -> str:
    digest = sc.request_digest(method, path, body)
    return _header(
        lambda nonce, stamp: sc.action_message(
            action=action, digest=digest, nonce=nonce, timestamp=stamp
        ),
        ts=ts,
    )


def test_canonical_body_and_digest_known_answers() -> None:
    """The Tauri shell (shell_actions.rs) builds the same text: sorted keys,
    compact separators, non-ASCII kept, serde_json's string escapes."""
    canonical = sc.canonical_body(json.dumps(VECTOR))
    assert canonical == (
        '{"n":[3,{"a":-1,"b":null}],"note":"café \\n\\t\\"q\\"\\u0001","pin":false,"scope":"run"}'
    )
    # Key order, whitespace and escaping in the request do not matter.
    assert sc.canonical_body(json.dumps(VECTOR, indent=2, ensure_ascii=True)) == canonical
    assert sc.canonical_body(b"") == sc.canonical_body(None) == sc.canonical_body(b"  ") == ""
    assert sc.canonical_body("{}") == "{}"
    assert (
        sc.request_digest("POST", APPROVE, json.dumps(VECTOR))
        == "bddb08be579eb465c4f780b45cdc8511c121aeacd46cc00660faf6427ccd1061"
    )
    assert (
        sc.request_digest("post", "/computer-use/reset", b"")
        == "025e98a868f4eab1dfd6745f1958dcc8bd9a833a76e86afcae6b383dfb04f2ba"
    )
    assert sc.action_message(
        action="computer_use.reset", digest="ab" * 32, nonce="n", timestamp=7
    ) == ("locus-shell-proof/v1|computer_use.reset|" + "ab" * 32 + "|n|7")


@pytest.mark.parametrize(
    "body", [b"{not json", b'{"x": 1.5}', b'{"x": NaN}', b'"\\ud800"', b"\xff\xfe"]
)
def test_unsupported_bodies_are_refused(body: bytes) -> None:
    with pytest.raises(sc.ShellProofError) as refused:
        sc.canonical_body(body)
    assert refused.value.code == "unsupported_body"


@pytest.mark.parametrize(
    ("method", "path"), [("GET", "/x"), ("POST", "x"), ("POST", "/x\n/y"), ("TRACE", "/x")]
)
def test_unsupported_requests_are_refused(method: str, path: str) -> None:
    with pytest.raises(sc.ShellProofError):
        sc.request_digest(method, path, b"")


def test_request_proof_is_bound_to_action_method_path_and_body() -> None:
    sc.install_secret(SECRET)
    action = "workflow.run.escalations.approve"
    body = json.dumps({"scope": "once"})
    header = _request_header(action, "POST", APPROVE, body)
    for other in (
        ("computer_use.reset", "POST", APPROVE, body),
        (action, "PUT", APPROVE, body),
        (action, "POST", APPROVE.replace("e1", "e2"), body),
        (action, "POST", APPROVE, json.dumps({"scope": "standing"})),
        (action, "POST", APPROVE + "?scope=standing", body),
    ):
        with pytest.raises(sc.ShellProofError) as refused:
            sc.verify_request(
                header, action=other[0], method=other[1], path=other[2], body=other[3]
            )
        assert refused.value.code == "bad_proof", other
    # The same request with keys reordered / re-spaced is the same request.
    sc.verify_request(
        header, action=action, method="POST", path=APPROVE, body='{ "scope" : "once" }'
    )
    with pytest.raises(sc.ShellProofError) as replay:
        sc.verify_request(header, action=action, method="POST", path=APPROVE, body=body)
    assert replay.value.code == "replayed_proof"


def test_request_proofs_and_browser_proofs_never_cross() -> None:
    sc.install_secret(SECRET)
    reset = {"action": "computer_use.reset", "method": "POST", "path": "/computer-use/reset"}
    pairing = _header(lambda nonce, ts: sc.pairing_message(nonce=nonce, timestamp=ts))
    with pytest.raises(sc.ShellProofError) as refused:
        sc.verify_request(pairing, body=b"", **reset)
    assert refused.value.code == "bad_proof"
    generic = _request_header("computer_use.reset", "POST", "/computer-use/reset", b"")
    with pytest.raises(sc.ShellProofError):
        sc.verify(generic, lambda nonce, ts: sc.pairing_message(nonce=nonce, timestamp=ts))


def test_request_proof_needs_a_shell_secret_and_a_fresh_timestamp() -> None:
    reset = {"action": "computer_use.reset", "method": "POST", "path": "/computer-use/reset"}
    header = _request_header("computer_use.reset", "POST", "/computer-use/reset", b"")
    with pytest.raises(sc.ShellProofError) as refused:
        sc.verify_request(header, body=b"", **reset)
    assert refused.value.code == "no_shell"
    sc.install_secret(SECRET)
    stale = _request_header(
        "computer_use.reset", "POST", "/computer-use/reset", b"", ts=int(time.time()) - 61
    )
    with pytest.raises(sc.ShellProofError) as expired:
        sc.verify_request(stale, body=b"", **reset)
    assert expired.value.code == "expired_proof"


def test_secret_line_parsing_and_repr_never_shows_it() -> None:
    line = sc.SECRET_LINE_PREFIX + SECRET.hex() + "\n"
    assert sc.parse_secret_line(line) == SECRET
    for bad in ("", "locus-shell-secret:v1:zz", "locus-shell-secret:v1:" + "ab" * 8, SECRET.hex()):
        with pytest.raises(ValueError):
            sc.parse_secret_line(bad)
    sc.install_secret(SECRET)
    assert SECRET.hex() not in repr(sc._BOX)  # noqa: SLF001


def test_receive_reads_only_when_the_shell_flag_is_set(monkeypatch: pytest.MonkeyPatch) -> None:
    detached: list[bool] = []
    line = io.StringIO(sc.SECRET_LINE_PREFIX + SECRET.hex() + "\n")
    monkeypatch.delenv(sc.SHELL_CONFIRMATION_ENV, raising=False)
    assert not sc.receive_from_stdin(stream=line, detach=lambda: detached.append(True))
    assert detached == [] and not sc.secret_installed()
    monkeypatch.setenv(sc.SHELL_CONFIRMATION_ENV, "stdin")
    assert sc.receive_from_stdin(stream=line, detach=lambda: detached.append(True))
    assert detached == [True] and sc.secret_installed()
    sc.install_secret(None)
    garbage = io.StringIO("hello\n")
    assert not sc.receive_from_stdin(stream=garbage, detach=lambda: detached.append(True))
    assert detached == [True, True] and not sc.secret_installed()


CHILD = textwrap.dedent(
    """
    import json, subprocess, sys
    sys.path.insert(0, {repo!r})
    from locus_tooling import shell_confirmation as sc
    installed = sc.receive_from_stdin()
    print(json.dumps({{"installed": installed, "argv": sys.argv}}), flush=True)
    grandchild = (
        "import ctypes, json, os, stat, sys\\n"
        "kind = 'other'\\n"
        "if sys.platform == 'win32':\\n"
        "    k = ctypes.WinDLL('kernel32')\\n"
        "    k.GetStdHandle.restype = ctypes.c_void_p\\n"
        "    h = k.GetStdHandle(-10)\\n"
        "    t = k.GetFileType(ctypes.c_void_p(h)) if h not in (None, 0, -1) else 0\\n"
        "    kind = {{2: 'char', 3: 'pipe'}}.get(t, 'other')\\n"
        "else:\\n"
        "    m = os.fstat(0).st_mode\\n"
        "    kind = 'pipe' if stat.S_ISFIFO(m) else ('char' if stat.S_ISCHR(m) else 'other')\\n"
        "data = sys.stdin.read() if kind != 'pipe' and sys.stdin else ''\\n"
        "print(json.dumps({{'stdin_kind': kind, 'stdin': data, 'env': dict(os.environ),"
        " 'argv': sys.argv}}))\\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", grandchild], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, timeout=60,
    )
    print(out.stdout.strip(), flush=True)
    print(out.stderr.strip(), file=sys.stderr, flush=True)
    """
)


def test_secret_never_reaches_env_argv_logs_or_child_processes(tmp_path: Path) -> None:
    secret = secrets.token_bytes(32)
    script = tmp_path / "child.py"
    script.write_text(CHILD.format(repo=str(REPO)), encoding="utf-8")
    env = {**os.environ, sc.SHELL_CONFIRMATION_ENV: "stdin"}
    child = subprocess.Popen(
        [sys.executable, str(script)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        text=True,
    )
    assert child.stdin is not None
    child.stdin.write(sc.SECRET_LINE_PREFIX + secret.hex() + "\n")
    child.stdin.write("CANARY-AFTER-SECRET\n")
    child.stdin.flush()
    try:
        stdout, stderr = child.communicate(timeout=90)
    finally:
        if child.poll() is None:
            child.kill()
    lines = [line for line in stdout.splitlines() if line.strip()]
    own = json.loads(lines[0])
    grandchild = json.loads(lines[1])
    assert own["installed"] is True
    # The grandchild's stdin is the null device, not the shell's pipe.
    assert grandchild["stdin_kind"] != "pipe"
    assert grandchild["stdin"] == ""
    blob = json.dumps(grandchild) + json.dumps(own) + stdout + stderr
    for form in (secret.hex(), secret.hex().upper()):
        assert form not in blob  # not in env, argv, stdin or any log line
    assert grandchild["env"].get(sc.SHELL_CONFIRMATION_ENV) == "stdin"  # only the flag
    assert "CANARY-AFTER-SECRET" not in blob
