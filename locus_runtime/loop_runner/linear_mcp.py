"""Linear MCP tracker bridge for the local runner.

The backend owns the configured MCP OAuth connection and gateway. The native
runner calls its authenticated loop bridge instead of keeping a second Linear
credential or bypassing the integration's policy path.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

import httpx

from locus_runtime.loop_runner.linear import (
    LinearError,
    LinearIssue,
    LinearNotConfigured,
    parse_claims,
)


def _resolve_api_token() -> str:
    try:
        from locus_tooling.native_secrets import SecretStorageUnavailable, get_secret
    except ImportError as exc:
        raise LinearNotConfigured("Locus API credentials are unavailable for Linear MCP") from exc
    try:
        token = get_secret("LOCUS_API_BEARER_TOKEN")
    except SecretStorageUnavailable as exc:
        raise LinearNotConfigured("Locus API credentials are unavailable for Linear MCP") from exc
    if not str(token or "").strip():
        raise LinearNotConfigured(
            "Locus API credentials are unavailable; start the desktop install or configure "
            "LOCUS_API_BEARER_TOKEN in the OS secret store"
        )
    return str(token).strip()


class LinearMcpTracker:
    """Tracker protocol implementation that delegates to the authenticated backend."""

    def __init__(
        self,
        *,
        api_base_url: str | None = None,
        api_token: str | None = None,
        token_resolver: Callable[[], str] = _resolve_api_token,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 25.0,
    ) -> None:
        self.base_url = (
            str(
                api_base_url
                or os.getenv("LOCUS_LOCAL_API_BASE_URL")
                or os.getenv("NEXT_PUBLIC_API_BASE_URL")
                or "http://127.0.0.1:8000"
            )
            .strip()
            .rstrip("/")
        )
        if not self.base_url.startswith(("http://", "https://")):
            raise LinearNotConfigured("Locus API base URL must use http(s)")
        self._token = api_token
        self._token_resolver = token_resolver
        self._transport = transport
        self._timeout = timeout
        self._client: httpx.Client | None = None

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=self._timeout, transport=self._transport)
        return self._client

    def _request(self, operation: str, **payload: Any) -> Any:
        if self._token is None:
            self._token = self._token_resolver()
        try:
            response = self._http().post(
                f"{self.base_url}/loop/tracker",
                json={"operation": operation, **payload},
                headers={"Authorization": f"Bearer {self._token}"},
                follow_redirects=False,
            )
        except httpx.TransportError as exc:
            raise LinearError(
                f"Locus Linear MCP bridge unreachable ({type(exc).__name__})", transient=True
            ) from exc
        if response.status_code == 429 or response.status_code >= 500:
            raise LinearError(
                f"Locus Linear MCP bridge HTTP {response.status_code}", transient=True
            )
        if response.status_code in {401, 403, 409}:
            raise LinearNotConfigured("Locus Linear MCP connection is unavailable or unauthorized")
        if response.status_code >= 400:
            raise LinearError(f"Locus Linear MCP bridge HTTP {response.status_code}")
        try:
            result = response.json()
        except ValueError as exc:
            raise LinearError("Locus Linear MCP bridge returned invalid JSON") from exc
        return result.get("result") if isinstance(result, dict) else result

    @staticmethod
    def _issue(payload: Any) -> LinearIssue:
        if not isinstance(payload, dict):
            raise LinearError("Locus Linear MCP bridge returned an invalid issue")
        raw_comments: Any = payload.get("comments")
        comments = raw_comments if isinstance(raw_comments, list) else []
        claims = parse_claims(str(item) for item in comments)
        raw_labels: Any = payload.get("labels")
        labels = tuple(str(item) for item in raw_labels) if isinstance(raw_labels, list) else ()
        return LinearIssue(
            id=str(payload.get("id") or ""),
            identifier=str(payload.get("identifier") or ""),
            title=str(payload.get("title") or ""),
            description=str(payload.get("description") or ""),
            priority=_priority(payload.get("priority")),
            url=str(payload.get("url") or ""),
            state=str(payload.get("state") or ""),
            labels=labels,
            created_at=str(payload.get("created_at") or ""),
            team_id=str(payload.get("team_id") or ""),
            claims=claims,
        )

    def list_candidate_issues(
        self, project_slug: str, *, active_states: Any, label: str = "agent:eligible"
    ) -> list[LinearIssue]:
        result = self._request(
            "list_candidate_issues",
            project_slug=project_slug,
            active_states=list(active_states),
            label=label,
        )
        return [self._issue(item) for item in result or []]

    def list_project_issues(self, project_slug: str) -> list[LinearIssue]:
        result = self._request("list_project_issues", project_slug=project_slug)
        return [self._issue(item) for item in result or []]

    def project_team_id(self, project_slug: str) -> str:
        result = self._request("project_team_id", project_slug=project_slug)
        return str(result or "")

    def get_issue(self, issue_id: str) -> LinearIssue:
        return self._issue(self._request("get_issue", issue_id=issue_id))

    def has_state(self, issue_id: str, state_name: str) -> bool:
        return bool(self._request("has_state", issue_id=issue_id, state_name=state_name))

    def transition(self, issue_id: str, state_name: str) -> None:
        self._request("transition", issue_id=issue_id, state_name=state_name)

    def add_label(self, issue_id: str, label_name: str) -> None:
        self._request("add_label", issue_id=issue_id, label_name=label_name)

    def add_comment(self, issue_id: str, body: str) -> None:
        self._request("add_comment", issue_id=issue_id, body=body)

    def attach_link(self, issue_id: str, url: str, title: str = "") -> None:
        self._request("attach_link", issue_id=issue_id, url=url, title=title)

    def find_issue_with_text(self, text: str) -> str | None:
        result = self._request("find_issue_with_text", text=text)
        return str(result) if result else None

    def create_issue(
        self,
        *,
        team_id: str,
        title: str,
        description: str,
        project_slug: str = "",
        priority: int = 3,
        state_name: str = "Todo",
        label_name: str = "agent:eligible",
    ) -> str:
        return str(
            self._request(
                "create_issue",
                team_id=team_id,
                title=title,
                description=description,
                project_slug=project_slug,
                priority=priority,
                state_name=state_name,
                label_name=label_name,
            )
            or ""
        )

    def set_priority(self, issue_id: str, priority: int) -> None:
        self._request("set_priority", issue_id=issue_id, priority=priority)


def _priority(value: Any) -> int:
    try:
        priority = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return priority if 0 <= priority <= 4 else 0
