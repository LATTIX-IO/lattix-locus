from __future__ import annotations

import os
from pathlib import Path


def workers_root() -> Path:
    return Path(__file__).resolve().parents[1]


def repo_root() -> Path:
    return workers_root().parents[1]


def runtime_root() -> Path:
    return Path(__file__).resolve().parent


def configured_agent_assets_root() -> Path | None:
    configured = str(os.getenv("LOCUS_AGENT_ASSETS_ROOT") or "").strip()
    if not configured:
        return None

    candidate = Path(configured)
    if not candidate.is_absolute():
        candidate = repo_root() / candidate
    return candidate.resolve()


def agent_assets_roots() -> list[Path]:
    roots: list[Path] = []
    examples_root = (repo_root() / "examples" / "agents").resolve()
    configured_root = configured_agent_assets_root()

    for candidate in [examples_root, configured_root]:
        if candidate is None:
            continue
        if candidate not in roots:
            roots.append(candidate)

    return roots


def default_agent_assets_root() -> Path:
    for candidate in agent_assets_roots():
        if candidate.exists() and candidate.is_dir():
            return candidate
    return agent_assets_roots()[0]


def registry_path_candidates() -> list[Path]:
    roots = agent_assets_roots()
    candidates: list[Path] = []
    for root in roots:
        candidates.append(root / "REGISTRY" / "agents.registry.json")
    return candidates


def default_registry_path() -> Path:
    for candidate in registry_path_candidates():
        if candidate.exists() and candidate.is_file():
            return candidate
    return registry_path_candidates()[0]


def topic_map_path() -> Path:
    return (runtime_root() / "layer2" / "topic_map.json").resolve()


def topic_endpoints_map_path() -> Path:
    return (runtime_root() / "network" / "topic_endpoints.example.json").resolve()
