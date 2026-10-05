"""LOCUS-379: the stdio bridge between the jailed candidate and its parent.

Framing, the request/response client (concurrent calls, closed bridge, refused
requests), the httpx transport (the candidate's OPA engine and model client),
fail-closed policy decisions when the bridge is gone, and the parent's request
validation for every op (http channels and paths, exec, git).
"""

from __future__ import annotations

import io
import json
import os
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from locus_runtime.rsi import bridge as br
from locus_runtime.rsi.candidate import CandidateInstance

REPO = Path(__file__).resolve().parents[2]


class _Pair:
    """Two OS pipes: parent <-> child, like the stdio of the jailed process."""

    def __init__(self) -> None:
        c_read, p_write = os.pipe()
        p_read, c_write = os.pipe()
        self.child_in = os.fdopen(c_read, "rb", buffering=0)
        self.parent_out = os.fdopen(p_write, "wb", buffering=0)
        self.parent_in = os.fdopen(p_read, "rb", buffering=0)
        self.child_out = os.fdopen(c_write, "wb", buffering=0)

    def close(self) -> None:
        # Write ends first: each blocked reader then sees EOF and returns (closing a
        # pipe another thread is reading can block on Windows).
        for fh in (self.parent_out, self.child_out, self.child_in, self.parent_in):
            try:
                fh.close()
            except OSError:
                pass


@pytest.fixture
def served() -> Iterator[tuple[br.BridgeClient, br.BridgeServer, _Pair]]:
    pair = _Pair()

    def echo(payload: dict[str, Any]) -> dict[str, Any]:
        return {"echo": payload}

    def slow(payload: dict[str, Any]) -> dict[str, Any]:
        time.sleep(float(payload.get("seconds") or 0.3))
        return {"slow": True}

    def refuse(_payload: dict[str, Any]) -> dict[str, Any]:
        raise br.BridgeError("not allowed here")

    def crash(_payload: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("secret detail must not leak")

    server = br.BridgeServer(
        pair.parent_in,
        pair.parent_out,
        {"echo": echo, "slow": slow, "refuse": refuse, "crash": crash},
    ).start()
    client = br.BridgeClient(pair.child_in, pair.child_out)
    yield client, server, pair
    pair.close()
    server.join(5.0)


def test_frames_round_trip_and_reject_oversize() -> None:
    frame = br.encode_frame({"id": 1, "op": "x", "payload": {"a": "ü"}})
    assert br.read_frame(io.BytesIO(frame)) == {"id": 1, "op": "x", "payload": {"a": "ü"}}
    assert br.read_frame(io.BytesIO(b"")) is None
    with pytest.raises(br.BridgeError, match="truncated"):
        br.read_frame(io.BytesIO(frame[:-2]))
    huge = (br.MAX_FRAME_BYTES + 1).to_bytes(4, "big")
    with pytest.raises(br.BridgeError, match="too large"):
        br.read_frame(io.BytesIO(huge))
    with pytest.raises(br.BridgeError, match="malformed"):
        br.read_frame(io.BytesIO((3).to_bytes(4, "big") + b"{x}"))
    with pytest.raises(br.BridgeError, match="not an object"):
        br.read_frame(io.BytesIO((2).to_bytes(4, "big") + b"[]"))


def test_calls_and_refusals(served: tuple[br.BridgeClient, br.BridgeServer, _Pair]) -> None:
    client, server, _pair = served
    assert client.call("echo", {"x": 1}) == {"echo": {"x": 1}}
    with pytest.raises(br.BridgeError, match="not allowed here"):
        client.call("refuse")
    with pytest.raises(br.BridgeError, match=r"crash failed \(RuntimeError\)") as info:
        client.call("crash")
    assert "secret detail" not in str(info.value)
    with pytest.raises(br.BridgeError, match="unknown op"):
        client.call("spawn_shell")
    assert server.counts == {"echo": 1, "refuse": 1, "crash": 1}
    assert server.refused == 2


def test_a_slow_call_does_not_block_others(
    served: tuple[br.BridgeClient, br.BridgeServer, _Pair],
) -> None:
    client, _server, _pair = served
    done: list[float] = []

    def slow() -> None:
        client.call("slow", {"seconds": 1.0})
        done.append(time.monotonic())

    thread = threading.Thread(target=slow)
    thread.start()
    time.sleep(0.1)
    started = time.monotonic()
    assert client.call("echo", {"fast": True}) == {"echo": {"fast": True}}
    assert time.monotonic() - started < 0.8  # answered while the slow call is pending
    thread.join(5.0)
    assert done


def test_a_closed_bridge_fails_every_call(
    served: tuple[br.BridgeClient, br.BridgeServer, _Pair],
) -> None:
    client, server, pair = served
    pair.parent_out.close()  # the parent went away
    with pytest.raises(br.BridgeError):
        client.call("echo", {}, timeout=5.0)
    with pytest.raises(br.BridgeError):
        client.call("echo", {}, timeout=5.0)


def test_a_malformed_request_stops_the_server() -> None:
    pair = _Pair()
    server = br.BridgeServer(pair.parent_in, pair.parent_out, {}).start()
    pair.child_out.write((5).to_bytes(4, "big") + b"nope!")
    pair.child_out.flush()
    for _ in range(50):
        if server.error:
            break
        time.sleep(0.1)
    assert "malformed" in server.error
    pair.close()


# --------------------------------------------------------------------------- #
# httpx transport: the candidate's OPA engine and model client
# --------------------------------------------------------------------------- #
def _opa_handler(seen: list[dict[str, Any]]) -> Any:
    def http(payload: dict[str, Any]) -> dict[str, Any]:
        seen.append(payload)
        path = payload["path"]
        if path == "/health":
            body: Any = {}
        elif path == "/v1/policies":
            body = {"result": [{"raw": "package lattix.x"}]}
        else:
            body = {"result": {"allow": True}}
        return {
            "status": 200,
            "content_type": "application/json",
            "body": br.b64encode(json.dumps(body).encode()),
        }

    return http


def test_the_candidates_opa_engine_decides_over_the_bridge() -> None:
    from locus_runtime.policy_engine import OpaSidecarEngine

    pair = _Pair()
    seen: list[dict[str, Any]] = []
    server = br.BridgeServer(pair.parent_in, pair.parent_out, {"http": _opa_handler(seen)}).start()
    client = br.BridgeClient(pair.child_in, pair.child_out)
    engine = OpaSidecarEngine(
        base_url="http://127.0.0.1:8181",
        transport=br.http_transport(client, "opa", timeout=10.0),
        timeout_seconds=5.0,
    )
    engine.start()
    decision = engine.decide("tool_jail", {"x": 1})
    assert decision.allow is True and decision.backend == "opa-sidecar"
    assert {s["channel"] for s in seen} == {"opa"}
    assert seen[-1]["method"] == "POST" and seen[-1]["path"] == "/v1/data/lattix/tool_jail"
    assert "authorization" not in seen[-1]["headers"]
    # The parent goes away: every later decision is a deny (fail closed).
    pair.parent_out.close()
    denied = engine.decide("tool_jail", {"x": 1})
    assert denied.allow is False and denied.reasons[0] == "policy_engine_unavailable"
    engine.close()
    pair.close()
    server.join(5.0)


def test_model_requests_cross_the_bridge_without_auth_headers() -> None:
    pair = _Pair()
    seen: list[dict[str, Any]] = []

    def http(payload: dict[str, Any]) -> dict[str, Any]:
        seen.append(payload)
        return {
            "status": 200,
            "content_type": "application/json",
            "body": br.b64encode(b'{"ok": true}'),
        }

    server = br.BridgeServer(pair.parent_in, pair.parent_out, {"http": http}).start()
    client = br.BridgeClient(pair.child_in, pair.child_out)
    with httpx.Client(transport=br.http_transport(client, "model")) as http_client:
        response = http_client.post(
            "http://127.0.0.1:1/v1/chat/completions?x=1",
            json={"model": "m"},
            headers={"Authorization": "Bearer sk-real", "X-Other": "1"},
        )
    assert response.json() == {"ok": True}
    assert seen[0]["channel"] == "model" and seen[0]["path"] == "/v1/chat/completions?x=1"
    assert set(seen[0]["headers"]) == {"content-type", "accept"}
    assert json.loads(br.b64decode(seen[0]["body"])) == {"model": "m"}
    pair.close()
    server.join(5.0)


# --------------------------------------------------------------------------- #
# The parent's validation of each op
# --------------------------------------------------------------------------- #
@pytest.fixture
def parent(tmp_path: Path) -> Iterator[CandidateInstance]:
    instance = CandidateInstance(
        REPO,
        model_base_url="http://127.0.0.1:4321/v1",
        model="m",
        home=tmp_path / "cand",
        isolation="bwrap",
    )
    yield instance
    instance.close()


def test_http_channels_reach_only_their_upstream(parent: CandidateInstance) -> None:
    calls: list[str] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json={"usage": {}})

    parent._http = httpx.Client(transport=httpx.MockTransport(upstream))  # noqa: SLF001
    ok = parent._http_call(  # noqa: SLF001
        {"channel": "model", "method": "POST", "path": "/v1/chat/completions", "body": ""}
    )
    assert ok["status"] == 200 and calls == ["http://127.0.0.1:4321/v1/chat/completions"]
    for payload in (
        {"channel": "model", "method": "POST", "path": "/api/pull"},
        {"channel": "model", "method": "DELETE", "path": "/v1/models"},
        {"channel": "model", "method": "POST", "path": "/v1/chat/completions/../../admin"},
        {"channel": "opa", "method": "PUT", "path": "/v1/policies/x"},
        {"channel": "opa", "method": "POST", "path": "/v1/data/system/main"},
        {"channel": "opa", "method": "GET", "path": "/v1/data/lattix/tool_jail?x"},
        {"channel": "file", "method": "GET", "path": "/etc/passwd"},
    ):
        with pytest.raises(br.BridgeError, match="not allowed|unknown"):
            parent._http_call({**payload, "body": ""})  # noqa: SLF001
    assert len(calls) == 1


def test_exec_and_git_need_a_bound_workspace_and_well_formed_input(
    parent: CandidateInstance, tmp_path: Path
) -> None:
    with pytest.raises(br.BridgeError, match="no workspace"):
        parent._exec({"command": ["sh", "-c", "true"]})  # noqa: SLF001
    with pytest.raises(br.BridgeError, match="no workspace"):
        parent._git({"call": "diff", "base": "HEAD", "pathspecs": []})  # noqa: SLF001
    parent._workspace = tmp_path  # noqa: SLF001
    for command in ([], "sh -c true", ["sh", 3], ["a\0b"], ["x"] * 513, ["x" * 300_000]):
        with pytest.raises(br.BridgeError, match="malformed command"):
            parent._exec({"command": command})  # noqa: SLF001
    with pytest.raises(br.BridgeError, match="malformed timeout"):
        parent._exec({"command": ["true"], "timeout": "soon"})  # noqa: SLF001
    for payload in (
        {"call": "diff", "base": 3, "pathspecs": []},
        {"call": "diff", "base": "HEAD", "pathspecs": "x"},
        {"call": "diff", "base": "HEAD", "pathspecs": ["x"] * 65},
    ):
        with pytest.raises(br.BridgeError, match="malformed"):
            parent._git(payload)  # noqa: SLF001
    with pytest.raises(br.BridgeError, match="unknown call"):
        parent._git({"call": "push"})  # noqa: SLF001


def test_host_git_runs_in_the_parent_and_refuses_options(
    parent: CandidateInstance, tmp_path: Path
) -> None:
    import subprocess

    ws = tmp_path / "ws"
    ws.mkdir()
    for args in (
        ["init", "-q"],
        ["-c", "user.email=a@b", "-c", "user.name=a", "commit", "-q", "--allow-empty", "-m", "x"],
    ):
        subprocess.run(["git", "-C", str(ws), *args], check=True, capture_output=True)
    (ws / "a.txt").write_text("hello\n", encoding="utf-8")
    parent._workspace = ws  # noqa: SLF001
    diff = parent._git({"call": "diff", "base": "HEAD", "pathspecs": []})  # noqa: SLF001
    assert "+hello" in diff["diff"]
    with pytest.raises(br.BridgeError, match="host git refused"):
        parent._git({"call": "diff", "base": "--output=/tmp/x", "pathspecs": []})  # noqa: SLF001
