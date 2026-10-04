"""The variant archive: every evaluated variant, under ``LOCUS_LOOP_HOME/variants/`` (LOCUS-351).

One JSON file per evaluated variant (git sha, branch, scorecard, the comparison
against the baseline it was judged by, timestamp) plus an append-only
``index.jsonl``. The *baseline* is the latest complete, untampered scorecard
recorded for the base branch (``main``); a candidate is compared with it.

Optionally the loop tags ``variant/<sha12>`` locally (never pushed) so a variant
can be checked out again by its scorecard.

The archive lives in the loop home, which is runner-side state: the agent's
write root is the run's working copy, so a run cannot rewrite its own baseline.
"""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from locus_runtime.rsi.scorecard import Comparison, Scorecard

_SHA = re.compile(r"^[0-9a-f]{7,64}$")
_STAMP = "%Y%m%dT%H%M%SZ"
VARIANTS_DIR = "variants"
INDEX_FILE = "index.jsonl"


def _safe_branch(branch: str) -> str:
    return re.sub(r"[^A-Za-z0-9._/-]+", "-", str(branch or ""))[:120]


def _write_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=1, sort_keys=True)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


class VariantArchive:
    """``<home>/variants/<stamp>-<sha12>.json`` + ``index.jsonl``."""

    def __init__(self, home: Path) -> None:
        self.root = Path(home) / VARIANTS_DIR
        self.index = self.root / INDEX_FILE

    def record(
        self,
        scorecard: Scorecard,
        *,
        comparison: Comparison | None = None,
        now: datetime | None = None,
        source: str = "",
    ) -> Path:
        sha = str(scorecard.git_sha or "").lower()
        if not _SHA.fullmatch(sha):
            raise ValueError(f"a variant needs a git sha, got {scorecard.git_sha!r}")
        moment = now or datetime.now(UTC)
        stamp = moment.strftime(_STAMP)
        path = self.root / f"{stamp}-{sha[:12]}.json"
        entry = {
            "kind": "locus.rsi_variant",
            "recorded_at": moment.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "git_sha": sha,
            "branch": _safe_branch(scorecard.branch),
            "source": source,
            "scorecard": scorecard.model_dump(mode="json"),
            "comparison": comparison.model_dump(mode="json") if comparison else None,
        }
        _write_atomic(path, entry)
        heldout = scorecard.split("heldout")
        line = {
            "recorded_at": entry["recorded_at"],
            "git_sha": sha,
            "branch": entry["branch"],
            "status": scorecard.status,
            "file": path.name,
            "model": scorecard.model,
            "heldout_digest": scorecard.split_digests.get("heldout", ""),
            "heldout_pass_rate": heldout.pass_rate if heldout else None,
            "decision": comparison.decision if comparison else None,
        }
        with self.index.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, sort_keys=True) + "\n")
        return path

    def entries(self) -> list[dict[str, Any]]:
        try:
            lines = self.index.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        out: list[dict[str, Any]] = []
        for line in lines:
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if isinstance(item, dict):
                out.append(item)
        return out

    def load(self, name: str) -> tuple[Scorecard, Comparison | None] | None:
        if "/" in name or "\\" in name or not name.endswith(".json"):
            return None
        try:
            data = json.loads((self.root / name).read_text(encoding="utf-8"))
            scorecard = Scorecard.model_validate(data["scorecard"])
            raw = data.get("comparison")
            comparison = Comparison.model_validate(raw) if raw else None
        except (OSError, ValueError, KeyError, TypeError, ValidationError):
            return None
        return scorecard, comparison

    def baseline(
        self, branch: str = "main", *, heldout_digest: str = "", model: str = ""
    ) -> Scorecard | None:
        """The latest complete scorecard recorded for ``branch`` (optionally matching
        the held-out suite digest and model, so the comparison is like for like)."""
        for item in reversed(self.entries()):
            if item.get("branch") != _safe_branch(branch) or item.get("status") != "complete":
                continue
            if heldout_digest and item.get("heldout_digest") != heldout_digest:
                continue
            if model and item.get("model") != model:
                continue
            loaded = self.load(str(item.get("file") or ""))
            if loaded is not None and loaded[0].tamper.ok:
                return loaded[0]
        return None


def variant_tag(sha: str) -> str:
    """The local tag name for a variant (``variant/<sha12>``)."""
    clean = str(sha or "").lower()
    if not _SHA.fullmatch(clean):
        raise ValueError(f"not a git sha: {sha!r}")
    return f"variant/{clean[:12]}"
