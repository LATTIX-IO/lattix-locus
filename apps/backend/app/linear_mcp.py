"""Small, schema-driven adapter for Linear's official MCP server.

Tool schemas are discovered at runtime because Linear owns the server's tool
catalog. Unknown or unsupported operations fail closed; this adapter never
falls back to Linear's GraphQL API.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from typing import Any, Protocol


class LinearMcpError(RuntimeError):
    """A required Linear MCP capability is unavailable or returned invalid data."""


class McpServer(Protocol):
    def list_tools(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None, *, decision: Any = None
    ) -> str:
        raise NotImplementedError


AuthorizeCall = Callable[[str, dict[str, Any]], Any]

_TOOL_NAMES: dict[str, tuple[str, ...]] = {
    "list_issues": ("list_issues", "search_issues", "issues_list"),
    "get_issue": ("get_issue", "issue_get"),
    "list_projects": ("list_projects", "projects_list"),
    "list_statuses": ("list_issue_statuses", "list_workflow_states", "list_statuses"),
    "update_issue": ("update_issue", "save_issue", "issue_update"),
    "list_comments": ("list_comments", "issue_comments"),
    "create_comment": ("create_comment", "add_comment", "comment_create"),
    "add_label": ("add_issue_label", "add_label", "issue_add_label"),
    "attach_link": ("create_attachment", "attach_link", "create_link"),
    "create_issue": ("create_issue", "save_issue", "issue_create"),
}

_ARGUMENTS: dict[str, tuple[str, ...]] = {
    "id": ("id", "issueId", "issue_id", "identifier"),
    "project": ("project", "projectSlug", "project_slug"),
    "project_id": ("projectId", "project_id"),
    "project_slug": ("projectSlug", "project_slug", "project"),
    "team": ("team",),
    "team_id": ("teamId", "team_id", "team"),
    "state": ("state", "status", "stateName", "state_name"),
    "state_id": ("stateId", "state_id"),
    "state_name": ("stateName", "state_name", "state", "status"),
    "labels": (
        "labelIds",
        "label_ids",
        "labels",
        "label",
        "labelId",
        "label_id",
        "labelName",
        "label_name",
    ),
    "body": ("body", "content", "text"),
    "title": ("title", "name"),
    "description": ("description", "body", "content"),
    "url": ("url", "link"),
    "limit": ("limit", "first", "pageSize", "page_size"),
    "cursor": ("cursor", "after", "pageCursor", "page_cursor"),
    "query": ("query", "search", "text"),
    "priority": ("priority",),
}

_REQUIRED_ARGUMENT_GROUPS: dict[str, tuple[tuple[str, ...], ...]] = {
    "list_issues": (("project_id", "project_slug"),),
    "get_issue": (("id",),),
    "list_comments": (("id",),),
    "list_statuses": (("team_id",),),
    "update_issue": (("id",),),
    "create_comment": (("id",), ("body",)),
    "add_label": (("id",), ("labels",)),
    "attach_link": (("id",), ("url",)),
    "create_issue": (("team_id",), ("project_id", "project_slug"), ("title",), ("description",)),
}


def _key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def _json_value(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        # MCP servers may decorate JSON text with a short human-readable prefix.
        decoder = json.JSONDecoder()
        for start, char in enumerate(text):
            if char not in "[{":
                continue
            try:
                parsed, _end = decoder.raw_decode(text[start:])
                return parsed
            except ValueError:
                continue
        return None
    return parsed


def _as_items(value: Any, *keys: str) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if not isinstance(value, dict):
        return []
    for key in keys:
        nested = value.get(key)
        if isinstance(nested, list):
            return [item for item in nested if isinstance(item, dict)]
        if isinstance(nested, dict):
            found = _as_items(
                nested,
                "nodes",
                "issues",
                "projects",
                "statuses",
                "states",
                "comments",
                "items",
                "data",
            )
            if found:
                return found
    for key in (
        "nodes",
        "issues",
        "projects",
        "statuses",
        "states",
        "comments",
        "items",
        "results",
        "data",
    ):
        nested = value.get(key)
        if isinstance(nested, list):
            return [item for item in nested if isinstance(item, dict)]
    return []


def _page_cursor(value: Any) -> tuple[str, bool, bool]:
    """Return the next opaque page cursor without assuming a provider response shape."""
    if not isinstance(value, dict):
        return "", False, False
    metadata: list[dict[str, Any]] = [value]
    for key in ("pagination", "pageInfo", "page_info", "meta", "metadata", "data"):
        nested = value.get(key)
        if isinstance(nested, dict):
            metadata.append(nested)
            for nested_key in ("pagination", "pageInfo", "page_info"):
                page = nested.get(nested_key)
                if isinstance(page, dict):
                    metadata.append(page)
    for item in metadata:
        for key in ("nextCursor", "next_cursor", "nextPageCursor", "next_page_cursor"):
            cursor = str(item.get(key) or "").strip()
            if cursor:
                return cursor, True, True
        more_keys = ("hasNextPage", "has_next_page", "hasMore", "has_more")
        known = any(key in item and isinstance(item.get(key), bool) for key in more_keys)
        has_more = any(item.get(key) is True for key in more_keys)
        if has_more:
            cursor = str(item.get("endCursor") or item.get("end_cursor") or "").strip()
            return cursor, True, True
        if known:
            return "", False, True
    return "", False, False


def normalize_issue(node: Mapping[str, Any]) -> dict[str, Any]:
    state = node.get("state") or node.get("status") or ""
    if isinstance(state, dict):
        state_name = str(state.get("name") or state.get("label") or "")
        state_id = str(state.get("id") or "")
    else:
        state_name, state_id = str(state), ""
    labels_value = node.get("labels") or []
    if isinstance(labels_value, dict):
        labels_value = labels_value.get("nodes") or labels_value.get("items") or []
    labels: list[str] = []
    if isinstance(labels_value, list):
        for item in labels_value:
            if isinstance(item, dict):
                labels.append(str(item.get("name") or item.get("label") or item))
            elif isinstance(item, str):
                labels.append(item)
    team_value = node.get("team")
    team = team_value if isinstance(team_value, dict) else {}
    comments = node.get("comments") or []
    if isinstance(comments, dict):
        comments = comments.get("nodes") or comments.get("items") or []
    return {
        "id": str(node.get("id") or node.get("issueId") or node.get("identifier") or ""),
        "identifier": str(node.get("identifier") or node.get("key") or ""),
        "title": str(node.get("title") or node.get("name") or ""),
        "description": str(node.get("description") or node.get("body") or ""),
        "priority": _priority(node.get("priority")),
        "url": str(node.get("url") or node.get("link") or ""),
        "state": state_name,
        "state_id": state_id,
        "labels": labels,
        "team_id": str(team.get("id") or node.get("teamId") or node.get("team_id") or ""),
        "created_at": str(node.get("createdAt") or node.get("created_at") or ""),
        "comments": [
            str(item.get("body") or item.get("text") or "")
            for item in comments
            if isinstance(item, dict)
        ]
        if isinstance(comments, list)
        else [],
    }


def _priority(value: Any) -> int:
    if isinstance(value, dict):
        value = value.get("value") or value.get("level") or value.get("priority")
    try:
        result = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return result if 0 <= result <= 4 else 0


class LinearMcpAdapter:
    """Discover and call a bounded set of Linear issue tools over MCP."""

    def __init__(self, server: McpServer, authorize: AuthorizeCall) -> None:
        self.server = server
        self.authorize = authorize
        self._tools: dict[str, dict[str, Any]] = {}
        self._projects: dict[str, dict[str, str]] = {}

    def _tool(self, operation: str) -> tuple[str, dict[str, Any]]:
        if not self._tools:
            self._tools = {
                _key(str(tool.get("name") or "")): tool for tool in self.server.list_tools()
            }
        candidates = _TOOL_NAMES.get(operation, ())
        for candidate in candidates:
            tool = self._tools.get(_key(candidate))
            if tool is not None:
                schema = tool.get("input_schema") or tool.get("inputSchema") or {}
                return str(tool["name"]), schema if isinstance(schema, dict) else {}
        raise LinearMcpError(f"Linear MCP does not expose the required '{operation}' tool")

    def _supports_argument(self, operation: str, semantic: str) -> bool:
        _name, schema = self._tool(operation)
        raw_properties = schema.get("properties")
        properties: dict[str, Any] = raw_properties if isinstance(raw_properties, dict) else {}
        aliases = _ARGUMENTS.get(semantic, (semantic,))
        return any(_key(alias) in {_key(str(prop)) for prop in properties} for alias in aliases)

    def _call(self, operation: str, values: Mapping[str, Any]) -> Any:
        name, schema = self._tool(operation)
        raw_properties = schema.get("properties")
        properties: dict[str, Any] = raw_properties if isinstance(raw_properties, dict) else {}
        raw_required = schema.get("required")
        required: list[Any] = raw_required if isinstance(raw_required, list) else []
        normalized_props = {_key(str(prop)): str(prop) for prop in properties}
        args: dict[str, Any] = {}
        for semantic, value in values.items():
            aliases = _ARGUMENTS.get(semantic, (semantic,))
            target = next(
                (
                    normalized_props[_key(alias)]
                    for alias in aliases
                    if _key(alias) in normalized_props
                ),
                None,
            )
            if (
                target is not None
                and value is not None
                and not (isinstance(value, str) and not value.strip())
            ):
                target_schema = properties.get(target)
                target_type = target_schema.get("type") if isinstance(target_schema, dict) else None
                if target_type == "string" and isinstance(value, (list, tuple)) and len(value) == 1:
                    value = value[0]
                elif target_type == "array" and isinstance(value, str):
                    value = [value]
                args[target] = value
        missing = [str(field) for field in required if str(field) not in args]
        missing_groups = [
            group
            for group in _REQUIRED_ARGUMENT_GROUPS.get(operation, ())
            if not any(
                normalized_props.get(_key(alias)) in args
                for semantic in group
                for alias in _ARGUMENTS.get(semantic, (semantic,))
            )
        ]
        if missing or missing_groups:
            raise LinearMcpError(f"Linear MCP tool '{name}' requires unsupported arguments")
        authorization = self.authorize(name, args)
        cleanup = None
        decision = authorization
        if isinstance(authorization, tuple) and len(authorization) == 2:
            decision, cleanup = authorization
        try:
            result = self.server.call_tool(name, args, decision=decision)
        finally:
            if callable(cleanup):
                cleanup()
        parsed = _json_value(result)
        if parsed is None:
            if (
                operation in {"update_issue", "create_comment", "add_label", "attach_link"}
                and str(result or "").strip()
            ):
                # MCP writes may acknowledge success with plain text. McpHttpClient
                # raises for isError, so retain this only for non-create mutations.
                return {"message": str(result)[:1000]}
            raise LinearMcpError(f"Linear MCP tool '{name}' returned no structured data")
        return parsed

    def _list_pages(
        self,
        operation: str,
        values: Mapping[str, Any],
        *,
        result_key: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        max_items = min(5000, max(1, int(limit)))
        rows: list[dict[str, Any]] = []
        cursor = ""
        seen_cursors: set[str] = set()
        for _page in range(51):
            page_limit = min(100, max_items - len(rows))
            if page_limit <= 0:
                raise LinearMcpError(f"Linear MCP {operation} exceeded the bounded result limit")
            page_values = {**values, "limit": page_limit, "cursor": cursor or None}
            result = self._call(operation, page_values)
            page_items = _as_items(result, result_key)
            rows.extend(page_items)
            if len(rows) > max_items:
                raise LinearMcpError(f"Linear MCP {operation} exceeded the bounded result limit")
            next_cursor, has_more, pagination_known = _page_cursor(result)
            if not has_more:
                if (
                    not pagination_known
                    and len(page_items) >= page_limit
                    and self._supports_argument(operation, "cursor")
                ):
                    raise LinearMcpError(
                        f"Linear MCP {operation} returned a full page without completion metadata"
                    )
                return rows
            if not next_cursor or not self._supports_argument(operation, "cursor"):
                raise LinearMcpError(
                    f"Linear MCP {operation} reports another page without a usable cursor"
                )
            if next_cursor in seen_cursors:
                raise LinearMcpError(f"Linear MCP {operation} did not advance its page cursor")
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        raise LinearMcpError(f"Linear MCP {operation} exceeded the page limit")

    def issues(self, project_slug: str, *, limit: int = 1000) -> list[dict[str, Any]]:
        project_id = self._resolve_project(project_slug)
        items = self._list_pages(
            "list_issues",
            {
                "project_id": project_id or None,
                "project_slug": project_slug,
                "query": "",
            },
            result_key="issues",
            limit=limit,
        )
        normalized = [normalize_issue(item) for item in items]
        return [item for item in normalized if item["id"]]

    def comments(self, issue_id: str, *, limit: int = 5000) -> list[str]:
        items = self._list_pages(
            "list_comments",
            {"id": issue_id},
            result_key="comments",
            limit=limit,
        )
        return [
            str(item.get("body") or item.get("text") or "")
            for item in items
            if str(item.get("body") or item.get("text") or "")
        ]

    def issue(self, issue_id: str, *, include_comments: bool = False) -> dict[str, Any]:
        result = self._call("get_issue", {"id": issue_id})
        if isinstance(result, dict):
            candidate = result.get("issue")
            node: Mapping[str, Any] = candidate if isinstance(candidate, dict) else result
            normalized = normalize_issue(node)
            if normalized["id"]:
                if include_comments:
                    normalized["comments"] = self.comments(issue_id)
                return normalized
        raise LinearMcpError("Linear MCP did not return the requested issue")

    def statuses(self, team_id: str) -> list[dict[str, str]]:
        result = self._call("list_statuses", {"team_id": team_id})
        items = _as_items(result, "statuses", "states")
        return [
            {
                "id": str(item.get("id") or ""),
                "name": str(item.get("name") or item.get("label") or ""),
                "type": str(item.get("type") or ""),
            }
            for item in items
            if str(item.get("name") or item.get("label") or "").strip()
        ]

    def _project(self, project_slug: str) -> dict[str, str]:
        cache_key = project_slug.lower()
        if cache_key in self._projects:
            return self._projects[cache_key]
        try:
            result = self._call("list_projects", {"query": project_slug, "limit": 100})
        except LinearMcpError:
            return {"id": "", "team_id": ""}
        for item in _as_items(result, "projects"):
            slug = str(item.get("slugId") or item.get("slug") or item.get("key") or "")
            if slug.lower() == project_slug.lower():
                teams = item.get("teams") or item.get("team") or []
                if isinstance(teams, dict):
                    teams = teams.get("nodes") or teams.get("items") or [teams]
                team_ids = (
                    {
                        str(team.get("id") or "")
                        for team in teams
                        if isinstance(team, dict) and str(team.get("id") or "")
                    }
                    if isinstance(teams, list)
                    else set()
                )
                direct_team = str(item.get("teamId") or item.get("team_id") or "")
                project = {
                    "id": str(item.get("id") or ""),
                    "team_id": direct_team or (next(iter(team_ids)) if len(team_ids) == 1 else ""),
                }
                self._projects[cache_key] = project
                return project
        return {"id": "", "team_id": ""}

    def _resolve_project(self, project_slug: str) -> str:
        return self._project(project_slug)["id"]

    def project_team_id(self, project_slug: str) -> str:
        """Return a project team only when Linear identifies exactly one team."""
        return self._project(project_slug)["team_id"]

    def transition(self, issue_id: str, state_name: str) -> None:
        issue = self.issue(issue_id)
        state_id = ""
        if issue["team_id"]:
            try:
                state_id = next(
                    (
                        item["id"]
                        for item in self.statuses(issue["team_id"])
                        if item["name"].strip().lower() == state_name.strip().lower()
                    ),
                    "",
                )
            except LinearMcpError:
                # Some MCP servers cannot enumerate team workflow statuses;
                # update_issue can still resolve the requested status by name.
                pass
        self._call(
            "update_issue",
            {"id": issue_id, "state_id": state_id or None, "state_name": state_name},
        )
        updated = self.issue(issue_id)
        if updated["state"].strip().lower() != state_name.strip().lower():
            raise LinearMcpError("Linear MCP did not confirm the requested issue status")

    def add_comment(self, issue_id: str, body: str) -> None:
        self._call("create_comment", {"id": issue_id, "body": body})

    def add_label(self, issue_id: str, label_name: str) -> None:
        try:
            self._call("add_label", {"id": issue_id, "labels": [label_name]})
        except LinearMcpError:
            self._call("update_issue", {"id": issue_id, "labels": [label_name]})
        labels = {str(item).strip().lower() for item in self.issue(issue_id)["labels"]}
        if label_name.strip().lower() not in labels:
            raise LinearMcpError("Linear MCP did not confirm the requested issue label")

    def attach_link(self, issue_id: str, url: str, title: str = "") -> None:
        self._call("attach_link", {"id": issue_id, "url": url, "title": title})

    def set_priority(self, issue_id: str, priority: int) -> None:
        requested = min(4, max(0, int(priority)))
        self._call("update_issue", {"id": issue_id, "priority": requested})
        if self.issue(issue_id)["priority"] != requested:
            raise LinearMcpError("Linear MCP did not confirm the requested issue priority")

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
        project_id = self._resolve_project(project_slug) if project_slug else ""
        result = self._call(
            "create_issue",
            {
                "team_id": team_id,
                "project_id": project_id or None,
                "project_slug": project_slug or None,
                "title": title[:200],
                "description": description[:8000],
            },
        )
        issue = result.get("issue") if isinstance(result, dict) else None
        if not isinstance(issue, dict) and isinstance(result, dict):
            issue = result if result.get("id") or result.get("identifier") else None
        issue_id = str((issue or {}).get("id") or (issue or {}).get("identifier") or "")
        if not issue_id and isinstance(result, dict):
            match = re.search(r"\b[A-Z][A-Z0-9]+-\d+\b", str(result.get("message") or ""))
            issue_id = match.group(0) if match else ""
        if not issue_id:
            raise LinearMcpError("Linear MCP did not return the created issue id")
        self.set_priority(issue_id, priority)
        if state_name:
            self.transition(issue_id, state_name)
        if label_name:
            self.add_label(issue_id, label_name)
        return str((issue or {}).get("identifier") or issue_id)
