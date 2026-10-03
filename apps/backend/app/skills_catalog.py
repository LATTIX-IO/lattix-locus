"""Bundled skills and the preloaded integration catalog.

Skills use the open Agent Skills format (D-18, LOCUS-340): a folder with a
``SKILL.md`` (YAML frontmatter + markdown body) plus optional ``scripts/``,
``references/`` and ``assets/``, parsed and validated by
:mod:`locus_runtime.skills`. The bundled set below is adapted from Symphony's
`.codex/skills/` catalog; each seed is rendered to SKILL.md and loaded through
the same parser as an imported skill, so seeds always conform to the format.

The integration catalog preloads well-known MCP servers and APIs so builders
start from a vetted list; custom integrations remain fully supported through
the existing integrations CRUD.
"""

from __future__ import annotations

import base64
import binascii
import re
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from typing import Any
from urllib.parse import urlsplit

from locus_runtime import skills as skill_format

SKILL_SEEDS: list[dict[str, Any]] = [
    {
        "id": "skill-commit",
        "name": "commit",
        "description": "Create well-formed git commits with rationale and verification notes.",
        "tags": ["git", "delivery"],
        "content": (
            "## Goal\n"
            "Produce a reviewable commit for the current working-tree changes.\n\n"
            "## Steps\n"
            "1. Review the full diff before committing; group unrelated changes into separate commits.\n"
            "2. Write the subject as `type(scope): summary` (feat, fix, chore, docs, refactor, test).\n"
            "3. In the body, explain the rationale (why, not just what) and list how the change was verified.\n"
            "4. Never skip hooks or signing. If a hook fails, fix the cause instead of bypassing it.\n\n"
            "## Output\n"
            "A single commit whose message lets a reviewer understand the change without reading the diff."
        ),
    },
    {
        "id": "skill-push-pr",
        "name": "push",
        "description": "Push a branch and open or update a pull request with a complete description.",
        "tags": ["git", "delivery"],
        "content": (
            "## Goal\n"
            "Publish local commits and ensure an up-to-date pull request exists.\n\n"
            "## Steps\n"
            "1. Push the current branch to origin (never force-push shared branches).\n"
            "2. Create the PR if missing; otherwise update its description to match the current state.\n"
            "3. The PR body must cover: problem, approach, verification evidence, and any follow-ups.\n"
            "4. Link the tracking issue and request the appropriate reviewers.\n\n"
            "## Output\n"
            "A pull request whose description is current and self-contained."
        ),
    },
    {
        "id": "skill-pull-reconcile",
        "name": "pull",
        "description": "Safely reconcile a local branch that diverged from its remote.",
        "tags": ["git"],
        "content": (
            "## Goal\n"
            "Bring the local branch up to date without losing work.\n\n"
            "## Steps\n"
            "1. Fetch and inspect divergence before acting (`ahead`/`behind` counts).\n"
            "2. Prefer rebase for local-only commits; merge when the branch is shared.\n"
            "3. Resolve conflicts file by file; re-run the focused tests for every conflicted area.\n"
            "4. Never resolve a conflict by discarding changes you do not understand.\n\n"
            "## Output\n"
            "A reconciled branch with verification evidence for conflicted areas."
        ),
    },
    {
        "id": "skill-land",
        "name": "land",
        "description": "Land an approved pull request and confirm post-merge health.",
        "tags": ["git", "delivery"],
        "content": (
            "## Goal\n"
            "Merge an approved PR and verify nothing regressed.\n\n"
            "## Steps\n"
            "1. Confirm approvals and green required checks before merging.\n"
            "2. Use the repository's preferred merge strategy; keep the merge message meaningful.\n"
            "3. After merge, watch the main-branch checks; if they fail, revert first and investigate second.\n"
            "4. Close or transition the tracking issue with a short outcome note.\n\n"
            "## Output\n"
            "A merged change with healthy main-branch checks and an updated tracker."
        ),
    },
    {
        "id": "skill-issue-tracker",
        "name": "issue-tracker",
        "description": "Keep the issue tracker authoritative: statuses, comments, and handoffs.",
        "tags": ["tracker", "process"],
        "content": (
            "## Goal\n"
            "The tracker reflects reality at every step of the work.\n\n"
            "## Steps\n"
            "1. Move the issue to the in-progress state when work starts.\n"
            "2. Comment with substantive progress: decisions made, blockers found, links to artifacts.\n"
            "3. On completion, hand off to the workflow-defined state (for example Human Review) — not\n"
            "   necessarily Done — and summarize what changed and how it was verified.\n"
            "4. If blocked, say precisely what input is needed and from whom.\n\n"
            "## Output\n"
            "An issue history a teammate can use to pick up the work cold."
        ),
    },
    {
        "id": "skill-debug",
        "name": "debug",
        "description": "Systematic debugging: reproduce, isolate, fix, and prove the fix.",
        "tags": ["engineering"],
        "content": (
            "## Goal\n"
            "Resolve a defect with evidence rather than guesswork.\n\n"
            "## Steps\n"
            "1. Reproduce the failure first; capture the exact error and the minimal trigger.\n"
            "2. Isolate by halving the search space (logs, bisection, targeted assertions).\n"
            "3. Fix the root cause, not the symptom; note any nearby latent issues separately.\n"
            "4. Prove the fix with the failing case turned into a focused test where practical.\n\n"
            "## Output\n"
            "A fix accompanied by the reproduction story and verification evidence."
        ),
    },
]

# Preloaded MCP servers and APIs. `metadata_json.protocol` distinguishes MCP
# servers from plain HTTP APIs; credentials are never stored here — installing
# an entry creates a draft integration whose secret is configured afterwards.
INTEGRATION_CATALOG: list[dict[str, Any]] = [
    {
        "catalog_id": "mcp-github",
        "name": "GitHub MCP",
        "type": "custom",
        "auth_type": "bearer",
        "base_url": "https://api.githubcopilot.com/mcp/",
        "publisher": "third_party",
        "capabilities": ["repos", "issues", "pull_requests", "code_search"],
        "egress_allowlist": ["api.githubcopilot.com", "api.github.com"],
        "metadata_json": {
            "protocol": "mcp",
            "transport": "http",
            "docs": "https://github.com/github/github-mcp-server",
        },
    },
    {
        "catalog_id": "mcp-linear",
        "name": "Linear MCP",
        "type": "custom",
        "auth_type": "oauth2",
        "base_url": "https://mcp.linear.app/mcp",
        "publisher": "third_party",
        "capabilities": ["issues", "projects", "comments"],
        "egress_allowlist": ["mcp.linear.app", "api.linear.app"],
        "metadata_json": {
            "protocol": "mcp",
            "transport": "http",
            "docs": "https://linear.app/docs/mcp",
        },
    },
    {
        "catalog_id": "api-linear-graphql",
        "name": "Linear GraphQL API",
        "type": "http",
        "auth_type": "api_key",
        "base_url": "https://api.linear.app/graphql",
        "publisher": "third_party",
        "capabilities": ["graphql", "issues", "comments", "attachments"],
        "egress_allowlist": ["api.linear.app"],
        "metadata_json": {
            "protocol": "http",
            "docs": "https://developers.linear.app/docs/graphql/working-with-the-graphql-api",
        },
    },
    {
        "catalog_id": "mcp-slack",
        "name": "Slack MCP",
        "type": "custom",
        "auth_type": "oauth2",
        "base_url": "",
        "publisher": "third_party",
        "capabilities": ["messages", "channels", "search"],
        "egress_allowlist": ["slack.com", "api.slack.com"],
        "metadata_json": {
            "protocol": "mcp",
            "transport": "stdio",
            "package": "@modelcontextprotocol/server-slack",
        },
    },
    {
        "catalog_id": "mcp-notion",
        "name": "Notion MCP",
        "type": "custom",
        "auth_type": "bearer",
        "base_url": "https://mcp.notion.com/mcp",
        "publisher": "third_party",
        "capabilities": ["pages", "databases", "search"],
        "egress_allowlist": ["mcp.notion.com", "api.notion.com"],
        "metadata_json": {
            "protocol": "mcp",
            "transport": "http",
            "docs": "https://developers.notion.com/docs/mcp",
        },
    },
    {
        "catalog_id": "mcp-atlassian",
        "name": "Atlassian MCP (Jira/Confluence)",
        "type": "custom",
        "auth_type": "oauth2",
        "base_url": "https://mcp.atlassian.com/v1/sse",
        "publisher": "third_party",
        "capabilities": ["jira_issues", "confluence_pages", "search"],
        "egress_allowlist": ["mcp.atlassian.com", "api.atlassian.com"],
        "metadata_json": {"protocol": "mcp", "transport": "sse"},
    },
    {
        "catalog_id": "mcp-filesystem",
        "name": "Filesystem MCP (local)",
        "type": "custom",
        "auth_type": "none",
        "base_url": "",
        "publisher": "first_party",
        "capabilities": ["read_files", "write_files", "directory_listing"],
        "egress_allowlist": [],
        "metadata_json": {
            "protocol": "mcp",
            "transport": "stdio",
            "package": "@modelcontextprotocol/server-filesystem",
            "execution_mode_hint": "sandboxed",
        },
    },
    {
        "catalog_id": "mcp-fetch",
        "name": "Fetch MCP (web retrieval)",
        "type": "custom",
        "auth_type": "none",
        "base_url": "",
        "publisher": "first_party",
        "capabilities": ["http_get", "html_to_markdown"],
        "egress_allowlist": [],
        "metadata_json": {
            "protocol": "mcp",
            "transport": "stdio",
            "package": "mcp-server-fetch",
            "note": "Constrain egress via the platform allowlist before enabling.",
        },
    },
    {
        "catalog_id": "mcp-postgres",
        "name": "PostgreSQL MCP",
        "type": "database",
        "auth_type": "basic",
        "base_url": "",
        "publisher": "first_party",
        "capabilities": ["read_only_queries", "schema_inspection"],
        "egress_allowlist": [],
        "metadata_json": {
            "protocol": "mcp",
            "transport": "stdio",
            "package": "@modelcontextprotocol/server-postgres",
        },
    },
    {
        "catalog_id": "api-github-rest",
        "name": "GitHub REST API",
        "type": "http",
        "auth_type": "bearer",
        "base_url": "https://api.github.com",
        "publisher": "third_party",
        "capabilities": ["repos", "issues", "actions", "releases"],
        "egress_allowlist": ["api.github.com"],
        "metadata_json": {"protocol": "http", "docs": "https://docs.github.com/rest"},
    },
    {
        "catalog_id": "api-nvidia-nim",
        "name": "NVIDIA NIM API",
        "type": "http",
        "auth_type": "bearer",
        "base_url": "https://integrate.api.nvidia.com/v1",
        "publisher": "third_party",
        "capabilities": ["chat_completions", "embeddings"],
        "egress_allowlist": ["integrate.api.nvidia.com"],
        "metadata_json": {
            "protocol": "http",
            "note": "Also configurable platform-wide via NVIDIA_API_KEY for nim/<model> routing.",
        },
    },
    {
        "catalog_id": "api-openai",
        "name": "OpenAI API",
        "type": "http",
        "auth_type": "bearer",
        "base_url": "https://api.openai.com/v1",
        "publisher": "third_party",
        "capabilities": ["chat_completions", "embeddings"],
        "egress_allowlist": ["api.openai.com"],
        "metadata_json": {
            "protocol": "http",
            "note": "Platform default provider; configured via OPENAI_API_KEY.",
        },
    },
]


def catalog_entry(catalog_id: str) -> dict[str, Any] | None:
    normalized = str(catalog_id or "").strip()
    for entry in INTEGRATION_CATALOG:
        if entry["catalog_id"] == normalized:
            return dict(entry)
    return None


# --- Agent Skills bundles (LOCUS-340) ----------------------------------------


def seed_skill_files(seed: Mapping[str, Any]) -> dict[str, bytes]:
    """A bundled seed as an Agent Skills folder (``SKILL.md`` only)."""
    text = skill_format.render_skill_md(
        name=str(seed["name"]),
        description=str(seed.get("description") or ""),
        body=str(seed.get("content") or ""),
        metadata={"tags": " ".join(str(tag) for tag in seed.get("tags", []))},
    )
    return {skill_format.SKILL_FILE: text.encode("utf-8")}


@lru_cache(maxsize=1)
def bundled_skill_bundles() -> tuple[tuple[skill_format.SkillDocument, dict[str, bytes]], ...]:
    """Every seed parsed through the Agent Skills parser (raises if a seed is invalid)."""
    out: list[tuple[skill_format.SkillDocument, dict[str, bytes]]] = []
    for seed in SKILL_SEEDS:
        files = seed_skill_files(seed)
        out.append((skill_format.load_skill_files(files), files))
    return tuple(out)


def bundled_skill_document(seed_id: str) -> skill_format.SkillDocument | None:
    for seed, (document, _files) in zip(SKILL_SEEDS, bundled_skill_bundles(), strict=True):
        if seed["id"] == seed_id:
            return document
    return None


def skill_store() -> skill_format.SkillStore:
    """The skill store under the Locus app home (``LOCUS_SKILLS_DIR`` overrides)."""
    return skill_format.SkillStore(skill_format.default_skills_dir())


def skill_library() -> skill_format.SkillLibrary:
    """Bundled + stored skills, for an agent run's ``SkillTools``."""
    return skill_format.SkillLibrary(store=skill_store(), bundled=bundled_skill_bundles())


def _first_line_description(body: str, fallback: str) -> str:
    for line in body.splitlines():
        text = line.strip().lstrip("#").strip()
        if text:
            return text[:300]
    return fallback


def files_from_markdown(text: str, *, name: str = "", description: str = "") -> dict[str, bytes]:
    """A single-file skill folder from pasted/fetched markdown.

    Text that already carries frontmatter is used as-is (it is validated later);
    plain markdown is wrapped with a generated name and description.
    """
    stripped = text.lstrip("﻿")
    if stripped.startswith("---"):
        return {skill_format.SKILL_FILE: stripped.encode("utf-8")}
    slug = skill_format.slugify_skill_name(name)
    rendered = skill_format.render_skill_md(
        name=slug,
        description=(description or "").strip()[: skill_format.DESCRIPTION_MAX]
        or _first_line_description(stripped, f"Imported skill {slug}"),
        body=stripped,
    )
    return {skill_format.SKILL_FILE: rendered.encode("utf-8")}


def decode_archive(encoded: str) -> bytes:
    """Base64 zip archive from an import request (size-capped before decoding)."""
    text = re.sub(r"\s+", "", str(encoded or ""))
    if len(text) > (skill_format.MAX_ARCHIVE_BYTES * 4) // 3 + 8:
        raise skill_format.SkillError("oversized", "archive is too large")
    try:
        return base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise skill_format.SkillError("invalid_archive", "archive_base64 is not base64") from exc


@dataclass(frozen=True)
class SkillUrlPlan:
    """How to fetch a skill URL: a markdown file or a zip archive (+ folder)."""

    kind: str  # "markdown" | "archive"
    fetch_url: str
    subdir: str = ""


_GITHUB_TREE = re.compile(r"^/([^/]+)/([^/]+)/tree/([^/]+)/?(.*)$")
_GITHUB_BLOB = re.compile(r"^/([^/]+)/([^/]+)/blob/([^/]+)/(.+)$")


def plan_skill_url(url: str, *, subdir: str = "") -> SkillUrlPlan:
    """Map a skill URL to a fetch plan. Git repositories are read as archives:

    * ``https://github.com/<o>/<r>/tree/<ref>/<folder>`` -> the codeload zip of
      ``<ref>`` with ``<folder>`` selected (``<ref>`` is one path segment);
    * ``https://github.com/<o>/<r>/blob/<ref>/<path>/SKILL.md`` -> the raw file;
    * any ``*.zip`` URL -> that archive (``subdir`` selects the folder);
    * anything else -> a markdown SKILL.md.

    Clone URLs (``git+``, ``ssh``, ``*.git``) are refused: cloning would run git
    with network access, which no sandbox tier permits.
    """
    raw = str(url or "").strip()
    lowered = raw.lower()
    if lowered.startswith(("git+", "ssh://", "git://", "git@")) or lowered.rstrip("/").endswith(
        ".git"
    ):
        raise skill_format.SkillError(
            "unsupported_url",
            "git clone URLs are not supported; use a GitHub tree URL or a .zip archive URL",
        )
    parts = urlsplit(raw)
    host = (parts.hostname or "").lower()
    path = parts.path or ""
    if host == "github.com":
        tree = _GITHUB_TREE.match(path)
        if tree:
            owner, repo, ref, folder = tree.groups()
            selected = "/".join(p for p in (folder.strip("/"), subdir.strip("/")) if p)
            return SkillUrlPlan(
                "archive", f"https://codeload.github.com/{owner}/{repo}/zip/{ref}", selected
            )
        blob = _GITHUB_BLOB.match(path)
        if blob:
            owner, repo, ref, file_path = blob.groups()
            return SkillUrlPlan(
                "markdown",
                f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{file_path}",
            )
    if path.lower().endswith(".zip"):
        return SkillUrlPlan("archive", raw, subdir.strip("/"))
    return SkillUrlPlan("markdown", raw)
