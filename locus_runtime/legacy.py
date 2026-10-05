"""Compatibility for installs and data created before the xFrontier -> Locus rename.

Everything here is a one-way read shim: legacy names are accepted on input and
translated to the Locus names; nothing is ever written back under a legacy name.
"""

from __future__ import annotations

import os
import re
from collections.abc import MutableMapping
from pathlib import Path
from typing import Any

LEGACY_ENV_PREFIX = "FRONTIER_"
ENV_PREFIX = "LOCUS_"
_LEGACY_ENV_EXACT = {
    "NEXT_PUBLIC_FRONTIER_ACTOR": "NEXT_PUBLIC_LOCUS_ACTOR",
}

LEGACY_NODE_PREFIX = "frontier/"
NODE_PREFIX = "locus/"
LEGACY_GRAPH_SCHEMA_PREFIX = "frontier-graph/"
GRAPH_SCHEMA_PREFIX = "locus-graph/"

# Postgres/SQLite tables created before the rename, mapped to their current names.
LEGACY_TABLES = {
    "frontier_state_store": "locus_state_store",
    "frontier_audit_events": "locus_audit_events",
    "frontier_long_term_memory": "locus_long_term_memory",
    "frontier_memory_consolidation_queue": "locus_memory_consolidation_queue",
    "frontier_kg_nodes": "locus_kg_nodes",
    "frontier_kg_edges": "locus_kg_edges",
}

_ENV_LINE_KEY = re.compile(r"^(\s*(?:export\s+)?)FRONTIER_", re.MULTILINE)


def _current_env_name(key: str) -> str | None:
    if key in _LEGACY_ENV_EXACT:
        return _LEGACY_ENV_EXACT[key]
    if key.startswith(LEGACY_ENV_PREFIX):
        return ENV_PREFIX + key[len(LEGACY_ENV_PREFIX) :]
    return None


def alias_legacy_env(environ: MutableMapping[str, str] | None = None) -> list[str]:
    """Copy FRONTIER_* variables to their LOCUS_* names when the new name is unset.

    An explicitly set LOCUS_* value always wins. Returns the names that were aliased.
    """
    env = os.environ if environ is None else environ
    aliased: list[str] = []
    for key in list(env):
        current = _current_env_name(key)
        if current and current not in env:
            env[current] = env[key]
            aliased.append(current)
    return aliased


def migrate_env_text(text: str) -> str:
    """Rewrite FRONTIER_* keys in a dotenv file body to LOCUS_* (values untouched)."""
    text = _ENV_LINE_KEY.sub(r"\1LOCUS_", text)
    for legacy, current in _LEGACY_ENV_EXACT.items():
        text = re.sub(rf"^(\s*(?:export\s+)?){legacy}=", rf"\g<1>{current}=", text, flags=re.MULTILINE)
    return text


def normalize_legacy_identifier(value: str) -> str:
    if value.startswith(LEGACY_NODE_PREFIX):
        return NODE_PREFIX + value[len(LEGACY_NODE_PREFIX) :]
    if value.startswith(LEGACY_GRAPH_SCHEMA_PREFIX):
        return GRAPH_SCHEMA_PREFIX + value[len(LEGACY_GRAPH_SCHEMA_PREFIX) :]
    return value


def normalize_legacy_identifiers(payload: Any) -> Any:
    """Return payload with stored `frontier/*` node types and `frontier-graph/*` schemas renamed.

    Only exact-prefix string values are touched, so free text is left alone.
    """
    if isinstance(payload, str):
        return normalize_legacy_identifier(payload)
    if isinstance(payload, list):
        return [normalize_legacy_identifiers(item) for item in payload]
    if isinstance(payload, dict):
        return {key: normalize_legacy_identifiers(value) for key, value in payload.items()}
    return payload


def prefer_existing_path(current: Path, legacy: Path) -> Path:
    """Use a pre-rename location only when it exists and the Locus one does not."""
    if not current.exists() and legacy.exists():
        return legacy
    return current
