#!/usr/bin/env python3
from __future__ import annotations
import argparse
import shutil
from pathlib import Path


WORKERS_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = WORKERS_ROOT.parents[1]
TEMPLATE = WORKERS_ROOT / "services" / "AGENT_SERVICE_TEMPLATE"
SERVICES = WORKERS_ROOT / "services"


def _agent_assets_candidates() -> list[Path]:
    configured = str(__import__("os").getenv("LOCUS_AGENT_ASSETS_ROOT") or "").strip()
    candidates = [
        (REPO_ROOT / "examples" / "agents").resolve(),
    ]
    if configured:
        configured_path = Path(configured)
        if not configured_path.is_absolute():
            configured_path = REPO_ROOT / configured_path
        candidates.append(configured_path.resolve())
    return candidates


def _resolve_agent_dir(agent_id: str) -> Path | None:
    for root in _agent_assets_candidates():
        candidate = root / agent_id
        if candidate.exists() and candidate.is_dir():
            return candidate
    return None


def scaffold(agent_id: str) -> Path:
    src = TEMPLATE
    dst = SERVICES / agent_id
    dst.mkdir(parents=True, exist_ok=True)
    # copytree while allowing existing dirs
    for p in src.rglob("*"):
        rel = p.relative_to(src)
        target = dst / rel
        if p.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            if not target.exists():
                if p.name == "README.md":
                    content = p.read_text(encoding="utf-8").replace("agent-service", agent_id)
                    target.write_text(content, encoding="utf-8")
                else:
                    shutil.copy2(p, target)
    return dst


def main() -> None:
    ap = argparse.ArgumentParser(description="Scaffold a containerized service for an agent")
    ap.add_argument(
        "agent_id", help="Agent folder name under examples/agents or private agent assets"
    )
    args = ap.parse_args()
    agent_dir = _resolve_agent_dir(args.agent_id)
    if agent_dir is None:
        raise SystemExit(f"Agent not found: {args.agent_id}")
    out = scaffold(args.agent_id)
    print(f"Service scaffolded at {out}")


if __name__ == "__main__":
    main()
