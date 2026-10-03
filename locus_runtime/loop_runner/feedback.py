"""Loop feedback: skill proposals from done runs, failure-pattern issues (LOCUS-339).

Both are **runner-side** steps after a run ended; nothing here is reachable by
the agent.

* **Skill proposals (P24, propose then promote).** A done run's trajectory is
  summarised deterministically (no model call) into a ``SKILL.md`` and
  installed in the skill store in the ``quarantined`` state -- never scanned
  into trust, never evaluated, never trusted here. A human promotes it through
  the normal skill lifecycle. The proposal is a single ``SKILL.md`` with no
  scripts, no capabilities (default deny) and provenance in its metadata.

* **Failure patterns.** Failed and stopped runs are clustered by a stable
  fingerprint of (kind, normalised reason). A new pattern seen at least
  ``min_occurrences`` times gets at most one Linear issue, deduplicated by a
  fingerprint marker line in the issue body (local registry first, then a
  search in Linear) and bounded by a daily cap. Filed issues are not labelled
  ``agent:eligible``: a human triages them, so the loop never feeds itself.

Trajectory and failure text is untrusted (P8): :func:`sanitize_untrusted`
redacts secrets, drops code blocks and HTML comments, neutralises anything
that could forge a ``locus-loop`` marker, and caps the length.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Protocol, runtime_checkable

from locus_runtime.gateway import redact_text
from locus_runtime.skills import (
    SKILL_FILE,
    SkillError,
    SkillRecord,
    SkillStore,
    load_skill_files,
    render_skill_md,
    scan_blocks,
    scan_skill_files,
)

logger = logging.getLogger(__name__)

FAILURE_MARKER = "locus-loop-failure-fingerprint"
FAILURE_OUTCOMES = frozenset({"blocked", "stopped", "error"})
_MAX_STEPS = 10
_MAX_STEP_CHARS = 200
_MAX_FILES = 20
_MAX_TOOLS = 15
_TOOL_NAME = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
_SAFE_PATH = re.compile(r"^[A-Za-z0-9._/-]{1,200}$")
_CODE_FENCE = re.compile(r"(```|~~~).*?(\1|$)", re.DOTALL)
_HTML_COMMENT = re.compile(r"<!--.*?(-->|$)", re.DOTALL)
_LOOP_MARKER = re.compile(r"locus[\s_-]*loop", re.IGNORECASE)
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


# --------------------------------------------------------------------------- #
# Sanitizing untrusted text (P8)
# --------------------------------------------------------------------------- #
def sanitize_untrusted(text: Any, limit: int = 300) -> str:
    """Bounded, inert text from untrusted run content.

    Redacts secret-looking values, removes fenced code blocks (no executable
    snippets travel into a skill or an issue) and HTML comments, neutralises
    ``locus-loop`` markers and ``<!--``, strips control characters, collapses
    whitespace and truncates to ``limit`` characters.
    """
    value = str(text or "")
    value = _CODE_FENCE.sub(" [code omitted] ", value)
    value = _HTML_COMMENT.sub(" ", value)
    value = _CONTROL.sub(" ", value)
    value = redact_text(value, limit=max(limit * 4, 64))
    value = _LOOP_MARKER.sub("loop", value)
    value = value.replace("<!--", "&lt;!--").replace("-->", "--&gt;").replace("`", "'")
    value = re.sub(r"\s+", " ", value).strip()
    if len(value) > limit:
        value = value[: max(0, limit - 1)].rstrip() + "…"
    return value


# --------------------------------------------------------------------------- #
# Skill proposal (pure summary + quarantined install)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class SkillProposal:
    skill_id: str
    name: str
    description: str
    body: str
    provenance: dict[str, str]

    def skill_md(self) -> str:
        return render_skill_md(
            name=self.name,
            description=self.description,
            body=self.body,
            metadata={"locus-proposal": {**self.provenance, "state": "proposed"}},
        )


def tool_counts(messages: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    """How often the run called each tool (names only; arguments are never used)."""
    counts: dict[str, int] = {}
    for message in messages:
        for call in message.get("tool_calls") or []:
            if not isinstance(call, Mapping):
                continue
            function = call.get("function")
            name = str((function or {}).get("name") if isinstance(function, Mapping) else "")
            name = name or str(call.get("name") or "")
            if _TOOL_NAME.fullmatch(name):
                counts[name] = counts.get(name, 0) + 1
    return counts


def _slug(text: str, limit: int) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")
    return re.sub(r"-{2,}", "-", slug)[:limit].strip("-")


def build_skill_proposal(
    *,
    issue_key: str,
    issue_title: str,
    run_id: str,
    pr_url: str,
    plan_steps: Sequence[Any],
    tools: Mapping[str, int],
    changed_paths: Sequence[str],
    checks_passed: Sequence[str],
    verification_attempts: int = 0,
) -> SkillProposal:
    """A deterministic SKILL.md proposal summarising a done run (pure)."""
    key = _slug(issue_key, 24) or "issue"
    title = sanitize_untrusted(issue_title, 120) or "a loop task"
    name = (f"loop-{key}-" + _slug(title, 40)).strip("-")[:64].strip("-") or f"loop-{key}"
    skill_id = f"loop-{key}-{_slug(run_id, 60)[-12:]}".strip("-")[:120] or f"loop-{key}"
    description = sanitize_untrusted(
        f"Proposed by the Locus loop from run {run_id} ({issue_key}): how a change like "
        f"'{title}' was planned, made and verified. Unreviewed; quarantined until promoted.",
        500,
    )
    steps = [s for s in (sanitize_untrusted(x, _MAX_STEP_CHARS) for x in plan_steps) if s]
    files = [
        p
        for p in (str(x).replace("\\", "/") for x in changed_paths)
        if _SAFE_PATH.fullmatch(p) and ".." not in PurePosixPath(p).parts
    ]
    tool_lines = [
        f"- `{name_}` x{count}"
        for name_, count in sorted(tools.items(), key=lambda kv: (-kv[1], kv[0]))
        if _TOOL_NAME.fullmatch(name_)
    ][:_MAX_TOOLS]
    checks = [c for c in checks_passed if _TOOL_NAME.fullmatch(str(c))]
    lines = [
        f"# {title}",
        "",
        "## When to use",
        f"Tasks similar to {sanitize_untrusted(issue_key, 40)}: {title}.",
        "",
        "## Approach that worked",
        *([f"{i}. {s}" for i, s in enumerate(steps[:_MAX_STEPS], 1)] or ["(no plan recorded)"]),
        "",
        "## Tools used",
        *(tool_lines or ["(none recorded)"]),
        "",
        "## Files touched",
        *([f"- `{p}`" for p in files[:_MAX_FILES]] or ["(none)"]),
        *(["- (more files omitted)"] if len(files) > _MAX_FILES else []),
        "",
        "## Verification",
        f"- Done criteria verified after {max(0, int(verification_attempts))} attempt(s).",
        *([f"- Gate `{c}` passed." for c in checks] or ["- (no repository gate selected)"]),
        "",
        "## Provenance",
        f"- Issue: {sanitize_untrusted(issue_key, 40)}",
        f"- Run: {sanitize_untrusted(run_id, 80)}",
        f"- PR: {sanitize_untrusted(pr_url, 200)}",
        "- Status: proposed by the self-improvement loop; review before promoting (P24).",
    ]
    return SkillProposal(
        skill_id=skill_id,
        name=name,
        description=description,
        body="\n".join(lines),
        provenance={
            "issue": sanitize_untrusted(issue_key, 40),
            "run_id": sanitize_untrusted(run_id, 80),
            "pr": sanitize_untrusted(pr_url, 200),
            "source": "self-improvement-loop",
        },
    )


def propose_skill(store: SkillStore, proposal: SkillProposal) -> SkillRecord | None:
    """Install the proposal **quarantined** (a blocking scan finding blocks it).

    Never trusts, never marks evaluated. Returns None when the id already exists
    (idempotent per run) or the proposal does not validate.
    """
    files = {SKILL_FILE: proposal.skill_md().encode("utf-8")}
    try:
        document = load_skill_files(files)
    except SkillError as exc:
        logger.warning("loop.skill_proposal_invalid", extra={"code": exc.code})
        return None
    if document.scripts:  # defence in depth: auto-proposals never carry scripts
        return None
    try:
        record, _ = store.install(proposal.skill_id, files, source="loop-proposal")
    except SkillError as exc:
        if exc.code == "exists":
            return None
        raise
    findings = scan_skill_files(document, files)
    if scan_blocks(findings):
        record = store.mark_scanned(proposal.skill_id, cleared=False, findings=findings)
    return record


# --------------------------------------------------------------------------- #
# Failure clustering (pure) + registry
# --------------------------------------------------------------------------- #
_NORMALIZERS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bloop-[a-z0-9-]+\b"), "<run>"),
    (re.compile(r"\bgw-[0-9a-f-]{8,}\b"), "<audit>"),
    (re.compile(r"\b[0-9a-f]{7,64}\b"), "<hex>"),
    (re.compile(r"(?:[A-Za-z]:)?(?:[\\/][\w.@~-]+){2,}"), "<path>"),
    (re.compile(r"'[^']{0,200}'|\"[^\"]{0,200}\""), "<str>"),
    (re.compile(r"\b\d+(?:\.\d+)?\b"), "<n>"),
)


def normalize_failure(kind: str, detail: str) -> str:
    """The stable shape of a failure reason (ids, paths, numbers, quotes removed)."""
    text = str(detail or "").lower()
    for pattern, token in _NORMALIZERS:
        text = pattern.sub(token, text)
    text = re.sub(r"\s+", " ", text).strip()[:200]
    return f"{str(kind or 'unknown').strip().lower()[:40]}|{text}"


def failure_fingerprint(kind: str, detail: str) -> str:
    return hashlib.sha256(normalize_failure(kind, detail).encode("utf-8")).hexdigest()[:16]


def split_outcome_detail(detail: str) -> tuple[str, str]:
    """``"kind: detail"`` as the ledger stores it -> ``(kind, detail)``."""
    head, sep, tail = str(detail or "").partition(":")
    if sep and re.fullmatch(r"[A-Za-z0-9_ -]{1,40}", head.strip()):
        return head.strip(), tail.strip()
    return "unknown", str(detail or "")


@dataclass
class FailureCluster:
    fingerprint: str
    kind: str
    sample: str
    count: int = 0
    issues: list[str] = field(default_factory=list)
    run_ids: list[str] = field(default_factory=list)
    team_id: str = ""
    first_at: str = ""
    last_at: str = ""


def cluster_failures(records: Iterable[Mapping[str, Any]]) -> list[FailureCluster]:
    """Group failed/stopped/blocked runs by fingerprint (user stops are not failures)."""
    clusters: dict[str, FailureCluster] = {}
    for record in records:
        outcome = str(record.get("outcome") or "")
        if outcome not in FAILURE_OUTCOMES:
            continue
        kind = str(record.get("kind") or "")
        detail = str(record.get("reason") or "")
        if not kind:
            kind, detail = split_outcome_detail(str(record.get("detail") or ""))
        if outcome == "stopped" and kind == "user":
            continue
        fp = failure_fingerprint(f"{outcome}/{kind}", detail)
        cluster = clusters.get(fp)
        if cluster is None:
            cluster = clusters[fp] = FailureCluster(
                fingerprint=fp,
                kind=f"{outcome}/{kind}",
                sample=sanitize_untrusted(detail, 300),
                first_at=str(record.get("finished_at") or ""),
            )
        cluster.count += 1
        issue = str(record.get("issue") or "")
        if issue and issue not in cluster.issues:
            cluster.issues.append(issue)
        run_id = str(record.get("run_id") or "")
        if run_id:
            cluster.run_ids = [*cluster.run_ids, run_id][-10:]
        cluster.team_id = str(record.get("team_id") or cluster.team_id)
        cluster.last_at = str(record.get("finished_at") or cluster.last_at)
    return sorted(clusters.values(), key=lambda c: (-c.count, c.fingerprint))


class FailureRegistry:
    """``failure-patterns.json``: fingerprints already filed, and filings per UTC day."""

    def __init__(self, home: Path) -> None:
        self.path = Path(home) / "failure-patterns.json"
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        self.data: dict[str, Any] = data if isinstance(data, dict) else {}

    @property
    def known(self) -> dict[str, dict[str, Any]]:
        known = self.data.get("patterns")
        return known if isinstance(known, dict) else {}

    def filed_on(self, day: str) -> int:
        return int((self.data.get("filed_per_day") or {}).get(day, 0))

    def remember(self, fingerprint: str, *, issue: str, day: str, filed: bool) -> None:
        patterns = dict(self.known)
        patterns[fingerprint] = {"issue": issue, "recorded": day, "filed_by_loop": filed}
        self.data["patterns"] = patterns
        if filed:
            per_day = {k: v for k, v in (self.data.get("filed_per_day") or {}).items() if k >= day}
            per_day[day] = int(per_day.get(day, 0)) + 1
            self.data["filed_per_day"] = per_day

    def save(self) -> None:
        from locus_runtime.loop_runner.state import write_json_atomic

        write_json_atomic(self.path, self.data)


def plan_failure_issues(
    clusters: Sequence[FailureCluster],
    *,
    known: Mapping[str, Any],
    filed_today: int,
    daily_cap: int,
    min_occurrences: int,
) -> list[FailureCluster]:
    """New patterns to file now: unknown, frequent enough, within today's cap. Pure."""
    room = max(0, int(daily_cap) - int(filed_today))
    out: list[FailureCluster] = []
    for cluster in clusters:
        if len(out) >= room:
            break
        if cluster.fingerprint in known or cluster.count < max(1, int(min_occurrences)):
            continue
        out.append(cluster)
    return out


def failure_marker(fingerprint: str) -> str:
    return f"{FAILURE_MARKER}: {fingerprint}"


def failure_issue(cluster: FailureCluster) -> tuple[str, str]:
    """``(title, body)`` of the Linear issue for a failure pattern."""
    kind = sanitize_untrusted(cluster.kind, 60)
    title = f"Loop failure pattern: {kind} ({cluster.fingerprint[:8]})"
    issues = ", ".join(sanitize_untrusted(i, 40) for i in cluster.issues[:10]) or "n/a"
    runs = ", ".join(sanitize_untrusted(r, 80) for r in cluster.run_ids[-5:]) or "n/a"
    body = "\n".join(
        [
            "The Locus self-improvement loop saw the same failure pattern "
            f"{cluster.count} time(s).",
            "",
            f"- Kind: {kind}",
            f"- Reason (sample, untrusted run output, sanitized): {cluster.sample or 'n/a'}",
            f"- Issues: {issues}",
            f"- Recent runs: {runs}",
            f"- First seen: {sanitize_untrusted(cluster.first_at, 30) or 'n/a'}; "
            f"last seen: {sanitize_untrusted(cluster.last_at, 30) or 'n/a'}",
            "",
            "Filed for triage: this issue is not labelled agent:eligible. Run artifacts are "
            "under LOCUS_LOOP_HOME/runs on the runner host.",
            "",
            failure_marker(cluster.fingerprint),
        ]
    )
    return title[:120], body


@runtime_checkable
class FailureTracker(Protocol):
    """The Linear writes failure filing needs (:class:`~.linear.LinearClient` has them)."""

    def find_issue_with_text(self, text: str) -> str | None: ...
    def create_issue(
        self, *, team_id: str, title: str, description: str, project_slug: str = ""
    ) -> str: ...
