"""LOCUS-338: native Linear client (httpx GraphQL via MockTransport) and issue eligibility."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from locus_runtime.harness.integrations import LinearSpecSource
from locus_runtime.loop_runner.linear import (
    CLAIM_MARKER,
    RELEASE_MARKER,
    LinearClient,
    LinearError,
    LinearIssue,
    LinearNotConfigured,
    eligible_issues,
    live_claim,
    marker,
    parse_claims,
    with_retry,
)

KEY = "lin_api_test_secret_value"
NOW = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
ACTIVE = ("Todo", "In Progress", "Rework")
EXCLUDE = ("epic", "agent:ineligible", "agent:human-review-required")


def _issue(
    key: str, *, priority: int = 3, labels=("agent:eligible",), state="Todo", **kw
) -> LinearIssue:
    return LinearIssue(
        id=f"id-{key}",
        identifier=key,
        title=f"title {key}",
        priority=priority,
        labels=tuple(labels),
        state=state,
        created_at=kw.pop("created_at", "2026-01-01"),
        **kw,
    )


# --------------------------------------------------------------------------- #
# Eligibility
# --------------------------------------------------------------------------- #
def test_selection_requires_label_active_state_and_orders_by_priority() -> None:
    issues = [
        _issue("A", priority=3),
        _issue("B", priority=1),
        _issue("C", priority=0),  # no priority sorts last
        _issue("D", priority=2, labels=()),  # not eligible
        _issue("E", priority=1, state="Done"),  # terminal
        _issue("F", priority=2, state="Rework"),
    ]
    picked = eligible_issues(issues, active_states=ACTIVE, exclude_labels=EXCLUDE, now=NOW)
    assert [i.identifier for i in picked] == ["B", "F", "A", "C"]


@pytest.mark.parametrize(
    "label", ["agent:ineligible", "agent:human-review-required", "epic", "Agent:Ineligible"]
)
def test_exclusion_labels_remove_issue(label: str) -> None:
    issue = _issue("A", labels=("agent:eligible", label))
    assert eligible_issues([issue], active_states=ACTIVE, exclude_labels=EXCLUDE, now=NOW) == []


def test_exclusions_are_enforced_even_if_config_omits_them() -> None:
    issue = _issue("A", labels=("agent:eligible", "agent:human-review-required"))
    assert eligible_issues([issue], active_states=ACTIVE, exclude_labels=(), now=NOW) == []


def test_live_claim_by_another_run_excludes_until_released_or_expired() -> None:
    claimed_at = NOW - timedelta(minutes=5)
    claims = parse_claims([marker(CLAIM_MARKER, "run-other", claimed_at)])
    issue = _issue("A", state="In Progress", claims=claims)
    assert eligible_issues([issue], active_states=ACTIVE, exclude_labels=EXCLUDE, now=NOW) == []
    # own run id -> still selectable (idempotent resume)
    assert eligible_issues(
        [issue], active_states=ACTIVE, exclude_labels=EXCLUDE, now=NOW, own_run_ids=["run-other"]
    )
    # expired
    assert eligible_issues(
        [issue], active_states=ACTIVE, exclude_labels=EXCLUDE, now=NOW + timedelta(hours=3)
    )
    # released
    released = parse_claims(
        [marker(CLAIM_MARKER, "run-other", claimed_at), marker(RELEASE_MARKER, "run-other", NOW)]
    )
    assert live_claim(_issue("A", claims=released), now=NOW, ttl_seconds=7200) is None


def test_claim_markers_ignore_lookalikes() -> None:
    bodies = [
        "locus-loop:claim run_id=x at=2026",
        "<!-- locus-loop:claim run_id=bad id at=nope -->",
    ]
    assert parse_claims(bodies) == ()


# --------------------------------------------------------------------------- #
# Client over MockTransport
# --------------------------------------------------------------------------- #
def _node(key: str, **kw) -> dict:
    return {
        "id": f"id-{key}",
        "identifier": key,
        "title": f"title {key}",
        "description": "Fix it.",
        "priority": kw.get("priority", 2),
        "url": f"https://linear.app/x/issue/{key}",
        "createdAt": "2026-01-01T00:00:00Z",
        "state": {"name": kw.get("state", "Todo")},
        "team": {"id": "team-1"},
        "labels": {"nodes": [{"name": n} for n in kw.get("labels", ["agent:eligible"])]},
        "comments": {"nodes": [{"body": b} for b in kw.get("comments", [])]},
    }


class FakeLinearServer:
    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.headers: list[httpx.Headers] = []
        self.fail_next: list[int] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.headers.append(request.headers)
        body = json.loads(request.content)
        self.requests.append(body)
        if self.fail_next:
            return httpx.Response(self.fail_next.pop(0), json={})
        q = body["query"]
        if "LocusLoopCandidates" in q:
            return httpx.Response(
                200,
                json={
                    "data": {
                        "issues": {"pageInfo": {"hasNextPage": False}, "nodes": [_node("LOC-1")]}
                    }
                },
            )
        if "LocusLoopIssue" in q:
            return httpx.Response(200, json={"data": {"issue": _node("LOC-1")}})
        if "LocusLoopStates" in q:
            states = [{"id": "s-todo", "name": "Todo"}, {"id": "s-prog", "name": "In Progress"}]
            return httpx.Response(
                200, json={"data": {"issue": {"team": {"states": {"nodes": states}}}}}
            )
        if "LocusLoopLabel" in q:
            return httpx.Response(
                200,
                json={
                    "data": {
                        "issueLabels": {
                            "nodes": [{"id": "l-1", "name": "agent:human-review-required"}]
                        }
                    }
                },
            )
        for field_name in ("issueUpdate", "issueAddLabel", "commentCreate", "attachmentLinkURL"):
            if field_name in q:
                return httpx.Response(200, json={"data": {field_name: {"success": True}}})
        return httpx.Response(200, json={"errors": [{"message": "unknown"}]})


def _client(server: FakeLinearServer, **kw) -> LinearClient:
    return LinearClient(
        api_key=KEY, transport=httpx.MockTransport(server.handler), sleep=lambda _s: None, **kw
    )


def test_list_get_and_writes_send_expected_graphql() -> None:
    server = FakeLinearServer()
    client = _client(server)
    issues = client.list_candidate_issues("3b160e533200", active_states=ACTIVE)
    assert [i.identifier for i in issues] == ["LOC-1"]
    first = server.requests[0]["variables"]
    assert first["slug"] == "3b160e533200" and first["label"] == "agent:eligible"
    assert first["states"] == list(ACTIVE)

    client.transition("id-LOC-1", "In Progress")
    client.add_label("id-LOC-1", "agent:human-review-required")
    client.add_comment("id-LOC-1", "hello")
    client.attach_link("id-LOC-1", "https://github.com/o/r/pull/1", "PR #1")
    sent = {
        next(
            k
            for k in ("issueUpdate", "issueAddLabel", "commentCreate", "attachmentLinkURL")
            if k in r["query"]
        ): r["variables"]
        for r in server.requests
        if "mutation" in r["query"]
    }
    assert sent["issueUpdate"] == {"id": "id-LOC-1", "stateId": "s-prog"}
    assert sent["issueAddLabel"] == {"id": "id-LOC-1", "labelId": "l-1"}
    assert sent["commentCreate"]["body"] == "hello"
    assert sent["attachmentLinkURL"]["url"].endswith("/pull/1")
    # Linear personal keys go in the Authorization header as-is.
    assert all(h["authorization"] == KEY for h in server.headers)


def test_linear_spec_source_uses_the_client_as_fetcher() -> None:
    client = _client(FakeLinearServer())
    spec = LinearSpecSource("id-LOC-1", fetcher=client.fetch_spec_dict).fetch_spec()
    assert spec.id == "LOC-1" and spec.source == "linear" and spec.body == "Fix it."


def test_transient_errors_retry_with_backoff_then_succeed() -> None:
    server = FakeLinearServer()
    server.fail_next = [503, 429]
    sleeps: list[float] = []
    client = LinearClient(
        api_key=KEY, transport=httpx.MockTransport(server.handler), sleep=sleeps.append
    )
    assert client.get_issue("id-LOC-1").identifier == "LOC-1"
    assert len(sleeps) == 2 and sleeps[1] > sleeps[0] * 0.9


def test_auth_failure_is_not_retried_and_never_leaks_the_key() -> None:
    server = FakeLinearServer()
    server.fail_next = [401]
    with pytest.raises(LinearNotConfigured) as excinfo:
        _client(server).get_issue("id-LOC-1")
    assert KEY not in str(excinfo.value) and KEY not in repr(_client(server))
    assert len(server.requests) == 1


def test_missing_state_or_unsuccessful_mutation_raises() -> None:
    client = _client(FakeLinearServer())
    with pytest.raises(LinearError):
        client.transition("id-LOC-1", "Nonexistent")


def test_key_resolves_lazily_through_native_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    import locus_tooling.native_secrets as ns

    monkeypatch.setattr(ns, "get_secret", lambda name, app_home=None: None)
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    client = LinearClient(transport=httpx.MockTransport(FakeLinearServer().handler))
    with pytest.raises(LinearNotConfigured):
        client.get_issue("x")
    monkeypatch.setattr(
        ns,
        "get_secret",
        lambda name, app_home=None: "from-keychain" if name == "LINEAR_API_KEY" else None,
    )
    server = FakeLinearServer()
    LinearClient(transport=httpx.MockTransport(server.handler)).get_issue("x")
    assert server.headers[0]["authorization"] == "from-keychain"


def test_with_retry_raises_non_transient_immediately() -> None:
    calls = []

    def op():
        calls.append(1)
        raise LinearError("bad", transient=False)

    with pytest.raises(LinearError):
        with_retry(op, sleep=lambda _s: None)
    assert len(calls) == 1
