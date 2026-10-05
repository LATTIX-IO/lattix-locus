"""Desktop shutdown must close the owned OPA gateway before the process exits."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("A2A_JWT_SECRET", "unit-test-super-secret-value-32bytes")
os.environ.setdefault("LOCUS_API_BEARER_TOKEN", "unit-test-bearer")

import app.main as main_module
from locus_tooling import desktop


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/system/shutdown",
            "headers": [],
            "query_string": b"",
        }
    )


def test_desktop_shutdown_runtime_closes_gateway_before_supervisors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    monkeypatch.setattr(
        "locus_runtime.gateway.close_installed_gateway", lambda: events.append("gateway")
    )
    monkeypatch.setattr(desktop, "shutdown_supervisors", lambda: events.append("supervisors"))

    desktop.shutdown_runtime()

    assert events == ["gateway", "supervisors"]


def test_desktop_shutdown_runtime_does_not_stop_parent_after_gateway_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    def fail_to_close() -> None:
        events.append("gateway")
        raise OSError("OPA sidecar did not stop")

    monkeypatch.setattr("locus_runtime.gateway.close_installed_gateway", fail_to_close)
    monkeypatch.setattr(desktop, "shutdown_supervisors", lambda: events.append("supervisors"))

    with pytest.raises(OSError, match="OPA sidecar did not stop"):
        desktop.shutdown_runtime()

    assert events == ["gateway"]


def test_system_shutdown_closes_gateway_before_supervisors_and_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class Timer:
        def __init__(self, interval: float, callback: object) -> None:
            self.interval = interval
            self.callback = callback

        def start(self) -> None:
            events.append("exit_timer")

    monkeypatch.setattr(
        main_module, "_active_runtime_profile", lambda: SimpleNamespace(name="local-native")
    )
    monkeypatch.setattr(main_module.threading, "Timer", Timer)
    monkeypatch.setattr("locus_tooling.desktop.shutdown_runtime", lambda: events.append("cleanup"))

    response = main_module.system_shutdown(_request())

    assert response.body == b'{"ok":true,"shutting_down":true}'
    assert events == ["cleanup", "exit_timer"]


def test_system_shutdown_refuses_to_exit_when_gateway_cannot_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class Timer:
        def __init__(self, *_args: object) -> None:
            raise AssertionError("shutdown must not schedule exit after cleanup failure")

    monkeypatch.setattr(
        main_module, "_active_runtime_profile", lambda: SimpleNamespace(name="local-native")
    )
    monkeypatch.setattr(main_module.threading, "Timer", Timer)

    def fail_to_close() -> None:
        events.append("gateway")
        raise OSError("OPA sidecar did not stop")

    monkeypatch.setattr("locus_tooling.desktop.shutdown_runtime", fail_to_close)

    with pytest.raises(HTTPException) as error:
        main_module.system_shutdown(_request())

    assert error.value.status_code == 503
    assert events == ["gateway"]
