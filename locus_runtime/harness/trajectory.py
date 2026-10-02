"""Lossless, replayable JSONL trajectory recording.

One run produces one trajectory: a header line, one ``message`` line per
message exchanged with the model (verbatim OpenAI chat format, untruncated),
``annotation`` lines for repairs/budget events, and a final ``outcome`` line.

The message lines reconstruct the exact message list sent to / returned by the
provider, so trajectories are directly usable as SFT/RL data. The
``outcome.status == "submitted"`` predicate is the DeepSWE compact-filtering
gate (train only on submitted trajectories).

Schema is version-tagged (``v``). ``seq`` is strictly increasing.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


@dataclass
class TrajectoryRecorder:
    """Accumulates trajectory records in memory; optionally mirrors to a file.

    Not thread-safe; one recorder per run/rollout.
    """

    run_id: str
    file_path: Path | None = None
    records: list[dict[str, Any]] = field(default_factory=list)
    _seq: int = 0
    _store_max_bytes: int = 0  # 0 = uncapped; otherwise cap per tool/message content

    def __post_init__(self) -> None:
        # storage cap for very large tool outputs (recorded in header so SFT can exclude)
        raw = os.getenv("LOCUS_TRAJECTORY_MAX_RECORD_BYTES", "0").strip()
        try:
            self._store_max_bytes = max(0, int(raw))
        except ValueError:
            self._store_max_bytes = 0
        if self.file_path is not None:
            self.file_path.parent.mkdir(parents=True, exist_ok=True)
            # truncate any prior file for this run
            self.file_path.write_text("", encoding="utf-8")

    # -- internal -----------------------------------------------------------
    def _emit(self, record: dict[str, Any]) -> dict[str, Any]:
        record = {"v": SCHEMA_VERSION, "seq": self._seq, "ts": _now_iso(), **record}
        self._seq += 1
        self.records.append(record)
        if self.file_path is not None:
            with self.file_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
                fh.flush()
        return record

    def _cap(self, content: Any) -> tuple[Any, bool]:
        if self._store_max_bytes <= 0 or not isinstance(content, str):
            return content, False
        encoded = content.encode("utf-8", errors="replace")
        if len(encoded) <= self._store_max_bytes:
            return content, False
        kept = encoded[: self._store_max_bytes].decode("utf-8", errors="ignore")
        return kept + "\n[... trajectory record truncated for storage ...]", True

    # -- public API ---------------------------------------------------------
    def header(
        self,
        *,
        agent_id: str,
        model: str,
        provider: str,
        sampler: dict[str, Any],
        budgets: dict[str, Any],
        system_prompt: str,
        task: dict[str, Any],
        harness: dict[str, Any] | None = None,
    ) -> None:
        self._emit(
            {
                "kind": "meta",
                "run_id": self.run_id,
                "agent_id": agent_id,
                "model": model,
                "provider": provider,
                "sampler": sampler,
                "budgets": budgets,
                "system_prompt_sha256": _sha256(system_prompt),
                "storage_record_cap_bytes": self._store_max_bytes,
                "task": task,
                "harness": harness or {},
            }
        )

    def message(
        self,
        message: dict[str, Any],
        *,
        step: int,
        usage: dict[str, Any] | None = None,
        tool: dict[str, Any] | None = None,
        event_id: str | None = None,
    ) -> None:
        recorded = dict(message)
        capped = False
        if isinstance(recorded.get("content"), str):
            recorded["content"], capped = self._cap(recorded["content"])
        rec: dict[str, Any] = {"kind": "message", "step": step, "message": recorded}
        if usage:
            rec["usage"] = usage
        if tool:
            rec["tool"] = tool
        if event_id:
            rec["event_id"] = event_id
        if capped:
            rec["storage_capped"] = True
        self._emit(rec)

    def annotation(self, note: str, *, step: int, **fields: Any) -> None:
        self._emit({"kind": "annotation", "step": step, "note": note, **fields})

    def outcome(
        self,
        status: str,
        *,
        submission: dict[str, Any] | None,
        steps: int,
        budgets_used: dict[str, Any],
    ) -> None:
        self._emit(
            {
                "kind": "outcome",
                "status": status,
                "submission": submission,
                "steps": steps,
                "budgets_used": budgets_used,
            }
        )

    # -- reconstruction -----------------------------------------------------
    def messages(self) -> list[dict[str, Any]]:
        """Reconstruct the verbatim message list (for replay / SFT)."""
        return [r["message"] for r in self.records if r.get("kind") == "message"]

    def final_outcome(self) -> dict[str, Any] | None:
        for r in reversed(self.records):
            if r.get("kind") == "outcome":
                outcome: dict[str, Any] = r
                return outcome
        return None

    def to_jsonl(self) -> str:
        return "\n".join(json.dumps(r, ensure_ascii=False) for r in self.records)

    @classmethod
    def parse(cls, jsonl: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for line in jsonl.splitlines():
            line = line.strip()
            if line:
                out.append(json.loads(line))
        return out


def _now_iso() -> str:
    # epoch->iso without argless datetime (kept deterministic-friendly)
    t = time.time()
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + f".{int((t % 1) * 1000):03d}Z"
