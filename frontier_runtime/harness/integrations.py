"""Connect the dev team to the outside world: specs in, delivery out.

Two transport-agnostic seams so the same TeamFlow works regardless of where a
spec comes from or where the result is delivered:

* ``SpecSource`` — normalize a spec from Linear / a file / inline text into the
  ``Spec`` the team consumes. Linear is the primary source; the fetch transport
  (platform MCP gateway, direct API, or a test fake) is injected.
* ``DeliveryTarget`` — act on an approved change. ``GitHubDelivery`` implements
  your policy: on approve, open a PR (show diff + CI); on re-approve of an
  already-open PR, merge to the target branch. Remote ops go through an injected
  ``GitHubClient`` (real impl shells ``git``/``gh``; tests fake it).

``DevFlow`` composes SpecSource -> TeamFlow -> DeliveryTarget.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Protocol

from frontier_runtime.harness.executor import Executor
from frontier_runtime.harness.swe_agent import SweTask
from frontier_runtime.harness.team import TeamFlow, TeamResult


# ---------------------------------------------------------------------------
# Specs in
# ---------------------------------------------------------------------------
@dataclass
class Spec:
    id: str
    title: str
    body: str
    source: str = "inline"
    url: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_prompt(self) -> str:
        head = f"# {self.title}\n\n" if self.title else ""
        return f"{head}{self.body}".strip()


class SpecSource(Protocol):
    def fetch_spec(self) -> Spec: ...


@dataclass
class InlineSpecSource:
    text: str
    title: str = ""
    id: str = "inline"

    def fetch_spec(self) -> Spec:
        return Spec(id=self.id, title=self.title, body=self.text, source="inline")


@dataclass
class FileSpecSource:
    path: str | Path

    def fetch_spec(self) -> Spec:
        p = Path(self.path)
        text = p.read_text(encoding="utf-8")
        return Spec(id=p.stem, title=p.stem, body=text, source="file", url=str(p))


@dataclass
class LinearSpecSource:
    """Spec from a Linear issue. ``fetcher(issue_id) -> dict`` abstracts the
    transport (platform MCP gateway / Linear API / test fake) and should return
    at least {identifier|id, title, description, url}."""

    issue_id: str
    fetcher: Callable[[str], dict[str, Any]]

    def fetch_spec(self) -> Spec:
        issue = self.fetcher(self.issue_id) or {}
        return Spec(
            id=str(issue.get("identifier") or issue.get("id") or self.issue_id),
            title=str(issue.get("title") or ""),
            body=str(issue.get("description") or issue.get("body") or ""),
            source="linear",
            url=str(issue.get("url") or ""),
            metadata={k: v for k, v in issue.items() if k not in ("description", "body")},
        )


# ---------------------------------------------------------------------------
# Delivery out
# ---------------------------------------------------------------------------
@dataclass
class DeliveryPolicy:
    """Configurable in /builder/settings."""

    auto_open_pr: bool = True
    auto_merge_on_reapprove: bool = False
    target_branch: str = "main"
    branch_prefix: str = "frontier/"
    merge_method: str = "squash"  # merge | squash | rebase

    @classmethod
    def from_settings(cls, settings: dict[str, Any] | None) -> "DeliveryPolicy":
        s = settings or {}
        return cls(
            auto_open_pr=bool(s.get("auto_open_pr", True)),
            auto_merge_on_reapprove=bool(s.get("auto_merge_on_reapprove", False)),
            target_branch=str(s.get("target_branch") or "main"),
            branch_prefix=str(s.get("branch_prefix") or "frontier/"),
            merge_method=str(s.get("merge_method") or "squash"),
        )


@dataclass
class DeliveryResult:
    action: str  # opened_pr | updated_pr | merged | awaiting_merge | skipped | no_changes
    branch: str = ""
    pr_url: str = ""
    pr_number: int | None = None
    ci_status: str = ""
    detail: str = ""


class GitHubClient(Protocol):
    """Remote GitHub operations (real impl shells git push + gh; tests fake it)."""

    def push_branch(self, branch: str) -> None: ...
    def find_open_pr(self, branch: str) -> dict[str, Any] | None: ...
    def open_pr(self, branch: str, base: str, title: str, body: str) -> dict[str, Any]: ...
    def merge_pr(self, number: int, method: str) -> dict[str, Any]: ...
    def ci_status(self, branch: str) -> str: ...


@dataclass
class GhCliGitHub:
    """GitHubClient backed by host ``git push`` + the ``gh`` CLI."""

    executor: Executor
    gh_runner: Callable[
        [list[str]], Any
    ]  # runs gh args, returns ExecResult-like (.stdout/.exit_code)

    def push_branch(self, branch: str) -> None:
        self.executor.run_shell(f"git push -u origin {branch}", timeout=120)

    def find_open_pr(self, branch: str) -> dict[str, Any] | None:
        res = self.gh_runner(
            ["pr", "list", "--head", branch, "--state", "open", "--json", "number,url,state"]
        )
        try:
            items = json.loads(res.stdout or "[]")
        except (json.JSONDecodeError, AttributeError):
            return None
        return items[0] if items else None

    def open_pr(self, branch: str, base: str, title: str, body: str) -> dict[str, Any]:
        res = self.gh_runner(
            ["pr", "create", "--head", branch, "--base", base, "--title", title, "--body", body]
        )
        url = (
            (getattr(res, "stdout", "") or "").strip().splitlines()[-1]
            if getattr(res, "stdout", "")
            else ""
        )
        return {"url": url}

    def merge_pr(self, number: int, method: str) -> dict[str, Any]:
        flag = {"merge": "--merge", "squash": "--squash", "rebase": "--rebase"}.get(
            method, "--squash"
        )
        self.gh_runner(["pr", "merge", str(number), flag, "--delete-branch"])
        return {"merged": True}

    def ci_status(self, branch: str) -> str:
        res = self.gh_runner(["pr", "checks", branch])
        return (getattr(res, "stdout", "") or "").strip()[:2000]


@dataclass
class GitHubDelivery:
    github: GitHubClient

    def deliver(
        self, task: SweTask, spec: Spec, team: TeamResult, policy: DeliveryPolicy
    ) -> DeliveryResult:
        ex = task.git_executor or task.executor
        branch = f"{policy.branch_prefix}{spec.id or task.instance_id}".replace(" ", "-")

        existing = self.github.find_open_pr(branch)
        if existing:
            # PR already open + re-approved -> merge if policy allows
            if policy.auto_merge_on_reapprove:
                num = int(existing.get("number"))
                self.github.merge_pr(num, policy.merge_method)
                return DeliveryResult(
                    action="merged",
                    branch=branch,
                    pr_url=str(existing.get("url") or ""),
                    pr_number=num,
                    ci_status=self.github.ci_status(branch),
                    detail=f"merged to {policy.target_branch} via {policy.merge_method}",
                )
            return DeliveryResult(
                action="awaiting_merge",
                branch=branch,
                pr_url=str(existing.get("url") or ""),
                pr_number=int(existing.get("number")),
                ci_status=self.github.ci_status(branch),
                detail="PR already open; auto-merge disabled",
            )

        if not policy.auto_open_pr:
            return DeliveryResult(action="skipped", branch=branch, detail="auto_open_pr disabled")

        # commit the approved change on a fresh branch and open a PR
        commit_msg = f"{spec.title or task.instance_id}\n\nResolves {spec.url or spec.id}".strip()
        ex.run_shell(f"git checkout -B {branch}", timeout=60)
        ex.run_shell("git add -A", timeout=60)
        ex.run_shell(f"git commit -m {json.dumps(commit_msg)}", timeout=60)
        self.github.push_branch(branch)
        pr = self.github.open_pr(
            branch, policy.target_branch, spec.title or task.instance_id, _pr_body(spec, team)
        )
        return DeliveryResult(
            action="opened_pr",
            branch=branch,
            pr_url=str(pr.get("url") or ""),
            ci_status=self.github.ci_status(branch),
            detail=f"opened PR against {policy.target_branch}",
        )


def _pr_body(spec: Spec, team: TeamResult) -> str:
    lines = [f"Spec: {spec.url or spec.id}", "", "## Summary", spec.body[:1000], ""]
    if team.rounds:
        last = team.rounds[-1]
        lines += [
            "## Review",
            f"Rounds: {team.round_count}",
            f"Moderator: {last.verdict.rationale}",
        ]
        if last.verdict.deferred:
            lines += ["", "Deferred (non-blocking):"] + [f"- {d}" for d in last.verdict.deferred]
    lines += ["", "_Generated by the xFrontier multi-agent dev team._"]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# DevFlow: spec -> team -> delivery
# ---------------------------------------------------------------------------
@dataclass
class DevFlowResult:
    spec: Spec
    team: TeamResult
    delivery: DeliveryResult | None = None

    @property
    def approved(self) -> bool:
        return self.team.approved


@dataclass
class DevFlow:
    team: TeamFlow
    spec_source: SpecSource
    delivery: DeliveryTarget | None = None
    policy: DeliveryPolicy = field(default_factory=DeliveryPolicy)
    on_event: Callable[[str, dict[str, Any]], None] | None = None

    def run(self, task: SweTask) -> DevFlowResult:
        spec = self.spec_source.fetch_spec()
        if self.on_event:
            self.on_event("spec", {"id": spec.id, "source": spec.source, "title": spec.title})
        task = replace(task, problem_statement=spec.as_prompt())
        team_result = self.team.run(task, spec.as_prompt())
        delivery_result = None
        if team_result.approved and self.delivery is not None:
            delivery_result = self.delivery.deliver(task, spec, team_result, self.policy)
            if self.on_event:
                self.on_event(
                    "delivery", {"action": delivery_result.action, "pr_url": delivery_result.pr_url}
                )
        return DevFlowResult(spec=spec, team=team_result, delivery=delivery_result)


class DeliveryTarget(Protocol):
    def deliver(
        self, task: SweTask, spec: Spec, team: TeamResult, policy: DeliveryPolicy
    ) -> DeliveryResult:
        raise NotImplementedError
