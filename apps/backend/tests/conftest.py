"""Backend test fixtures."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def permissive_gateway():
    """Allow-all gateway double for backend suites (LOCUS-332); see tests/gateway_support.py."""
    from tests.gateway_support import AllowAllAuthorizer, installed

    with installed(AllowAllAuthorizer()) as authorizer:
        yield authorizer
