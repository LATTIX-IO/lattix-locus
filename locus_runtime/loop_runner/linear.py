"""Native Linear intake for the self-improvement loop (LOCUS-338; 11 §8, 16 §7).

FOSS-first (P30): this is plain ``httpx`` against Linear's public GraphQL API
(``https://api.linear.app/graphql``) -- no vendor SDK. The Linear SDKs are
TypeScript-first; the Python surface we need is five queries/mutations, so a
thin typed client over the existing ``httpx`` dependency is smaller than any
wrapper and adds no dependency.

The API key resolves by reference (P10): ``LINEAR_API_KEY`` from the
environment, then the OS keychain, then Windows DPAPI
(:mod:`locus_tooling.native_secrets`). It is sent only in the
``Authorization`` header and never logged, returned or put in an error message.

Issue text fetched here is **untrusted content** (P8): it becomes the run's
goal and done criteria, never its capabilities or grants.
"""

from __future__ import annotations

import logging
import random
import re
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, TypeVar

import httpx

logger = logging.getLogger(__name__)

LINEAR_GRAPHQL_URL = "https://api.linear.app/graphql"
LINEAR_KEY_NAME = "LINEAR_API_KEY"
ELIGIBLE_LABEL = "agent:eligible"
INELIGIBLE_LABEL = "agent:ineligible"
HUMAN_REVIEW_LABEL = "agent:human-review-required"

#: Comment markers the runner writes; parsed back to make claims idempotent.
CLAIM_MARKER = "locus-loop:claim"
RELEASE_MARKER = "locus-loop:release"
_MARKER_RE = re.compile(
    r"<!--\s*(locus-loop:(?:claim|release))\s+run_id=([A-Za-z0-9_.:-]{1,80})"
    r"\s+at=(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)\s*-->"
)

T = TypeVar("T")


class LinearError(RuntimeError):
    """A Linear API failure. ``transient`` failures are retried with backoff."""

    def __init__(self, message: str, *, transient: bool = False) -> None:
        super().__init__(message)
        self.transient = transient


class LinearNotConfigured(LinearError):
    """No ``LINEAR_API_KEY`` is available from env, keychain or DPAPI."""


# --------------------------------------------------------------------------- #
# Model
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Claim:
    kind: str  # claim | release
    run_id: str
    at: datetime


@dataclass(frozen=True)
class LinearIssue:
    id: str
    identifier: str
    title: str
    description: str = ""
    priority: int = 0  # Linear: 0 none, 1 urgent, 2 high, 3 normal, 4 low
    url: str = ""
    state: str = ""
    labels: tuple[str, ...] = ()
    created_at: str = ""
    team_id: str = ""
    claims: tuple[Claim, ...] = field(default_factory=tuple)

    @property
    def label_set(self) -> frozenset[str]:
        return frozenset(label.strip().lower() for label in self.labels)

    def as_spec_dict(self) -> dict[str, Any]:
        """The shape :class:`~locus_runtime.harness.integrations.LinearSpecSource` reads."""
        return {
            "id": self.id,
            "identifier": self.identifier,
            "title": self.title,
            "description": self.description,
            "url": self.url,
            "priority": self.priority,
            "state": self.state,
            "labels": list(self.labels),
        }

    def task_text(self) -> str:
        return f"# {self.title}\n\n{self.description}".strip()


def parse_claims(comment_bodies: Iterable[str]) -> tuple[Claim, ...]:
    claims: list[Claim] = []
    for body in comment_bodies:
        for kind, run_id, at in _MARKER_RE.findall(str(body or "")):
            try:
                stamp = datetime.strptime(at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
            except ValueError:
                continue
            claims.append(Claim("claim" if kind == CLAIM_MARKER else "release", run_id, stamp))
    return tuple(sorted(claims, key=lambda c: c.at))


def marker(kind: str, run_id: str, at: datetime) -> str:
    return f"<!-- {kind} run_id={run_id} at={at.strftime('%Y-%m-%dT%H:%M:%SZ')} -->"


def live_claim(issue: LinearIssue, *, now: datetime, ttl_seconds: float) -> Claim | None:
    """The newest unreleased claim younger than ``ttl_seconds`` (None if free)."""
    released = {c.run_id for c in issue.claims if c.kind == "release"}
    for claim in reversed(issue.claims):
        if claim.kind != "claim" or claim.run_id in released:
            continue
        if (now - claim.at).total_seconds() < ttl_seconds:
            return claim
        return None
    return None


# --------------------------------------------------------------------------- #
# Eligibility (pure)
# --------------------------------------------------------------------------- #
def _priority_rank(priority: int) -> int:
    # Linear's 0 means "no priority": sort it after low (4).
    return priority if 1 <= priority <= 4 else 5


def eligible_issues(
    issues: Sequence[LinearIssue],
    *,
    active_states: Iterable[str],
    exclude_labels: Iterable[str],
    required_label: str = ELIGIBLE_LABEL,
    now: datetime | None = None,
    claim_ttl_seconds: float = 7200.0,
    own_run_ids: Iterable[str] = (),
) -> list[LinearIssue]:
    """Issues the loop may take, highest priority first (then oldest, then key).

    Requires ``required_label``; excludes any ``exclude_labels`` (always
    including ``agent:ineligible`` and ``agent:human-review-required``), states
    outside ``active_states``, and issues live-claimed by another run.
    """
    now = now or datetime.now(UTC)
    states = {s.strip().lower() for s in active_states}
    excluded = {s.strip().lower() for s in exclude_labels} | {
        INELIGIBLE_LABEL,
        HUMAN_REVIEW_LABEL,
    }
    required = required_label.strip().lower()
    mine = set(own_run_ids)
    out: list[LinearIssue] = []
    for issue in issues:
        labels = issue.label_set
        if required not in labels or labels & excluded:
            continue
        if issue.state.strip().lower() not in states:
            continue
        claim = live_claim(issue, now=now, ttl_seconds=claim_ttl_seconds)
        if claim is not None and claim.run_id not in mine:
            continue
        out.append(issue)
    return sorted(out, key=lambda i: (_priority_rank(i.priority), i.created_at, i.identifier))


# --------------------------------------------------------------------------- #
# Retry
# --------------------------------------------------------------------------- #
def with_retry(
    operation: Callable[[], T],
    *,
    attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Run ``operation``; retry transient :class:`LinearError` with jittered backoff."""
    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except LinearError as exc:
            if not exc.transient or attempt == attempts:
                raise
            delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
            sleep(delay * (0.5 + random.random() / 2))  # noqa: S311 - jitter, not crypto
    raise AssertionError("unreachable")  # pragma: no cover


# --------------------------------------------------------------------------- #
# Client
# --------------------------------------------------------------------------- #
def resolve_linear_key() -> str:
    """``LINEAR_API_KEY`` from env → OS keychain → DPAPI. Raises when absent."""
    try:
        from locus_tooling.native_secrets import SecretStorageUnavailable, get_secret
    except ImportError as exc:  # pragma: no cover - tooling ships with the runtime
        raise LinearNotConfigured("secret storage is unavailable") from exc
    try:
        value = get_secret(LINEAR_KEY_NAME)
    except SecretStorageUnavailable as exc:
        raise LinearNotConfigured(
            "LINEAR_API_KEY is not set and no secure secret store is available"
        ) from exc
    if not str(value or "").strip():
        raise LinearNotConfigured(
            "LINEAR_API_KEY is not configured; run `lattix secrets set LINEAR_API_KEY`"
        )
    return str(value).strip()


_ISSUE_FIELDS = """
  id identifier title description priority url createdAt
  state { name }
  team { id }
  labels(first: 50) { nodes { name } }
  comments(first: 100) { nodes { body } }
"""

_CANDIDATES_QUERY = (
    """
query LocusLoopCandidates($slug: String!, $states: [String!], $label: String!, $after: String) {
  issues(
    first: 50
    after: $after
    filter: {
      project: { slugId: { eq: $slug } }
      state: { name: { in: $states } }
      labels: { some: { name: { eqIgnoreCase: $label } } }
    }
  ) {
    pageInfo { hasNextPage endCursor }
    nodes {"""
    + _ISSUE_FIELDS
    + """}
  }
}
"""
)

_ISSUE_QUERY = "query LocusLoopIssue($id: String!) { issue(id: $id) {" + _ISSUE_FIELDS + "} }"

_TEAM_STATES_QUERY = """
query LocusLoopStates($id: String!) {
  issue(id: $id) { team { states(first: 100) { nodes { id name } } } }
}
"""

_LABEL_QUERY = """
query LocusLoopLabel($name: String!) {
  issueLabels(first: 5, filter: { name: { eqIgnoreCase: $name } }) { nodes { id name } }
}
"""

_UPDATE_STATE = """
mutation LocusLoopState($id: String!, $stateId: String!) {
  issueUpdate(id: $id, input: { stateId: $stateId }) { success }
}
"""

_ADD_LABEL = """
mutation LocusLoopAddLabel($id: String!, $labelId: String!) {
  issueAddLabel(id: $id, labelId: $labelId) { success }
}
"""

_COMMENT = """
mutation LocusLoopComment($issueId: String!, $body: String!) {
  commentCreate(input: { issueId: $issueId, body: $body }) { success }
}
"""

_ATTACH_LINK = """
mutation LocusLoopLink($issueId: String!, $url: String!, $title: String) {
  attachmentLinkURL(issueId: $issueId, url: $url, title: $title) { success }
}
"""

_FIND_BY_TEXT = """
query LocusLoopFindByText($text: String!) {
  issues(first: 1, filter: { description: { contains: $text } }) { nodes { identifier } }
}
"""

_PROJECT_QUERY = """
query LocusLoopProject($slug: String!) {
  projects(first: 1, filter: { slugId: { eq: $slug } }) { nodes { id } }
}
"""

_CREATE_ISSUE = """
mutation LocusLoopCreateIssue($input: IssueCreateInput!) {
  issueCreate(input: $input) { success issue { identifier } }
}
"""

_MAX_PAGES = 10
_ISSUE_TITLE_MAX = 200
_ISSUE_BODY_MAX = 8000


def _issue_from_node(node: dict[str, Any]) -> LinearIssue:
    labels = tuple(
        str(n.get("name") or "") for n in ((node.get("labels") or {}).get("nodes") or [])
    )
    comments = [str(n.get("body") or "") for n in ((node.get("comments") or {}).get("nodes") or [])]
    try:
        priority = int(node.get("priority") or 0)
    except (TypeError, ValueError):
        priority = 0
    return LinearIssue(
        id=str(node.get("id") or ""),
        identifier=str(node.get("identifier") or ""),
        title=str(node.get("title") or ""),
        description=str(node.get("description") or ""),
        priority=priority,
        url=str(node.get("url") or ""),
        state=str((node.get("state") or {}).get("name") or ""),
        labels=labels,
        created_at=str(node.get("createdAt") or ""),
        team_id=str((node.get("team") or {}).get("id") or ""),
        claims=parse_claims(comments),
    )


class LinearClient:
    """Typed Linear GraphQL client (httpx). Inject ``transport`` in tests.

    ``api_key`` is resolved lazily (``key_resolver``) so constructing a client
    never touches the keychain; the key is held only for the request header.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        key_resolver: Callable[[], str] = resolve_linear_key,
        endpoint: str = LINEAR_GRAPHQL_URL,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 30.0,
        retry_attempts: int = 3,
        retry_base_delay: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._api_key = api_key
        self._key_resolver = key_resolver
        self.endpoint = endpoint
        self._transport = transport
        self._timeout = timeout
        self._retry_attempts = retry_attempts
        self._retry_base_delay = retry_base_delay
        self._sleep = sleep
        self._http: httpx.Client | None = None
        self._state_cache: dict[str, dict[str, str]] = {}
        self._label_cache: dict[str, str] = {}

    def __repr__(self) -> str:  # never print the key
        return f"LinearClient(endpoint={self.endpoint!r})"

    # -- transport -------------------------------------------------------------
    def _client(self) -> httpx.Client:
        if self._http is None:
            self._http = httpx.Client(timeout=self._timeout, transport=self._transport)
        return self._http

    def close(self) -> None:
        if self._http is not None:
            self._http.close()
            self._http = None

    def _key(self) -> str:
        if self._api_key is None:
            self._api_key = self._key_resolver()
        return self._api_key

    def _post_once(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        headers = {"Authorization": self._key(), "Content-Type": "application/json"}
        try:
            response = self._client().post(
                self.endpoint, json={"query": query, "variables": variables}, headers=headers
            )
        except httpx.TransportError as exc:
            raise LinearError(f"Linear unreachable ({type(exc).__name__})", transient=True) from exc
        if response.status_code == 429 or response.status_code >= 500:
            raise LinearError(f"Linear HTTP {response.status_code}", transient=True)
        if response.status_code in (401, 403):
            raise LinearNotConfigured(f"Linear rejected the API key (HTTP {response.status_code})")
        if response.status_code >= 400:
            raise LinearError(f"Linear HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise LinearError("Linear returned invalid JSON", transient=True) from exc
        errors = payload.get("errors") if isinstance(payload, dict) else None
        if errors:
            codes = {str(((e or {}).get("extensions") or {}).get("code") or "") for e in errors}
            transient = bool(codes & {"RATELIMITED", "INTERNAL_SERVER_ERROR"})
            messages = "; ".join(str((e or {}).get("message") or "")[:200] for e in errors[:3])
            raise LinearError(f"Linear GraphQL error: {messages}", transient=transient)
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            raise LinearError("Linear response has no data")
        return data

    def graphql(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        return with_retry(
            lambda: self._post_once(query, dict(variables or {})),
            attempts=self._retry_attempts,
            base_delay=self._retry_base_delay,
            sleep=self._sleep,
        )

    # -- reads -------------------------------------------------------------------
    def list_candidate_issues(
        self, project_slug: str, *, active_states: Sequence[str], label: str = ELIGIBLE_LABEL
    ) -> list[LinearIssue]:
        """Issues in the project carrying ``label`` in an active state (unfiltered
        for exclusions -- :func:`eligible_issues` applies those)."""
        issues: list[LinearIssue] = []
        after: str | None = None
        for _ in range(_MAX_PAGES):
            data = self.graphql(
                _CANDIDATES_QUERY,
                {
                    "slug": project_slug,
                    "states": list(active_states),
                    "label": label,
                    "after": after,
                },
            )
            page = data.get("issues") or {}
            issues.extend(_issue_from_node(n) for n in page.get("nodes") or [])
            info = page.get("pageInfo") or {}
            if not info.get("hasNextPage"):
                break
            after = str(info.get("endCursor") or "") or None
            if after is None:
                break
        return issues

    def get_issue(self, issue_id: str) -> LinearIssue:
        data = self.graphql(_ISSUE_QUERY, {"id": issue_id})
        node = data.get("issue")
        if not isinstance(node, dict):
            raise LinearError(f"Linear issue {issue_id!r} not found")
        return _issue_from_node(node)

    def fetch_spec_dict(self, issue_id: str) -> dict[str, Any]:
        """``LinearSpecSource`` fetcher: ``LinearSpecSource(id, fetcher=client.fetch_spec_dict)``."""
        return self.get_issue(issue_id).as_spec_dict()

    def _team_states(self, issue_id: str) -> dict[str, str]:
        if issue_id not in self._state_cache:
            data = self.graphql(_TEAM_STATES_QUERY, {"id": issue_id})
            nodes = (((data.get("issue") or {}).get("team") or {}).get("states") or {}).get(
                "nodes"
            ) or []
            self._state_cache[issue_id] = {
                str(n.get("name") or "").strip().lower(): str(n.get("id") or "") for n in nodes
            }
        return self._state_cache[issue_id]

    def has_state(self, issue_id: str, state_name: str) -> bool:
        return state_name.strip().lower() in self._team_states(issue_id)

    # -- writes ------------------------------------------------------------------
    def transition(self, issue_id: str, state_name: str) -> None:
        state_id = self._team_states(issue_id).get(state_name.strip().lower())
        if not state_id:
            raise LinearError(f"workflow state {state_name!r} does not exist for this team")
        self._mutate(_UPDATE_STATE, {"id": issue_id, "stateId": state_id}, "issueUpdate")

    def add_label(self, issue_id: str, label_name: str) -> None:
        key = label_name.strip().lower()
        if key not in self._label_cache:
            data = self.graphql(_LABEL_QUERY, {"name": label_name})
            nodes = (data.get("issueLabels") or {}).get("nodes") or []
            if not nodes:
                raise LinearError(f"label {label_name!r} does not exist in this workspace")
            self._label_cache[key] = str(nodes[0].get("id") or "")
        self._mutate(
            _ADD_LABEL, {"id": issue_id, "labelId": self._label_cache[key]}, "issueAddLabel"
        )

    def add_comment(self, issue_id: str, body: str) -> None:
        self._mutate(_COMMENT, {"issueId": issue_id, "body": body}, "commentCreate")

    def attach_link(self, issue_id: str, url: str, title: str = "") -> None:
        self._mutate(
            _ATTACH_LINK,
            {"issueId": issue_id, "url": url, "title": title or None},
            "attachmentLinkURL",
        )

    def _mutate(self, query: str, variables: dict[str, Any], field_name: str) -> None:
        data = self.graphql(query, variables)
        result = data.get(field_name) or {}
        if result.get("success") is not True:
            raise LinearError(f"Linear {field_name} did not succeed")

    # -- failure-pattern filing (LOCUS-339) ----------------------------------------
    def find_issue_with_text(self, text: str) -> str | None:
        """The identifier of an issue whose description contains ``text`` (dedupe marker)."""
        data = self.graphql(_FIND_BY_TEXT, {"text": str(text)[:200]})
        nodes = (data.get("issues") or {}).get("nodes") or []
        return str(nodes[0].get("identifier") or "") or None if nodes else None

    def create_issue(
        self, *, team_id: str, title: str, description: str, project_slug: str = ""
    ) -> str:
        """Create an issue (no labels: it is triaged by a human). Returns its identifier."""
        if not str(team_id or "").strip():
            raise LinearError("cannot create an issue without a team")
        payload: dict[str, Any] = {
            "teamId": team_id,
            "title": str(title)[:_ISSUE_TITLE_MAX],
            "description": str(description)[:_ISSUE_BODY_MAX],
        }
        if project_slug:
            data = self.graphql(_PROJECT_QUERY, {"slug": project_slug})
            nodes = (data.get("projects") or {}).get("nodes") or []
            if nodes and nodes[0].get("id"):
                payload["projectId"] = str(nodes[0]["id"])
        data = self.graphql(_CREATE_ISSUE, {"input": payload})
        result = data.get("issueCreate") or {}
        if result.get("success") is not True:
            raise LinearError("Linear issueCreate did not succeed")
        return str((result.get("issue") or {}).get("identifier") or "")
