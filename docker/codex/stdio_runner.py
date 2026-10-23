#!/usr/bin/python3
"""Unprivileged, container-only Codex/OpenAI CLI + stdio Ollama HTTP bridge.

Only used as the entrypoint of the digest-pinned Locus Codex OCI image.
Docker networking is NONE. A loopback HTTP server emulates the *transport*
surface for the official Codex --oss / Ollama mode; each actual inference call
crosses a bounded JSONL Docker pipe to the trusted supervisor's Gateway model
broker. No Locus credentials, host Codex configuration, or Docker socket exist
inside the container. No MCP tools or arbitrary host capabilities are exposed.

stdout is a protocol, never Codex stdout or logs. The supervisor must reject
malformed/missing terminal protocol events and separately verify stage outputs.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

SCHEMA = "locus.codex.stdio.v1"
PINNED_CODEX_VERSION = "0.163.0"
_MAX_PROMPT = 48 * 1024
_MAX_MODEL_BYTES = 1024 * 1024
_MAX_REPLY_BYTES = 4 * 1024 * 1024
_MODEL = re.compile(r"[A-Za-z][A-Za-z0-9_.:/-]{0,100}\Z", re.ASCII)
_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,80}\.txt\Z", re.ASCII)
_output_lock = threading.Lock()
_response_lock = threading.Condition()
_response_slots: dict[int, dict[str, object]] = {}
_counter = 0
_closed = False


def emit(frame: dict[str, object]) -> None:
    raw = json.dumps(frame, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    if len(raw) > _MAX_REPLY_BYTES * 4 // 3 + 4096:
        raise RuntimeError("oversized relay response")
    with _output_lock:
        sys.stdout.buffer.write(raw + b"\n")
        sys.stdout.buffer.flush()


def read_frame(raw: bytes) -> dict[str, object]:
    if len(raw) > _MAX_REPLY_BYTES * 4 // 3 + 4096:
        raise ValueError("oversized frame")
    def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
        data: dict[str, object] = {}
        for key, value in pairs:
            if key in data:
                raise ValueError("duplicate wire field")
            data[key] = value
        return data
    frame = json.loads(raw, object_pairs_hook=unique)
    if not isinstance(frame, dict) or frame.get("schema") != SCHEMA:
        raise ValueError("invalid wire version")
    return frame


def receive_replies() -> None:
    global _closed
    while True:
        frame_bytes = sys.stdin.buffer.readline(_MAX_REPLY_BYTES * 4 // 3 + 4097)
        if not frame_bytes:
            break
        try:
            frame = read_frame(frame_bytes)
            ident = frame.get("id")
            if (
                set(frame) != {"schema", "kind", "id", "status", "content_type", "body"}
                or frame["kind"] != "model_reply"
                or type(ident) is not int or ident <= 0
                or type(frame["status"]) is not int or not 100 <= frame["status"] <= 599
                or frame["content_type"] not in {"application/json", "text/event-stream"}
            ):
                raise ValueError("invalid model reply")
            body = frame["body"]
            if not isinstance(body, str) or len(body) > _MAX_REPLY_BYTES * 4 // 3 + 8:
                raise ValueError("bad response size")
            base64.b64decode(body, validate=True)
            with _response_lock:
                if ident not in _response_slots or _response_slots[ident]:
                    raise ValueError("unknown or duplicate reply")
                _response_slots[ident] = frame
                _response_lock.notify_all()
        except (ValueError, TypeError, KeyError):
            break
    with _response_lock:
        _closed = True
        _response_lock.notify_all()


class LocalOllamaHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *_: object) -> None:
        pass

    def do_POST(self) -> None:
        global _counter
        if self.path not in {"/v1/responses", "/v1/chat/completions"}:
            self.send_error(404)
            return
        size = self.headers.get("Content-Length", "")
        if not size.isascii() or not size.isdecimal() or not 0 < int(size) <= _MAX_MODEL_BYTES:
            self.send_error(413)
            return
        body = self.rfile.read(int(size))
        with _response_lock:
            if _closed:
                self.send_error(503)
                return
            _counter += 1
            ident = _counter
            if ident > 16:
                self.send_error(429)
                return
            _response_slots[ident] = {}
        emit({
            "schema": SCHEMA, "kind": "model_request", "id": ident,
            "method": "POST", "path": self.path,
            "body": base64.b64encode(body).decode("ascii"),
        })
        with _response_lock:
            ready = _response_lock.wait_for(
                lambda: _closed or bool(_response_slots.get(ident)), timeout=52,
            )
            reply = _response_slots.pop(ident, None)
            if not ready or not reply:
                self.send_error(503)
                return
        status = reply["status"]
        media = reply["content_type"]
        encoded = reply["body"]
        assert type(status) is int and isinstance(media, str) and isinstance(encoded, str)
        data = base64.b64decode(encoded, validate=True)
        if len(data) > _MAX_REPLY_BYTES:
            self.send_error(502)
            return
        self.send_response(status)
        self.send_header("Content-Type", media)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)
        self.wfile.flush()
        self.close_connection = True

    def do_GET(self) -> None:
        # No unmediated GET to the host. A fixed local health response is the
        # only discovery capability; model calls still require the supervisor.
        if self.path != "/":
            self.send_error(404)
            return
        data = b'{"status":"ok"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main() -> int:
    raw = sys.stdin.buffer.readline(_MAX_PROMPT + 2049)
    try:
        init = read_frame(raw)
        if set(init) != {"schema", "kind", "model", "prompt", "output", "version"}:
            raise ValueError("unexpected launch fields")
        if init["kind"] != "init" or init["version"] != PINNED_CODEX_VERSION:
            raise ValueError("CLI version pin mismatch")
        model, prompt, output = init["model"], init["prompt"], init["output"]
        if (
            not isinstance(model, str) or _MODEL.fullmatch(model) is None
            or not isinstance(prompt, str) or not 0 < len(prompt.encode("utf-8")) <= _MAX_PROMPT
            or not isinstance(output, str) or _NAME.fullmatch(output) is None
        ):
            raise ValueError("bounded model/task/artifact required")
        version = subprocess.run(
            ["/usr/local/bin/codex", "--version"], check=True, capture_output=True,
            timeout=5, env={"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/tmp"},
        ).stdout.decode("ascii").strip()
        if version != "codex-cli " + PINNED_CODEX_VERSION:
            raise ValueError("unexpected official Codex CLI version")
        output_path = Path("/workspace") / output
        if output_path.exists() or output_path.is_symlink():
            raise ValueError("output must not exist in the stage before launch")
        server = ThreadingHTTPServer(("127.0.0.1", 11434), LocalOllamaHandler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        receiver = threading.Thread(target=receive_replies, daemon=True)
        receiver.start()
        emit({"schema": SCHEMA, "kind": "ready", "version": PINNED_CODEX_VERSION})
        env = {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": "/tmp",
            "CODEX_HOME": "/tmp/locus-codex",
            "OLLAMA_HOST": "http://127.0.0.1:11434",
        }
        Path(env["CODEX_HOME"]).mkdir(mode=0o700, exist_ok=False)
        argv = [
            "/usr/local/bin/codex", "exec", "--json", "--skip-git-repo-check",
            "--ephemeral", "--ignore-user-config", "--cd", "/workspace",
            "--sandbox", "read-only", "--oss", "--local-provider", "ollama",
            "--disable", "shell_tool",
            "--model", model, "-o", str(output_path),
            "-c", 'web_search="disabled"',
            "-",
        ]
        stream_digest = hashlib.sha256()
        events = 0
        tool_seen = False
        proc = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, cwd="/workspace", shell=False,
            close_fds=True, env=env,
        )
        assert proc.stdin is not None and proc.stdout is not None
        proc.stdin.write(prompt.encode("utf-8"))
        proc.stdin.close()
        # Codex stdout is untrusted. Its content is digested in memory only,
        # never placed into Docker stdout or an audit record.
        for line in iter(proc.stdout.readline, b""):
            if len(line) > 131072:
                proc.kill()
                break
            stream_digest.update(line)
            events += 1
            if events > 8192:
                proc.kill()
                break
            try:
                event = json.loads(line)
                if event.get("type") == "item.completed":
                    item = event.get("item")
                    if isinstance(item, dict) and item.get("type") in {
                        "command_execution", "mcp_tool_call", "file_change",
                    }:
                        tool_seen = True
            except (ValueError, TypeError, AttributeError):
                proc.kill()
                break
        code = proc.wait(timeout=5)
        server.shutdown()
        if code != 0 or tool_seen or not output_path.is_file() or output_path.is_symlink():
            emit({"schema": SCHEMA, "kind": "done", "ok": False, "events": events})
            return 3
        data = output_path.read_bytes()
        if not 0 < len(data) <= 65536:
            emit({"schema": SCHEMA, "kind": "done", "ok": False, "events": events})
            return 4
        emit({
            "schema": SCHEMA, "kind": "done", "ok": True,
            "events": events, "sha256": hashlib.sha256(data).hexdigest(),
            "stdout_sha256": stream_digest.hexdigest(),
            "bytes": len(data),
        })
        return 0
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        # Never report input, filesystem names, subprocess stderr, or secrets.
        emit({"schema": SCHEMA, "kind": "done", "ok": False, "events": 0})
        return 5


if __name__ == "__main__":
    raise SystemExit(main())
