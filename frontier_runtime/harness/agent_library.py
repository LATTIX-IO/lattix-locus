"""Load shipped agent definitions (the ones in ``examples/agents/``) so the
benchmark drives the *same* agent the platform ships in its modeler.

The platform seeds these dirs via ``_load_seeded_agents_from_repo`` in the
backend; here we read the same files to build a harness ``SweAgent``. One
definition, two consumers — the modeler/UI and the evaluation harness.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from frontier_runtime.harness.model_profiles import ModelCapabilityProfile, resolve_profile


@dataclass
class AgentSpec:
    agent_id: str
    name: str
    system_prompt: str
    model_defaults: dict[str, Any] = field(default_factory=dict)
    tools: list[dict[str, Any]] = field(default_factory=list)
    capabilities: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    source_dir: Path | None = None

    @property
    def provider(self) -> str:
        return str(self.model_defaults.get("provider") or "")

    @property
    def model(self) -> str:
        return str(self.model_defaults.get("model") or "")

    def profile(self, *, overrides: dict[str, Any] | None = None) -> ModelCapabilityProfile:
        md = self.model_defaults
        prof_overrides: dict[str, Any] = {}
        for key in (
            "edit_format",
            "tool_protocol",
            "temperature",
            "top_p",
            "max_effective_context",
        ):
            if key in md and md[key] is not None:
                prof_overrides[key] = md[key]
        if overrides:
            prof_overrides.update(overrides)
        return resolve_profile(
            self.provider or "openai-compatible",
            self.model,
            profile_id=md.get("capability_profile") or None,
            overrides=prof_overrides or None,
        )


def _agents_root(repo_root: Path | None = None) -> Path:
    root = repo_root or Path(__file__).resolve().parents[2]
    return root / "examples" / "agents"


def list_shipped_agents(repo_root: Path | None = None) -> list[str]:
    root = _agents_root(repo_root)
    if not root.is_dir():
        return []
    return sorted(
        d.name for d in root.iterdir() if d.is_dir() and (d / "agent.config.json").exists()
    )


def load_agent_spec(agent_id: str, repo_root: Path | None = None) -> AgentSpec:
    """Load a shipped agent by its directory id (e.g. 'sdet-swe-agent')."""
    agent_dir = _agents_root(repo_root) / agent_id
    config_path = agent_dir / "agent.config.json"
    if not config_path.exists():
        raise FileNotFoundError(
            f"no shipped agent '{agent_id}' (looked in {config_path}). "
            f"Available: {', '.join(list_shipped_agents(repo_root)) or 'none'}"
        )
    config = json.loads(config_path.read_text(encoding="utf-8"))
    prompt_file = config.get("prompt_file") or "system-prompt.md"
    prompt_path = agent_dir / prompt_file
    system_prompt = ""
    if prompt_path.exists():
        system_prompt = prompt_path.read_text(encoding="utf-8").strip()
    return AgentSpec(
        agent_id=str(config.get("id") or agent_id),
        name=str(config.get("name") or agent_id),
        system_prompt=system_prompt,
        model_defaults=config.get("model_defaults") or {},
        tools=config.get("tools") or [],
        capabilities=config.get("capabilities") or [],
        tags=config.get("tags") or [],
        source_dir=agent_dir,
    )
