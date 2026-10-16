"""Policy for the CI required-gates aggregator (LOCUS-455).

Reports optional disabled security scans as NOT EXECUTED. Strict release mode
fails if any security scan was not actually successful. No third-party deps.
"""
from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass

_ALWAYS = frozenset({
    "release-version", "frontend-unit", "backend-unit", "integration",
    "quality-policy-helm", "dast", "performance", "windows-sandbox",
})
_OPTIONAL_FLAGS = {
    "sast-codeql": "CODEQL_ENABLED",
    "lattix-cicd-pentest": "LATTIX_PENTEST_ENABLED",
    "sbom-sca": "LATTIX_PENTEST_ENABLED",
}
_EXPECTED = _ALWAYS | _OPTIONAL_FLAGS.keys()


class GateContractError(ValueError):
    """Required CI job omitted, failed, or improperly skipped."""


@dataclass(frozen=True)
class GateDecision:
    not_executed: tuple[str, ...]


def _enabled(name: str, env: Mapping[str, str]) -> bool:
    # GitHub job expressions compare the literal repository variable to 'true'.
    # Normalizing 1/TRUE/whitespace would disagree with which scanners ran.
    value = env.get(name, "")
    if value in ("", "false"):
        return False
    if value == "true":
        return True
    raise GateContractError(f"invalid CI security toggle: {name}")


def validate_ci_gates(
    needs: Mapping[str, object],
    *,
    env: Mapping[str, str],
) -> GateDecision:
    """Validate COMPLETE GitHub Actions needs result map, never by PR status."""
    if not isinstance(needs, dict) or set(needs) != _EXPECTED:
        raise GateContractError("CI needs contract missing or unexpected jobs")
    strict_release = _enabled("LATTIX_SECURITY_RELEASE_REQUIRED", env)
    enabled = {name: _enabled(flag, env) for name, flag in _OPTIONAL_FLAGS.items()}
    not_executed: list[str] = []
    for name in sorted(_EXPECTED):
        entry = needs[name]
        if not isinstance(entry, dict) or type(entry.get("result")) is not str:
            raise GateContractError(f"{name}: missing CI job result")
        status = entry["result"]
        if status == "success":
            continue
        if (
            status == "skipped"
            and name in _OPTIONAL_FLAGS
            and not enabled[name]
            and not strict_release
        ):
            not_executed.append(name)
            continue
        raise GateContractError(f"{name}: unacceptable status {status}")
    return GateDecision(not_executed=tuple(not_executed))


def main() -> int:
    try:
        needs = json.loads(os.environ["RESULTS"])
        result = validate_ci_gates(needs, env=os.environ)
    except (KeyError, ValueError, TypeError) as exc:
        print(f"CI required-gates DENIED: {exc}", file=sys.stderr)
        return 1
    if result.not_executed:
        print(
            "CI REQUIRED BASELINE PASSED; SECURITY SCANS NOT EXECUTED: "
            + ", ".join(result.not_executed)
            + ". Full production/security release is NOT ATTESTED."
        )
    else:
        print("All configured CI gates executed successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
