"""LOCUS-479: deterministic, availability-first local Auto model planning.

A model visible in a catalog is not necessarily installed, healthy, or approved.
This component accepts ONLY a live Ollama /api/tags inventory and an already
computed authorization policy. It never contacts a hosted provider, guesses
credentials, downloads weights, or changes policy. Gateway remains the final
authority on each model call.

The model-class heuristics are *not* benchmarked quality scores. A separate
evaluation/telemetry increment will learn per-task performance and budgets.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable


_EMBEDDING_ONLY = re.compile(
    r"(?:^|/)(?:nomic-embed-text|mxbai-embed-large|all-minilm|bge-m3)(?::|$)",
    flags=re.IGNORECASE,
)
_CODING_TASK = re.compile(
    r"\b(code|coding|python|rust|typescript|debug|implement|refactor|build|compile|"
    r"pull request|unit tests?|repository|software|api|function|fix a bug)\b",
    flags=re.IGNORECASE,
)
_ANALYSIS_TASK = re.compile(
    r"\b(architecture|assess|research|investigate|analy[sz]e|security|strategy|"
    r"complex|compare|design|risk|reason|evaluation)\b",
    flags=re.IGNORECASE,
)
# Auto avoids lineages requiring separate D-29 provenance attestations. They may
# still be explicitly eligible after verification at the ModelClient boundary.
_KNOWN_FAMILIES = ("gpt-oss", "llama", "gemma", "mistral", "phi", "granite")


@dataclass(frozen=True)
class AutoLocalPlan:
    selected: str
    # Models are provider-qualified to prevent implicit OpenAI dispatch.
    candidates: tuple[str, ...]
    task_class: str
    considered_count: int

    @property
    def model(self) -> str:
        return self.selected


def chat_capable_model(model_id: str, capabilities: Iterable[str] | None = None) -> bool:
    """Hide known embedding-only models; reject explicit non-chat capability."""
    model_id = str(model_id or "").strip()
    if not model_id or len(model_id) > 256 or _EMBEDDING_ONLY.search(model_id):
        return False
    listed = {str(value).strip().lower() for value in (capabilities or [])}
    return not listed or "completion" in listed or "tools" in listed


def _intent(prompt: str) -> str:
    bounded = str(prompt or "")[:4096]
    if _CODING_TASK.search(bounded):
        return "coding"
    if _ANALYSIS_TASK.search(bounded):
        return "analysis"
    return "general"


def _score(model_id: str, task_class: str, preferred: str) -> int:
    # Pure, auditable task hints. Do not market this as measured intelligence.
    name = model_id.lower()
    score = 0
    if "gpt-oss:20b" in name:
        score = 80
    elif "gpt-oss-t3" in name:
        score = 67
    elif "gpt-oss" in name:
        score = 72
    elif "coder" in name:
        score = 68
    elif any(family in name for family in ("gemma", "llama", "mistral", "phi", "granite")):
        score = 50
    if task_class == "coding":
        score += 14 if "coder" in name else (8 if "gpt-oss" in name else 0)
    elif task_class == "analysis":
        score += 8 if "gpt-oss" in name else 0
    else:
        score += 9 if "gpt-oss-t3" in name else 0
    if "ctx32k" in name:
        score += 5 if task_class in {"coding", "analysis"} else 1
    if model_id == preferred:
        score += 3
    return score


def plan_local_auto(
    installed: Iterable[dict[str, Any]],
    *,
    prompt: str = "",
    preferred: str = "",
    allowed_models: Iterable[str] | None = None,
    max_candidates: int = 3,
) -> AutoLocalPlan | None:
    """Plan bounded, already-installed local candidates, or return no route.

    An allowed_models filter is an enforced intersection, never an expansion.
    Unknown/third-party fine-tunes stay selectable explicitly but are not
    *automatically* trusted until a capability/quality profile is established.
    """
    allowed = {str(item).strip() for item in (allowed_models or []) if str(item).strip()}
    task_class = _intent(prompt)
    names: set[str] = set()
    for item in installed:
        if not isinstance(item, dict):
            continue
        name = str(item.get("id") or "").strip()
        caps = item.get("capabilities")
        if not chat_capable_model(name, caps if isinstance(caps, list) else None):
            continue
        # A custom Hugging Face fine-tune is not automatically eligible
        # merely because its filename contains a familiar model family.
        if name.lower().startswith("hf.co/"):
            continue
        if not any(family in name.lower() for family in _KNOWN_FAMILIES):
            continue
        if allowed and name not in allowed and f"ollama/{name}" not in allowed:
            continue
        names.add(name)

    if not names:
        return None
    ranked = sorted(names, key=lambda model: (-_score(model, task_class, preferred), model))
    qualified = tuple(f"ollama/{value}" for value in ranked[: max(1, min(3, max_candidates))])
    return AutoLocalPlan(
        selected=qualified[0],
        candidates=qualified,
        task_class=task_class,
        considered_count=len(names),
    )


def valid_response_body(text: str) -> bool:
    """Transport-independent minimum validator; not a quality judgment."""
    return bool(str(text or "").strip())
