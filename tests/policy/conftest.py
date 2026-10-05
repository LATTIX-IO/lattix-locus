"""Fixtures for tests that need a real OPA binary (LOCUS-328).

Locally these skip when no OPA binary is found (``LOCUS_OPA_BIN``, repo
``.tools/opa``, native bin dir, PATH). CI sets ``LOCUS_REQUIRE_OPA=1`` so a
missing binary fails the job instead of silently skipping the parity suite.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

from locus_runtime.policy_engine import OpaSidecarEngine, find_opa_binary


@pytest.fixture(scope="session")
def opa_engine() -> Iterator[OpaSidecarEngine]:
    binary = find_opa_binary()
    if binary is None:
        if str(os.getenv("LOCUS_REQUIRE_OPA") or "").strip() == "1":
            pytest.fail("LOCUS_REQUIRE_OPA=1 but no OPA binary was found")
        pytest.skip("OPA binary not available (set LOCUS_OPA_BIN or put opa on PATH)")
    engine = OpaSidecarEngine(opa_binary=binary, timeout_seconds=5.0)
    engine.start()
    try:
        yield engine
    finally:
        engine.close()
