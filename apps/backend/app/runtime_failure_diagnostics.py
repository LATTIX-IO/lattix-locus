"""Safe, durable diagnostics for failed operator runs.

Locus previously synthesized a blocked "Agent Execution Failure Report" during
GET /workflow-runs/{id} and claimed provider/auth details had been captured,
even for an arbitrary exception. It stored no report body.

This module carries only bounded, structural exception metadata. Never persist
str(exc), raw tracebacks, provider secrets, user prompts or exception args.
"""
from __future__ import annotations

import json
import re
from pathlib import PurePath
from typing import Any


_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,79}$")
_SAFE_BASENAME = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
_REPORT_SCHEMA = "locus/runtime-failure/v1"
HISTORICAL_UNRECORDED_MESSAGE = (
    "The run failed before structured failure diagnostics were recorded. "
    "The original exception was not retained; retry after installing a "
    "diagnostics-enabled build. A provider/auth cause has not been established."
)


def _safe_identifier(value: str) -> str:
    return value if _SAFE_IDENTIFIER.fullmatch(value) else "UnclassifiedError"


def describe_unexpected_failure(exc: Exception, *, run_id: str) -> dict[str, str]:
    """Return safe failure facts independent of untrusted exception text."""
    if isinstance(exc, TimeoutError):
        code, message = "execution_timeout", "Execution timed out."
    elif isinstance(exc, PermissionError):
        code, message = "execution_permission_denied", "Execution permission was denied."
    elif isinstance(exc, ConnectionError):
        code, message = "execution_dependency_unreachable", "Execution dependency unavailable."
    else:
        code, message = "execution_unhandled_exception", "Runtime execution failed."

    exception_type = _safe_identifier(type(exc).__name__)
    # Only filename, function and line. No raw traceback or absolute paths.
    traceback = exc.__traceback__
    while traceback is not None and traceback.tb_next is not None:
        traceback = traceback.tb_next
    site = "unavailable"
    if traceback is not None:
        code_obj = traceback.tb_frame.f_code
        filename = str(code_obj.co_filename).replace("\\", "/").rsplit("/", 1)[-1]
        function = str(code_obj.co_name)
        line = traceback.tb_lineno
        if (
            _SAFE_BASENAME.fullmatch(filename)
            and _SAFE_IDENTIFIER.fullmatch(function)
            and type(line) is int
            and 0 < line < 1000000
        ):
            site = f"{filename}:{function}:{line}"

    # The run identifier is already server-authorized and used to retrieve
    # the report; it is the correlation reference for support.
    return {
        "code": code,
        "message": message,
        "exception_type": exception_type,
        "failure_site": site,
        "run_id": run_id,
    }


def make_failure_report(error: dict[str, Any]) -> str:
    """A stable report body that cannot accidentally contain exception args."""
    allowed: dict[str, str] = {}
    for key in ("run_id", "code", "message", "exception_type", "failure_site"):
        value = error.get(key)
        if isinstance(value, str) and 0 < len(value) <= 180:
            allowed[key] = value
    if "code" not in allowed:
        allowed["code"] = "unclassified_runtime_error"
    if "message" not in allowed:
        allowed["message"] = "Runtime execution failed."
    return json.dumps(
        {"schema_version": _REPORT_SCHEMA, "error": allowed},
        ensure_ascii=True, sort_keys=True, separators=(",", ":"),
    )


def historical_failure_report(*, run_id: str) -> str:
    """Explicitly state lost evidence instead of manufacturing provider detail."""
    return make_failure_report({
        "run_id": run_id,
        "code": "historical_failure_details_unavailable",
        "message": HISTORICAL_UNRECORDED_MESSAGE,
    })
