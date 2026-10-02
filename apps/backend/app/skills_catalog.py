"""Bundled skills and the preloaded integration catalog.

Skills follow the Symphony SKILL.md model: a named, versioned markdown
procedure that is injected into agent context when enabled. The bundled set
below is adapted from Symphony's `.codex/skills/` catalog, generalized away
from Symphony-specific tooling.

The integration catalog preloads well-known MCP servers and APIs so builders
start from a vetted list; custom integrations remain fully supported through
the existing integrations CRUD.
"""

from __future__ import annotations

from typing import Any

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
