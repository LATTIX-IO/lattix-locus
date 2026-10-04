from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache

from fastapi import FastAPI
from fastapi.routing import APIRoute


class RouteAccessCategory(str, Enum):
    PUBLIC_MINIMAL = "public-minimal"
    AUTHENTICATED_READ = "authenticated-read"
    AUTHENTICATED_MUTATE = "authenticated-mutate"
    INTERNAL_ONLY = "internal-only"


@dataclass(frozen=True)
class RouteAccessRule:
    methods: tuple[str, ...]
    path_template: str
    category: RouteAccessCategory
    action: str = ""


_ROUTE_ACCESS_RULES: tuple[RouteAccessRule, ...] = (
    RouteAccessRule(("GET",), "/health", RouteAccessCategory.PUBLIC_MINIMAL),
    RouteAccessRule(("GET",), "/healthz", RouteAccessCategory.PUBLIC_MINIMAL),
    RouteAccessRule(
        ("GET",), "/auth/oidc/start", RouteAccessCategory.PUBLIC_MINIMAL, "auth.oidc.start"
    ),
    RouteAccessRule(
        ("GET",), "/auth/oidc/callback", RouteAccessCategory.PUBLIC_MINIMAL, "auth.oidc.callback"
    ),
    RouteAccessRule(("POST",), "/auth/login", RouteAccessCategory.PUBLIC_MINIMAL, "auth.login"),
    RouteAccessRule(
        ("POST",), "/auth/register", RouteAccessCategory.PUBLIC_MINIMAL, "auth.register"
    ),
    RouteAccessRule(("POST",), "/auth/logout", RouteAccessCategory.PUBLIC_MINIMAL, "auth.logout"),
    RouteAccessRule(
        ("GET",),
        "/system/active-agents",
        RouteAccessCategory.AUTHENTICATED_READ,
        "system.active_agents.read",
    ),
    RouteAccessRule(
        ("POST",),
        "/system/shutdown",
        RouteAccessCategory.AUTHENTICATED_MUTATE,
        "system.shutdown",
    ),
    # Desktop update channels (LOCUS-349).
    RouteAccessRule(
        ("GET",),
        "/system/update/status",
        RouteAccessCategory.AUTHENTICATED_READ,
        "system.update.status.read",
    ),
    RouteAccessRule(
        ("POST",),
        "/system/update/prepare",
        RouteAccessCategory.AUTHENTICATED_MUTATE,
        "system.update.prepare",
    ),
    RouteAccessRule(
        ("POST",),
        "/system/update/cancel",
        RouteAccessCategory.AUTHENTICATED_MUTATE,
        "system.update.cancel",
    ),
    # Self-improvement loop controls (Settings, LOCUS-353).
    RouteAccessRule(
        ("GET",), "/loop/status", RouteAccessCategory.AUTHENTICATED_READ, "loop.status.read"
    ),
    RouteAccessRule(
        ("POST",), "/loop/enable", RouteAccessCategory.AUTHENTICATED_MUTATE, "loop.enable"
    ),
    RouteAccessRule(
        ("POST",), "/loop/disable", RouteAccessCategory.AUTHENTICATED_MUTATE, "loop.disable"
    ),
    RouteAccessRule(
        ("POST", "DELETE"),
        "/loop/autostart",
        RouteAccessCategory.AUTHENTICATED_MUTATE,
        "loop.autostart",
    ),
    # Export / import (agents, workflows, playbooks, bundle) — JSON/YAML.
    RouteAccessRule(
        ("GET",),
        "/agent-definitions/{item_id}/export",
        RouteAccessCategory.AUTHENTICATED_READ,
        "agent.definition.export",
    ),
    RouteAccessRule(
        ("POST",),
        "/agent-definitions/import",
        RouteAccessCategory.AUTHENTICATED_MUTATE,
        "agent.definition.import",
    ),
    RouteAccessRule(
        ("GET",),
        "/workflow-definitions/{item_id}/export",
        RouteAccessCategory.AUTHENTICATED_READ,
        "workflow.definition.export",
    ),
    RouteAccessRule(
        ("POST",),
        "/workflow-definitions/import",
        RouteAccessCategory.AUTHENTICATED_MUTATE,
        "workflow.definition.import",
    ),
    RouteAccessRule(
        ("GET",),
        "/playbooks/{playbook_id}/export",
        RouteAccessCategory.AUTHENTICATED_READ,
        "playbook.export",
    ),
    RouteAccessRule(
        ("POST",),
        "/playbooks/import",
        RouteAccessCategory.AUTHENTICATED_MUTATE,
        "playbook.import",
    ),
    RouteAccessRule(
        ("GET",),
        "/bundle/export",
        RouteAccessCategory.AUTHENTICATED_READ,
        "bundle.export",
    ),
    RouteAccessRule(
        ("POST",),
        "/bundle/import",
        RouteAccessCategory.AUTHENTICATED_MUTATE,
        "bundle.import",
    ),
    RouteAccessRule(
        ("GET",), "/auth/session", RouteAccessCategory.PUBLIC_MINIMAL, "auth.session.read"
    ),
    RouteAccessRule(("GET",), "/platform/version", RouteAccessCategory.PUBLIC_MINIMAL),
    RouteAccessRule(
        ("GET",), "/healthz/details", RouteAccessCategory.AUTHENTICATED_READ, "health.details.read"
    ),
    RouteAccessRule(
        ("GET",),
        "/federation/status",
        RouteAccessCategory.AUTHENTICATED_READ,
        "federation.status.read",
    ),
    RouteAccessRule(
        ("GET",),
        "/runtime/providers",
        RouteAccessCategory.AUTHENTICATED_READ,
        "runtime.providers.read",
    ),
    RouteAccessRule(
        ("GET",),
        "/runtime/user-providers",
        RouteAccessCategory.AUTHENTICATED_READ,
        "runtime.providers.read",
    ),
    RouteAccessRule(
        ("GET",),
        "/skills/user",
        RouteAccessCategory.AUTHENTICATED_READ,
        "skills.user.read",
    ),
    RouteAccessRule(
        ("PUT",),
        "/skills/user",
        RouteAccessCategory.AUTHENTICATED_MUTATE,
        "skills.user.write",
    ),
    RouteAccessRule(
        ("PUT",),
        "/runtime/user-providers/{provider}",
        RouteAccessCategory.AUTHENTICATED_MUTATE,
        "runtime.providers.write",
    ),
    RouteAccessRule(
        ("DELETE",),
        "/runtime/user-providers/{provider}",
        RouteAccessCategory.AUTHENTICATED_MUTATE,
        "runtime.providers.write",
    ),
    RouteAccessRule(
        ("GET",),
        "/runtime/l3-parity-report",
        RouteAccessCategory.AUTHENTICATED_READ,
        "runtime.l3_parity.read",
    ),
    RouteAccessRule(
        ("GET",),
        "/runtime/local-integration-readiness",
        RouteAccessCategory.AUTHENTICATED_READ,
        "runtime.local_integration_readiness.read",
    ),
    RouteAccessRule(
        ("GET",),
        "/platform/settings",
        RouteAccessCategory.AUTHENTICATED_READ,
        "platform.settings.read",
    ),
    RouteAccessRule(
        ("GET",),
        "/platform/security-policy",
        RouteAccessCategory.AUTHENTICATED_READ,
        "platform.security_policy.read",
    ),
    RouteAccessRule(
        ("POST",),
        "/platform/settings",
        RouteAccessCategory.AUTHENTICATED_MUTATE,
        "platform.settings.save",
    ),
    # AI observability (LOCUS-375): reads of the local trace store only. The
    # exporter settings are platform settings (POST /platform/settings).
    RouteAccessRule(
        ("GET",),
        "/telemetry/runs",
        RouteAccessCategory.AUTHENTICATED_READ,
        "telemetry.runs.read",
    ),
    RouteAccessRule(
        ("GET",),
        "/telemetry/runs/{run_id}/trace",
        RouteAccessCategory.AUTHENTICATED_READ,
        "telemetry.trace.read",
    ),
    RouteAccessRule(
        ("GET",),
        "/telemetry/summary",
        RouteAccessCategory.AUTHENTICATED_READ,
        "telemetry.summary.read",
    ),
    # Computer use (LOCUS-341): the handlers always require authentication.
    RouteAccessRule(
        ("POST",),
        "/computer-use/panic",
        RouteAccessCategory.AUTHENTICATED_MUTATE,
        "computer_use.panic",
    ),
    RouteAccessRule(
        ("POST",),
        "/computer-use/reset",
        RouteAccessCategory.AUTHENTICATED_MUTATE,
        "computer_use.reset",
    ),
    RouteAccessRule(
        ("GET",),
        "/computer-use/status",
        RouteAccessCategory.AUTHENTICATED_READ,
        "computer_use.status.read",
    ),
    # User browser (LOCUS-350). Pairing and the tier are principal-only in the
    # handlers. The relay routes serve only the native-messaging host: the
    # handlers require loopback, no browser headers, and the pairing key.
    RouteAccessRule(
        ("GET",),
        "/user-browser/status",
        RouteAccessCategory.AUTHENTICATED_READ,
        "user_browser.status.read",
    ),
    RouteAccessRule(
        ("POST", "DELETE"),
        "/user-browser/pairing",
        RouteAccessCategory.AUTHENTICATED_MUTATE,
        "user_browser.pairing",
    ),
    RouteAccessRule(
        ("GET",),
        "/user-browser/tier",
        RouteAccessCategory.AUTHENTICATED_READ,
        "user_browser.tier.read",
    ),
    RouteAccessRule(
        ("PUT",),
        "/user-browser/tier",
        RouteAccessCategory.AUTHENTICATED_MUTATE,
        "user_browser.tier.set",
    ),
    *(
        RouteAccessRule(
            ("POST",),
            f"/user-browser/relay/{name}",
            RouteAccessCategory.PUBLIC_MINIMAL,
            f"user_browser.relay.{name}",
        )
        for name in ("hello", "next", "result", "event", "bye")
    ),
    # Composer capabilities (per-user settings, working folders, MCP, escalations).
    RouteAccessRule(
        ("GET",), "/user/settings", RouteAccessCategory.AUTHENTICATED_READ, "user.settings.read"
    ),
    RouteAccessRule(
        ("PUT",), "/user/settings", RouteAccessCategory.AUTHENTICATED_MUTATE, "user.settings.save"
    ),
    RouteAccessRule(
        ("GET",),
        "/workspace/folders",
        RouteAccessCategory.AUTHENTICATED_READ,
        "workspace.folders.list",
    ),
    RouteAccessRule(
        ("GET",), "/mcp/servers", RouteAccessCategory.AUTHENTICATED_READ, "mcp.servers.list"
    ),
    RouteAccessRule(
        ("GET",),
        "/workflow-runs/{run_id}/escalations",
        RouteAccessCategory.AUTHENTICATED_READ,
        "workflow.run.escalations.read",
    ),
    RouteAccessRule(
        ("POST",),
        "/workflow-runs/{run_id}/escalations/{escalation_id}/approve",
        RouteAccessCategory.AUTHENTICATED_MUTATE,
        "workflow.run.escalations.approve",
    ),
    # Biscuit capability grants (LOCUS-334): list own grants, revoke one.
    RouteAccessRule(
        ("GET",), "/gateway/grants", RouteAccessCategory.AUTHENTICATED_READ, "gateway.grants.read"
    ),
    RouteAccessRule(
        ("POST",),
        "/gateway/grants/{grant_id}/revoke",
        RouteAccessCategory.AUTHENTICATED_MUTATE,
        "gateway.grants.revoke",
    ),
    RouteAccessRule(
        ("GET",), "/memory/{session_id}", RouteAccessCategory.AUTHENTICATED_READ, "memory.read"
    ),
    RouteAccessRule(
        ("DELETE",),
        "/memory/{session_id}",
        RouteAccessCategory.AUTHENTICATED_MUTATE,
        "memory.clear",
    ),
    RouteAccessRule(
        ("POST",),
        "/internal/memory/consolidation/run",
        RouteAccessCategory.INTERNAL_ONLY,
        "memory.consolidation.run",
    ),
    RouteAccessRule(
        ("POST",),
        "/internal/memory/world-graph/project",
        RouteAccessCategory.INTERNAL_ONLY,
        "memory.world_graph.project",
    ),
    RouteAccessRule(
        ("POST",),
        "/internal/cognition/assemblies/run",
        RouteAccessCategory.INTERNAL_ONLY,
        "cognition.assembly.run",
    ),
    RouteAccessRule(
        ("POST",),
        "/internal/cognition/messages/admit",
        RouteAccessCategory.INTERNAL_ONLY,
        "cognition.message.admit",
    ),
    RouteAccessRule(
        ("GET",),
        "/workflows/published",
        RouteAccessCategory.AUTHENTICATED_READ,
        "workflow.definition.published.read",
    ),
    RouteAccessRule(
        ("GET",),
        "/workflows/active",
        RouteAccessCategory.AUTHENTICATED_READ,
        "workflow.definition.active.read",
    ),
    RouteAccessRule(
        ("POST",), "/workflow-runs", RouteAccessCategory.AUTHENTICATED_MUTATE, "workflow.run.create"
    ),
    RouteAccessRule(
        ("GET",), "/workflow-runs", RouteAccessCategory.AUTHENTICATED_READ, "workflow.run.list"
    ),
    RouteAccessRule(
        ("POST",),
        "/workflow-runs/{run_id}/messages",
        RouteAccessCategory.AUTHENTICATED_MUTATE,
        "workflow.run.message",
    ),
    RouteAccessRule(
        ("POST",),
        "/workflow-runs/{run_id}/rename",
        RouteAccessCategory.AUTHENTICATED_MUTATE,
        "workflow.run.rename",
    ),
    RouteAccessRule(
        ("GET",),
        "/workflow-runs/{run_id}",
        RouteAccessCategory.AUTHENTICATED_READ,
        "workflow.run.read",
    ),
    RouteAccessRule(
        ("GET",),
        "/workflow-runs/{run_id}/events",
        RouteAccessCategory.AUTHENTICATED_READ,
        "workflow.run.events.read",
    ),
    RouteAccessRule(
        ("GET",),
        "/workflow-runs/{run_id}/events/stream",
        RouteAccessCategory.AUTHENTICATED_READ,
        "workflow.run.events.stream",
    ),
    RouteAccessRule(
        ("GET",),
        "/models/overview",
        RouteAccessCategory.AUTHENTICATED_READ,
        "models.overview.read",
    ),
    RouteAccessRule(
        ("GET",),
        "/models/providers/{provider_id}/models",
        RouteAccessCategory.AUTHENTICATED_READ,
        "models.provider.models.read",
    ),
    RouteAccessRule(
        ("GET",),
        "/workflow-definitions/{item_id}/triggers",
        RouteAccessCategory.AUTHENTICATED_READ,
        "workflow.trigger.list",
    ),
    RouteAccessRule(
        ("POST",),
        "/workflow-definitions/{item_id}/triggers",
        RouteAccessCategory.AUTHENTICATED_MUTATE,
        "workflow.trigger.create",
    ),
    RouteAccessRule(
        ("DELETE",),
        "/triggers/{token}",
        RouteAccessCategory.AUTHENTICATED_MUTATE,
        "workflow.trigger.revoke",
    ),
    RouteAccessRule(
        ("POST",),
        "/triggers/webhook/{token}",
        RouteAccessCategory.PUBLIC_MINIMAL,
        "workflow.trigger.fire",
    ),
    RouteAccessRule(
        ("GET",),
        "/workflow-definitions/{item_id}/schedules",
        RouteAccessCategory.AUTHENTICATED_READ,
        "workflow.schedule.list",
    ),
    RouteAccessRule(
        ("POST",),
        "/workflow-definitions/{item_id}/schedules",
        RouteAccessCategory.AUTHENTICATED_MUTATE,
        "workflow.schedule.create",
    ),
    RouteAccessRule(
        ("POST",),
        "/schedules/{schedule_id}/toggle",
        RouteAccessCategory.AUTHENTICATED_MUTATE,
        "workflow.schedule.toggle",
    ),
    RouteAccessRule(
        ("DELETE",),
        "/schedules/{schedule_id}",
        RouteAccessCategory.AUTHENTICATED_MUTATE,
        "workflow.schedule.delete",
    ),
    RouteAccessRule(
        ("GET",),
        "/knowledge/collections",
        RouteAccessCategory.AUTHENTICATED_READ,
        "knowledge.collection.list",
    ),
    RouteAccessRule(
        ("POST",),
        "/knowledge/collections",
        RouteAccessCategory.AUTHENTICATED_MUTATE,
        "knowledge.collection.create",
    ),
    RouteAccessRule(
        ("DELETE",),
        "/knowledge/collections/{collection_id}",
        RouteAccessCategory.AUTHENTICATED_MUTATE,
        "knowledge.collection.delete",
    ),
    RouteAccessRule(
        ("POST",),
        "/knowledge/collections/{collection_id}/documents",
        RouteAccessCategory.AUTHENTICATED_MUTATE,
        "knowledge.document.add",
    ),
    RouteAccessRule(
        ("POST",),
        "/knowledge/collections/{collection_id}/search",
        RouteAccessCategory.AUTHENTICATED_MUTATE,
        "knowledge.search",
    ),
    RouteAccessRule(
        ("GET",),
        "/knowledge/memory-layers",
        RouteAccessCategory.AUTHENTICATED_READ,
        "knowledge.memory.layers",
    ),
    RouteAccessRule(
        ("GET",),
        "/knowledge/vector-stores",
        RouteAccessCategory.AUTHENTICATED_READ,
        "knowledge.vector.list",
    ),
    RouteAccessRule(
        ("GET",),
        "/skills",
        RouteAccessCategory.AUTHENTICATED_READ,
        "skill.list",
    ),
    RouteAccessRule(
        ("POST",),
        "/skills",
        RouteAccessCategory.AUTHENTICATED_MUTATE,
        "skill.save",
    ),
    RouteAccessRule(
        ("POST",),
        "/skills/import",
        RouteAccessCategory.AUTHENTICATED_MUTATE,
        "skill.import",
    ),
    RouteAccessRule(
        ("POST",),
        "/skills/{skill_id}/scan",
        RouteAccessCategory.AUTHENTICATED_MUTATE,
        "skill.scan",
    ),
    RouteAccessRule(
        ("DELETE",),
        "/skills/{skill_id}",
        RouteAccessCategory.AUTHENTICATED_MUTATE,
        "skill.delete",
    ),
    RouteAccessRule(
        ("POST",),
        "/skills/{skill_id}/test",
        RouteAccessCategory.AUTHENTICATED_MUTATE,
        "skill.test",
    ),
    RouteAccessRule(
        ("POST",),
        "/skills/{skill_id}/eval",
        RouteAccessCategory.AUTHENTICATED_MUTATE,
        "skill.eval",
    ),
    RouteAccessRule(
        ("POST",),
        "/skills/{skill_id}/promote",
        RouteAccessCategory.AUTHENTICATED_MUTATE,
        "skill.promote",
    ),
    RouteAccessRule(
        ("POST",),
        "/skills/{skill_id}/revoke",
        RouteAccessCategory.AUTHENTICATED_MUTATE,
        "skill.revoke",
    ),
    RouteAccessRule(
        ("GET",),
        "/integrations/catalog",
        RouteAccessCategory.AUTHENTICATED_READ,
        "integration.catalog.read",
    ),
    RouteAccessRule(
        ("POST",),
        "/integrations/catalog/{catalog_id}/install",
        RouteAccessCategory.AUTHENTICATED_MUTATE,
        "integration.catalog.install",
    ),
    RouteAccessRule(
        ("PUT", "DELETE"),
        "/models/providers/{provider_id}/key",
        RouteAccessCategory.AUTHENTICATED_MUTATE,
        "models.provider.key.manage",
    ),
    RouteAccessRule(
        ("POST",),
        "/models/local/pull",
        RouteAccessCategory.AUTHENTICATED_MUTATE,
        "models.local.pull",
    ),
    RouteAccessRule(
        ("DELETE",),
        "/models/local/{model_id}",
        RouteAccessCategory.AUTHENTICATED_MUTATE,
        "models.local.delete",
    ),
    RouteAccessRule(
        ("GET",),
        "/workflow-runs/{run_id}/stream",
        RouteAccessCategory.AUTHENTICATED_READ,
        "workflow.run.events.read",
    ),
    RouteAccessRule(
        ("POST",),
        "/workflow-runs/{run_id}/archive",
        RouteAccessCategory.AUTHENTICATED_MUTATE,
        "workflow.run.archive",
    ),
    RouteAccessRule(
        ("PATCH",),
        "/workflow-runs/{run_id}",
        RouteAccessCategory.AUTHENTICATED_MUTATE,
        "workflow.run.update",
    ),
    RouteAccessRule(
        ("POST",),
        "/artifacts/{artifact_id}/versions",
        RouteAccessCategory.AUTHENTICATED_MUTATE,
        "artifact.version.create",
    ),
    RouteAccessRule(
        ("POST",), "/approvals", RouteAccessCategory.AUTHENTICATED_MUTATE, "approval.submit"
    ),
    RouteAccessRule(("GET",), "/inbox", RouteAccessCategory.AUTHENTICATED_READ, "inbox.read"),
    RouteAccessRule(
        ("GET",), "/inbox/groups", RouteAccessCategory.AUTHENTICATED_READ, "inbox.groups.list"
    ),
    RouteAccessRule(
        ("POST",), "/inbox/groups", RouteAccessCategory.AUTHENTICATED_MUTATE, "inbox.groups.create"
    ),
    RouteAccessRule(
        ("POST",),
        "/inbox/groups/{group_id}",
        RouteAccessCategory.AUTHENTICATED_MUTATE,
        "inbox.groups.update",
    ),
    RouteAccessRule(
        ("DELETE",),
        "/inbox/groups/{group_id}",
        RouteAccessCategory.AUTHENTICATED_MUTATE,
        "inbox.groups.delete",
    ),
    RouteAccessRule(
        ("GET",), "/integrations", RouteAccessCategory.AUTHENTICATED_READ, "integration.list"
    ),
    RouteAccessRule(
        ("GET",),
        "/integrations/starters",
        RouteAccessCategory.AUTHENTICATED_READ,
        "integration.starter_catalog.read",
    ),
    RouteAccessRule(
        ("GET",),
        "/integrations/mcp",
        RouteAccessCategory.AUTHENTICATED_READ,
        "integration.mcp.list",
    ),
    RouteAccessRule(
        ("GET",),
        "/integrations/mcp/starters",
        RouteAccessCategory.AUTHENTICATED_READ,
        "integration.mcp.starter_catalog.read",
    ),
    RouteAccessRule(
        ("POST",), "/integrations", RouteAccessCategory.AUTHENTICATED_MUTATE, "integration.save"
    ),
    RouteAccessRule(
        ("POST",),
        "/integrations/mcp",
        RouteAccessCategory.AUTHENTICATED_MUTATE,
        "integration.mcp.save",
    ),
    RouteAccessRule(
        ("POST",),
        "/integrations/mcp/{connection_id}/validate",
        RouteAccessCategory.AUTHENTICATED_MUTATE,
        "integration.mcp.validate",
    ),
    RouteAccessRule(
        ("POST",),
        "/integrations/mcp/{connection_id}/approve",
        RouteAccessCategory.AUTHENTICATED_MUTATE,
        "integration.mcp.approve",
    ),
    RouteAccessRule(
        ("GET",),
        "/integrations/{integration_id}/oauth/status",
        RouteAccessCategory.AUTHENTICATED_READ,
        "integration.oauth.status.read",
    ),
    RouteAccessRule(
        ("POST",),
        "/integrations/{integration_id}/oauth/connect",
        RouteAccessCategory.AUTHENTICATED_MUTATE,
        "integration.oauth.connect",
    ),
    RouteAccessRule(
        ("GET",),
        "/integrations/{integration_id}/oauth/callback",
        RouteAccessCategory.PUBLIC_MINIMAL,
        "integration.oauth.callback",
    ),
    RouteAccessRule(
        ("POST",),
        "/integrations/{integration_id}/oauth/refresh",
        RouteAccessCategory.AUTHENTICATED_MUTATE,
        "integration.oauth.refresh",
    ),
    RouteAccessRule(
        ("POST",),
        "/integrations/{integration_id}/oauth/disconnect",
        RouteAccessCategory.AUTHENTICATED_MUTATE,
        "integration.oauth.disconnect",
    ),
    RouteAccessRule(
        ("POST",),
        "/integrations/{integration_id}/test",
        RouteAccessCategory.AUTHENTICATED_MUTATE,
        "integration.test",
    ),
    RouteAccessRule(
        ("GET",),
        "/integrations/{integration_id}/policy",
        RouteAccessCategory.AUTHENTICATED_READ,
        "integration.policy.read",
    ),
    RouteAccessRule(
        ("DELETE",),
        "/integrations/{integration_id}",
        RouteAccessCategory.AUTHENTICATED_MUTATE,
        "integration.delete",
    ),
    RouteAccessRule(
        ("GET",), "/templates/agents", RouteAccessCategory.AUTHENTICATED_READ, "template.agent.list"
    ),
    RouteAccessRule(
        ("GET",),
        "/templates/catalog",
        RouteAccessCategory.AUTHENTICATED_READ,
        "template.catalog.read",
    ),
    RouteAccessRule(
        ("POST",),
        "/templates/agents/{template_id}/instantiate",
        RouteAccessCategory.AUTHENTICATED_MUTATE,
        "template.agent.instantiate",
    ),
    RouteAccessRule(
        ("POST",),
        "/templates/workflows/{workflow_id}/instantiate",
        RouteAccessCategory.AUTHENTICATED_MUTATE,
        "template.workflow.instantiate",
    ),
    RouteAccessRule(
        ("GET",), "/playbooks", RouteAccessCategory.AUTHENTICATED_READ, "playbook.list"
    ),
    RouteAccessRule(
        ("POST",), "/playbooks", RouteAccessCategory.AUTHENTICATED_MUTATE, "playbook.save"
    ),
    RouteAccessRule(
        ("GET",),
        "/playbooks/{playbook_id}",
        RouteAccessCategory.AUTHENTICATED_READ,
        "playbook.read",
    ),
    RouteAccessRule(
        ("POST",),
        "/playbooks/{playbook_id}/publish",
        RouteAccessCategory.AUTHENTICATED_MUTATE,
        "playbook.publish",
    ),
    RouteAccessRule(
        ("POST",),
        "/playbooks/{playbook_id}/unpublish",
        RouteAccessCategory.AUTHENTICATED_MUTATE,
        "playbook.unpublish",
    ),
    RouteAccessRule(
        ("POST",),
        "/playbooks/{playbook_id}/archive",
        RouteAccessCategory.AUTHENTICATED_MUTATE,
        "playbook.archive",
    ),
    RouteAccessRule(
        ("POST",),
        "/playbooks/{playbook_id}/instantiate",
        RouteAccessCategory.AUTHENTICATED_MUTATE,
        "playbook.instantiate",
    ),
    RouteAccessRule(
        ("GET",),
        "/observability/runs/{run_id}/trace",
        RouteAccessCategory.AUTHENTICATED_READ,
        "observability.trace.read",
    ),
    RouteAccessRule(
        ("GET",),
        "/observability/dashboard",
        RouteAccessCategory.AUTHENTICATED_READ,
        "observability.dashboard.read",
    ),
    RouteAccessRule(
        ("GET",), "/audit/events", RouteAccessCategory.AUTHENTICATED_READ, "audit.events.read"
    ),
    RouteAccessRule(
        ("GET",),
        "/audit/atf-alignment-report",
        RouteAccessCategory.AUTHENTICATED_READ,
        "audit.atf_alignment.read",
    ),
    RouteAccessRule(
        ("POST",),
        "/collab/sessions/join",
        RouteAccessCategory.AUTHENTICATED_MUTATE,
        "collab.session.join",
    ),
    RouteAccessRule(
        ("GET",),
        "/collab/sessions/{session_id}",
        RouteAccessCategory.AUTHENTICATED_READ,
        "collab.session.read",
    ),
    RouteAccessRule(
        ("POST",),
        "/collab/sessions/{session_id}/sync",
        RouteAccessCategory.AUTHENTICATED_MUTATE,
        "collab.session.sync",
    ),
    RouteAccessRule(
        ("POST",),
        "/collab/sessions/{session_id}/permissions",
        RouteAccessCategory.AUTHENTICATED_MUTATE,
        "collab.session.permissions.update",
    ),
    RouteAccessRule(
        ("GET",), "/artifacts", RouteAccessCategory.AUTHENTICATED_READ, "artifact.list"
    ),
    RouteAccessRule(
        ("GET",),
        "/artifacts/{artifact_id}",
        RouteAccessCategory.AUTHENTICATED_READ,
        "artifact.read",
    ),
    RouteAccessRule(
        ("GET",),
        "/workflow-definitions",
        RouteAccessCategory.AUTHENTICATED_READ,
        "workflow.definition.list",
    ),
    RouteAccessRule(
        ("GET",),
        "/workflow-definitions/{item_id}",
        RouteAccessCategory.AUTHENTICATED_READ,
        "workflow.definition.read",
    ),
    RouteAccessRule(
        ("GET",),
        "/workflow-definitions/{item_id}/versions",
        RouteAccessCategory.AUTHENTICATED_READ,
        "workflow.definition.versions.read",
    ),
    RouteAccessRule(
        ("GET",),
        "/workflow-definitions/{item_id}/versions/{revision_id}",
        RouteAccessCategory.AUTHENTICATED_READ,
        "workflow.definition.version.read",
    ),
    RouteAccessRule(
        ("GET",),
        "/workflow-definitions/{item_id}/security-policy",
        RouteAccessCategory.AUTHENTICATED_READ,
        "workflow.definition.security_policy.read",
    ),
    RouteAccessRule(
        ("GET",),
        "/workflows/{item_id}/security-policy",
        RouteAccessCategory.AUTHENTICATED_READ,
        "workflow.definition.security_policy.read",
    ),
    RouteAccessRule(
        ("POST",),
        "/workflow-definitions",
        RouteAccessCategory.AUTHENTICATED_MUTATE,
        "workflow.definition.save",
    ),
    RouteAccessRule(
        ("POST",),
        "/workflow-definitions/{item_id}/publish",
        RouteAccessCategory.AUTHENTICATED_MUTATE,
        "workflow.definition.publish",
    ),
    RouteAccessRule(
        ("POST",),
        "/workflow-definitions/{item_id}/unpublish",
        RouteAccessCategory.AUTHENTICATED_MUTATE,
        "workflow.definition.unpublish",
    ),
    RouteAccessRule(
        ("POST",),
        "/workflow-definitions/{item_id}/archive",
        RouteAccessCategory.AUTHENTICATED_MUTATE,
        "workflow.definition.archive",
    ),
    RouteAccessRule(
        ("DELETE",),
        "/workflow-definitions/{item_id}",
        RouteAccessCategory.AUTHENTICATED_MUTATE,
        "workflow.definition.delete",
    ),
    RouteAccessRule(
        ("POST",),
        "/workflow-definitions/{item_id}/rollback",
        RouteAccessCategory.AUTHENTICATED_MUTATE,
        "workflow.definition.rollback",
    ),
    RouteAccessRule(
        ("POST",),
        "/workflow-definitions/{item_id}/activate",
        RouteAccessCategory.AUTHENTICATED_MUTATE,
        "workflow.definition.activate",
    ),
    RouteAccessRule(
        ("GET",),
        "/agent-definitions",
        RouteAccessCategory.AUTHENTICATED_READ,
        "agent.definition.list",
    ),
    RouteAccessRule(
        ("GET",),
        "/agent-definitions/{item_id}",
        RouteAccessCategory.AUTHENTICATED_READ,
        "agent.definition.read",
    ),
    RouteAccessRule(
        ("GET",),
        "/agent-definitions/{item_id}/versions",
        RouteAccessCategory.AUTHENTICATED_READ,
        "agent.definition.versions.read",
    ),
    RouteAccessRule(
        ("GET",),
        "/agent-definitions/{item_id}/versions/{revision_id}",
        RouteAccessCategory.AUTHENTICATED_READ,
        "agent.definition.version.read",
    ),
    RouteAccessRule(
        ("GET",),
        "/agent-definitions/{item_id}/security-policy",
        RouteAccessCategory.AUTHENTICATED_READ,
        "agent.definition.security_policy.read",
    ),
    RouteAccessRule(
        ("GET",),
        "/agents/{item_id}/security-policy",
        RouteAccessCategory.AUTHENTICATED_READ,
        "agent.definition.security_policy.read",
    ),
    RouteAccessRule(
        ("POST",),
        "/agent-definitions",
        RouteAccessCategory.AUTHENTICATED_MUTATE,
        "agent.definition.save",
    ),
    RouteAccessRule(
        ("POST",),
        "/agent-definitions/{item_id}/publish",
        RouteAccessCategory.AUTHENTICATED_MUTATE,
        "agent.definition.publish",
    ),
    RouteAccessRule(
        ("POST",),
        "/agent-definitions/{item_id}/unpublish",
        RouteAccessCategory.AUTHENTICATED_MUTATE,
        "agent.definition.unpublish",
    ),
    RouteAccessRule(
        ("POST",),
        "/agent-definitions/{item_id}/archive",
        RouteAccessCategory.AUTHENTICATED_MUTATE,
        "agent.definition.archive",
    ),
    RouteAccessRule(
        ("DELETE",),
        "/agent-definitions/{item_id}",
        RouteAccessCategory.AUTHENTICATED_MUTATE,
        "agent.definition.delete",
    ),
    RouteAccessRule(
        ("POST",),
        "/agent-definitions/{item_id}/rollback",
        RouteAccessCategory.AUTHENTICATED_MUTATE,
        "agent.definition.rollback",
    ),
    RouteAccessRule(
        ("POST",),
        "/agent-definitions/{item_id}/activate",
        RouteAccessCategory.AUTHENTICATED_MUTATE,
        "agent.definition.activate",
    ),
    RouteAccessRule(
        ("GET",),
        "/node-definitions",
        RouteAccessCategory.AUTHENTICATED_READ,
        "node.definition.list",
    ),
    RouteAccessRule(
        ("GET",),
        "/guardrail-rulesets",
        RouteAccessCategory.AUTHENTICATED_READ,
        "guardrail.ruleset.list",
    ),
    RouteAccessRule(
        ("POST",),
        "/guardrail-rulesets",
        RouteAccessCategory.AUTHENTICATED_MUTATE,
        "guardrail.ruleset.save",
    ),
    RouteAccessRule(
        ("POST",),
        "/guardrail-rulesets/{item_id}/publish",
        RouteAccessCategory.AUTHENTICATED_MUTATE,
        "guardrail.ruleset.publish",
    ),
    RouteAccessRule(
        ("DELETE",),
        "/guardrail-rulesets/{item_id}",
        RouteAccessCategory.AUTHENTICATED_MUTATE,
        "guardrail.ruleset.delete",
    ),
    RouteAccessRule(
        ("POST",),
        "/guardrail-rulesets/{item_id}/archive",
        RouteAccessCategory.AUTHENTICATED_MUTATE,
        "guardrail.ruleset.archive",
    ),
    RouteAccessRule(
        ("POST",),
        "/guardrail-rulesets/{item_id}/activate",
        RouteAccessCategory.AUTHENTICATED_MUTATE,
        "guardrail.ruleset.activate",
    ),
    RouteAccessRule(
        ("GET",),
        "/guardrail-rulesets/{item_id}/versions",
        RouteAccessCategory.AUTHENTICATED_READ,
        "guardrail.ruleset.versions.read",
    ),
    RouteAccessRule(
        ("GET",),
        "/guardrail-rulesets/{item_id}/versions/{revision_id}",
        RouteAccessCategory.AUTHENTICATED_READ,
        "guardrail.ruleset.version.read",
    ),
    RouteAccessRule(
        ("POST",),
        "/guardrail-rulesets/{item_id}/rollback",
        RouteAccessCategory.AUTHENTICATED_MUTATE,
        "guardrail.ruleset.rollback",
    ),
    RouteAccessRule(
        ("DELETE",),
        "/node-definitions/{_item_id:path}",
        RouteAccessCategory.AUTHENTICATED_MUTATE,
        "node.definition.delete",
    ),
    RouteAccessRule(
        ("POST",), "/graph/validate", RouteAccessCategory.AUTHENTICATED_MUTATE, "graph.validate"
    ),
    RouteAccessRule(
        ("POST",), "/graph/runs", RouteAccessCategory.AUTHENTICATED_MUTATE, "graph.run"
    ),
)

_FRAMEWORK_MANAGED_PATHS = {
    "/docs",
    "/docs/oauth2-redirect",
    "/openapi.json",
    "/redoc",
}


@lru_cache(maxsize=None)
def _compiled_rule_pattern(path_template: str) -> re.Pattern[str]:
    escaped = re.escape(path_template)
    escaped = re.sub(r"\\\{[^{}]+:path\\\}", r".+", escaped)
    escaped = re.sub(r"\\\{[^{}]+\\\}", r"[^/]+", escaped)
    return re.compile(f"^{escaped}$")


def route_access_rules() -> tuple[RouteAccessRule, ...]:
    return _ROUTE_ACCESS_RULES


def describe_route_inventory() -> dict[str, list[dict[str, str]]]:
    inventory: dict[str, list[dict[str, str]]] = {
        category.value: [] for category in RouteAccessCategory
    }
    for rule in _ROUTE_ACCESS_RULES:
        inventory[rule.category.value].append(
            {
                "methods": ",".join(rule.methods),
                "path": rule.path_template,
                "action": rule.action,
            }
        )
    return inventory


def classify_route_access(method: str, path: str) -> RouteAccessRule | None:
    normalized_method = str(method or "").upper()
    normalized_path = str(path or "").strip() or "/"
    if normalized_method == "OPTIONS":
        return RouteAccessRule(("OPTIONS",), normalized_path, RouteAccessCategory.PUBLIC_MINIMAL)
    for rule in _ROUTE_ACCESS_RULES:
        if normalized_method not in rule.methods:
            continue
        if _compiled_rule_pattern(rule.path_template).match(normalized_path):
            return rule
    return None


def validate_route_inventory(app: FastAPI) -> None:
    missing: list[str] = []
    seen: set[str] = set()
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        if route.path in _FRAMEWORK_MANAGED_PATHS:
            continue
        for method in sorted(route.methods or []):
            if method in {"HEAD", "OPTIONS"}:
                continue
            key = f"{method} {route.path}"
            seen.add(key)
            if classify_route_access(method, route.path) is None:
                missing.append(key)
    if missing:
        formatted = ", ".join(sorted(missing))
        raise RuntimeError(f"Unclassified backend routes detected: {formatted}")


# --------------------------------------------------------------------------- #
# Capability effect of every mutating route (LOCUS-357)
# --------------------------------------------------------------------------- #
# On the desktop profile (``local-native``) the local-operator bootstrap
# authenticates every loopback request as the operator, so an un-jailed local
# process could otherwise widen what the agents may do. Every mutating route is
# classified here; widening needs the Tauri shell's out-of-band confirmation
# proof (``X-Locus-Shell-Proof``, ``locus_tooling/shell_confirmation.py``):
#
# * ``widening``    -- always needs the proof on the desktop;
# * ``conditional`` -- needs it when the named predicate says the change widens
#                      (``app/capability_widening.py``): body predicates are
#                      decided before the handler, state predicates by the
#                      handler right before it commits;
# * ``narrowing`` / ``neutral`` -- never need it (deny, revoke, panic, disable,
#                      remove; builder content whose effect is bounded by the
#                      proof-protected settings, gateway policy and grants).
#
# ``title`` and ``risk`` are what the shell's native dialog shows; the Rust
# table in ``apps/desktop-tauri/src-tauri/src/shell_actions.rs`` mirrors them
# byte for byte (``tests/backend/test_desktop_packaging.py``). ``current`` is a
# GET path the shell reads to show what changes. Startup refuses a mutating
# route missing from this table (``validate_shell_proof_inventory``).
class CapabilityEffect(str, Enum):
    WIDENING = "widening"
    NARROWING = "narrowing"
    CONDITIONAL = "conditional"
    NEUTRAL = "neutral"


class ShellProofFormat(str, Enum):
    #: Generic request-bound proof, verified centrally (LOCUS-357).
    REQUEST = "request"
    #: LOCUS-350 formats, verified by the user-browser handlers.
    BROWSER_TIER = "browser-tier"
    BROWSER_PAIR = "browser-pair"


@dataclass(frozen=True)
class ShellProofRule:
    method: str
    path_template: str
    effect: CapabilityEffect
    action: str = ""
    predicate: str = ""
    title: str = ""
    risk: str = ""
    current: str = ""
    proof: ShellProofFormat = ShellProofFormat.REQUEST

    @property
    def may_need_proof(self) -> bool:
        return self.effect in {CapabilityEffect.WIDENING, CapabilityEffect.CONDITIONAL}


def _neutral(method: str, path: str) -> ShellProofRule:
    return ShellProofRule(method, path, CapabilityEffect.NEUTRAL)


def _narrowing(method: str, path: str) -> ShellProofRule:
    return ShellProofRule(method, path, CapabilityEffect.NARROWING)


def _widening(
    method: str,
    path: str,
    action: str,
    title: str = "",
    risk: str = "",
    *,
    current: str = "",
    proof: ShellProofFormat = ShellProofFormat.REQUEST,
) -> ShellProofRule:
    return ShellProofRule(
        method, path, CapabilityEffect.WIDENING, action, "", title, risk, current, proof
    )


def _conditional(
    method: str,
    path: str,
    action: str,
    predicate: str,
    title: str = "",
    risk: str = "",
    *,
    current: str = "",
    proof: ShellProofFormat = ShellProofFormat.REQUEST,
) -> ShellProofRule:
    return ShellProofRule(
        method, path, CapabilityEffect.CONDITIONAL, action, predicate, title, risk, current, proof
    )


_RISK_PROVIDER_KEY = "Agents may send your prompts and data to this model provider using this key."
_RISK_GUARDRAIL_CHANGE = (
    "Changes the guardrails the agents run under. The rules that become active "
    "may be weaker than the current ones."
)
_RISK_GUARDRAIL_REMOVE = (
    "Removes this ruleset from the active guardrails. Workflows that use it lose its rules."
)
_RISK_SCHEDULE = "The workflow will start on its own on this schedule, without you."

_SHELL_PROOF_RULES: tuple[ShellProofRule, ...] = (
    # --- auth / system -------------------------------------------------------
    _neutral("POST", "/auth/login"),
    _neutral("POST", "/auth/register"),
    _narrowing("POST", "/auth/logout"),
    _neutral("POST", "/system/update/prepare"),  # signed update bundles only
    _neutral("POST", "/system/update/cancel"),
    _narrowing("POST", "/system/shutdown"),
    # --- self-improvement loop (Settings → Loop & Linear) ------------------------
    _widening(
        "POST",
        "/loop/enable",
        "loop.enable",
        "Turn on the self-improvement loop",
        "The loop picks eligible Linear issues, runs agents on them and opens pull "
        "requests without asking each time.",
    ),
    _narrowing("POST", "/loop/disable"),
    _widening(
        "POST",
        "/loop/autostart",
        "loop.autostart.enable",
        "Start the loop with Locus",
        "The loop starts on this checkout every time Locus starts, also after updates.",
    ),
    _narrowing("DELETE", "/loop/autostart"),
    # --- builder content (bounded by settings, gateway policy and grants) ----
    _neutral("POST", "/agent-definitions/import"),
    _neutral("POST", "/workflow-definitions/import"),
    _neutral("POST", "/playbooks/import"),
    _neutral("POST", "/bundle/import"),
    # --- skills ---------------------------------------------------------------
    _conditional(
        "PUT",
        "/skills/user",
        "skills.user.write",
        "user_skills_widening",
        "Add skills to your agents",
        "The added skills will be loaded into your agents.",
        current="/skills/user",
    ),
    _conditional(
        "POST",
        "/skills",
        "skill.save",
        "skill_save_enables",
        "Enable or change a skill",
        "The instructions of this skill will be given to the agents.",
    ),
    _neutral("POST", "/skills/{skill_id}/eval"),
    _widening(
        "POST",
        "/skills/{skill_id}/promote",
        "skill.promote",
        "Trust and promote a skill",
        "Signs the skill as trusted and raises its tier. Its scripts may then run.",
    ),
    _widening(
        "POST",
        "/skills/import",
        "skill.import",
        "Install a skill",
        "Fetches the skill and installs it in quarantine. It cannot run until it is "
        "scanned and promoted.",
    ),
    _neutral("POST", "/skills/{skill_id}/scan"),
    _neutral("POST", "/skills/{skill_id}/test"),
    _narrowing("DELETE", "/skills/{skill_id}"),
    _narrowing("POST", "/skills/{skill_id}/revoke"),
    # --- model providers ------------------------------------------------------
    _widening(
        "PUT",
        "/runtime/user-providers/{provider}",
        "runtime.user_providers.write",
        "Set a model provider key",
        _RISK_PROVIDER_KEY,
    ),
    _narrowing("DELETE", "/runtime/user-providers/{provider}"),
    _widening(
        "PUT",
        "/models/providers/{provider_id}/key",
        "models.provider.key.set",
        "Set a model provider key",
        _RISK_PROVIDER_KEY,
    ),
    _narrowing("DELETE", "/models/providers/{provider_id}/key"),
    _neutral("POST", "/models/local/pull"),
    _narrowing("DELETE", "/models/local/{model_id}"),
    # --- settings -------------------------------------------------------------
    _conditional(
        "PUT",
        "/user/settings",
        "user.settings.save",
        "user_settings_widening",
        "Change your default chat mode",
        "New chats will start in a mode that lets the agent do more, without you switching modes.",
        current="/user/settings",
    ),
    _conditional(
        "POST",
        "/platform/settings",
        "platform.settings.save",
        "platform_settings_widening",
        "Loosen platform settings",
        "These changes loosen platform security for every agent: allowlists, "
        "approvals, limits, guardrail enforcement, runtimes or model providers.",
        current="/platform/settings",
    ),
    # --- approvals and grants -------------------------------------------------
    _widening(
        "POST",
        "/workflow-runs/{run_id}/escalations/{escalation_id}/approve",
        "workflow.run.escalations.approve",
        "Approve an agent request",
        "The agent may perform this action. A run or standing scope also mints a "
        "grant, so matching actions stop asking.",
        current="/workflow-runs/{run_id}/escalations",
    ),
    _narrowing("POST", "/gateway/grants/{grant_id}/revoke"),
    _conditional(
        "POST",
        "/approvals",
        "approval.submit",
        "approval_decision_approves",
        "Approve a run",
        "Marks the result of the run as approved and the run as done.",
    ),
    # --- computer use and the user's browser ----------------------------------
    _narrowing("POST", "/computer-use/panic"),
    _widening(
        "POST",
        "/computer-use/reset",
        "computer_use.reset",
        "Resume computer use",
        "Clears the panic stop: the agent may control the desktop and browsers again.",
    ),
    _widening(
        "POST",
        "/user-browser/pairing",
        "user_browser.pair",
        proof=ShellProofFormat.BROWSER_PAIR,
    ),
    _narrowing("DELETE", "/user-browser/pairing"),
    _conditional(
        "PUT",
        "/user-browser/tier",
        "user_browser.tier.set",
        "browser_tier_widening",
        proof=ShellProofFormat.BROWSER_TIER,
    ),
    # Native-messaging host only (loopback, no browser headers, pairing key).
    _neutral("POST", "/user-browser/relay/hello"),
    _neutral("POST", "/user-browser/relay/next"),
    _neutral("POST", "/user-browser/relay/result"),
    _neutral("POST", "/user-browser/relay/event"),
    _neutral("POST", "/user-browser/relay/bye"),
    # --- memory and internal services -----------------------------------------
    _narrowing("DELETE", "/memory/{session_id}"),
    _neutral("POST", "/internal/memory/consolidation/run"),
    _neutral("POST", "/internal/memory/world-graph/project"),
    _neutral("POST", "/internal/cognition/assemblies/run"),
    _neutral("POST", "/internal/cognition/messages/admit"),
    # --- runs (every action is checked by the gateway) ------------------------
    _neutral("POST", "/workflow-runs"),
    _neutral("POST", "/workflow-runs/{run_id}/messages"),
    _neutral("POST", "/workflow-runs/{run_id}/rename"),
    _neutral("PATCH", "/workflow-runs/{run_id}"),
    _neutral("POST", "/workflow-runs/{run_id}/archive"),
    _neutral("POST", "/artifacts/{artifact_id}/versions"),
    _neutral("POST", "/graph/validate"),
    _neutral("POST", "/graph/runs"),
    # --- triggers and schedules -----------------------------------------------
    _widening(
        "POST",
        "/workflow-definitions/{item_id}/triggers",
        "workflow.trigger.create",
        "Create a webhook trigger",
        "Anyone who has the webhook URL can start this workflow, without you.",
    ),
    _narrowing("DELETE", "/triggers/{token}"),
    _neutral("POST", "/triggers/webhook/{token}"),  # authenticated by the trigger token
    _conditional(
        "POST",
        "/workflow-definitions/{item_id}/schedules",
        "workflow.schedule.create",
        "schedule_enabled",
        "Schedule a workflow",
        _RISK_SCHEDULE,
    ),
    _conditional(
        "POST",
        "/schedules/{schedule_id}/toggle",
        "workflow.schedule.toggle",
        "schedule_toggle_enables",
        "Turn on a schedule",
        _RISK_SCHEDULE,
    ),
    _narrowing("DELETE", "/schedules/{schedule_id}"),
    # --- knowledge --------------------------------------------------------------
    _neutral("POST", "/knowledge/collections"),
    _narrowing("DELETE", "/knowledge/collections/{collection_id}"),
    _neutral("POST", "/knowledge/collections/{collection_id}/documents"),
    _neutral("POST", "/knowledge/collections/{collection_id}/search"),
    # --- integrations and MCP ---------------------------------------------------
    _widening(
        "POST",
        "/integrations/catalog/{catalog_id}/install",
        "integration.catalog.install",
        "Install an integration",
        "Adds this integration to the services the agents may use.",
    ),
    _widening(
        "POST",
        "/integrations/mcp",
        "integration.mcp.save",
        "Add or change an MCP server",
        "Registers this MCP server. Once approved, the agents may call its tools.",
    ),
    _neutral("POST", "/integrations/mcp/{connection_id}/validate"),
    _widening(
        "POST",
        "/integrations/mcp/{connection_id}/approve",
        "integration.mcp.approve",
        "Approve an MCP server",
        "The agents may call the tools of this MCP server.",
    ),
    _widening(
        "POST",
        "/integrations/{integration_id}/oauth/connect",
        "integration.oauth.connect",
        "Connect an account",
        "Starts sign-in so the agents may act with the access of this account.",
    ),
    _neutral("POST", "/integrations/{integration_id}/oauth/refresh"),
    _narrowing("POST", "/integrations/{integration_id}/oauth/disconnect"),
    _widening(
        "POST",
        "/integrations",
        "integration.save",
        "Add or change an integration",
        "The agents may call this service with the credentials stored for it.",
    ),
    _neutral("POST", "/integrations/{integration_id}/test"),
    _narrowing("DELETE", "/integrations/{integration_id}"),
    # --- inbox, templates, playbooks, collaboration ----------------------------
    _neutral("POST", "/inbox/groups"),
    _neutral("POST", "/inbox/groups/{group_id}"),
    _neutral("DELETE", "/inbox/groups/{group_id}"),
    _neutral("POST", "/templates/agents/{template_id}/instantiate"),
    _neutral("POST", "/templates/workflows/{workflow_id}/instantiate"),
    _neutral("POST", "/playbooks"),
    _neutral("POST", "/playbooks/{playbook_id}/publish"),
    _narrowing("POST", "/playbooks/{playbook_id}/unpublish"),
    _narrowing("POST", "/playbooks/{playbook_id}/archive"),
    _neutral("POST", "/playbooks/{playbook_id}/instantiate"),
    _neutral("POST", "/collab/sessions/join"),
    _neutral("POST", "/collab/sessions/{session_id}/sync"),
    _neutral("POST", "/collab/sessions/{session_id}/permissions"),  # human roles
    # --- workflow and agent definitions ----------------------------------------
    _neutral("POST", "/workflow-definitions"),
    _neutral("POST", "/workflow-definitions/{item_id}/publish"),
    _narrowing("POST", "/workflow-definitions/{item_id}/unpublish"),
    _narrowing("POST", "/workflow-definitions/{item_id}/archive"),
    _narrowing("DELETE", "/workflow-definitions/{item_id}"),
    _neutral("POST", "/workflow-definitions/{item_id}/rollback"),
    _neutral("POST", "/workflow-definitions/{item_id}/activate"),
    _neutral("POST", "/agent-definitions"),
    _neutral("POST", "/agent-definitions/{item_id}/publish"),
    _narrowing("POST", "/agent-definitions/{item_id}/unpublish"),
    _narrowing("POST", "/agent-definitions/{item_id}/archive"),
    _narrowing("DELETE", "/agent-definitions/{item_id}"),
    _neutral("POST", "/agent-definitions/{item_id}/rollback"),
    _neutral("POST", "/agent-definitions/{item_id}/activate"),
    _neutral("DELETE", "/node-definitions/{_item_id:path}"),
    # --- guardrails: a draft is inert; anything that changes or removes the
    # active rules can weaken them -----------------------------------------------
    _neutral("POST", "/guardrail-rulesets"),
    _widening(
        "POST",
        "/guardrail-rulesets/{item_id}/publish",
        "guardrail.ruleset.publish",
        "Publish a guardrail ruleset",
        _RISK_GUARDRAIL_CHANGE,
    ),
    _widening(
        "POST",
        "/guardrail-rulesets/{item_id}/activate",
        "guardrail.ruleset.activate",
        "Activate a guardrail ruleset revision",
        _RISK_GUARDRAIL_CHANGE,
    ),
    _widening(
        "POST",
        "/guardrail-rulesets/{item_id}/rollback",
        "guardrail.ruleset.rollback",
        "Roll back a guardrail ruleset",
        _RISK_GUARDRAIL_CHANGE,
    ),
    _widening(
        "POST",
        "/guardrail-rulesets/{item_id}/archive",
        "guardrail.ruleset.archive",
        "Archive a guardrail ruleset",
        _RISK_GUARDRAIL_REMOVE,
    ),
    _widening(
        "DELETE",
        "/guardrail-rulesets/{item_id}",
        "guardrail.ruleset.delete",
        "Delete a guardrail ruleset",
        _RISK_GUARDRAIL_REMOVE,
    ),
)

_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_UNSAFE_DIALOG_TEXT = re.compile(r"[\"\\\x00-\x1f\x7f-￿]")


def shell_proof_rules() -> tuple[ShellProofRule, ...]:
    return _SHELL_PROOF_RULES


def classify_shell_proof(method: str, path: str) -> ShellProofRule | None:
    """The capability-effect rule for a concrete request (``None``: unclassified)."""
    normalized_method = str(method or "").upper()
    normalized_path = str(path or "").strip() or "/"
    for rule in _SHELL_PROOF_RULES:
        if rule.method != normalized_method:
            continue
        if _compiled_rule_pattern(rule.path_template).match(normalized_path):
            return rule
    return None


def shell_proof_table_errors() -> list[str]:
    """Internal consistency of the table (also checked at startup)."""
    from app.capability_widening import BODY_PREDICATES, STATE_PREDICATES

    errors: list[str] = []
    seen_routes: set[tuple[str, str]] = set()
    seen_actions: set[str] = set()
    for rule in _SHELL_PROOF_RULES:
        key = (rule.method, rule.path_template)
        if key in seen_routes:
            errors.append(f"duplicate rule {rule.method} {rule.path_template}")
        seen_routes.add(key)
        if rule.method not in _MUTATING_METHODS:
            errors.append(f"not a mutating method: {rule.method} {rule.path_template}")
        if not rule.may_need_proof:
            continue
        if not rule.action or rule.action in seen_actions:
            errors.append(f"missing or duplicate action id: {rule.method} {rule.path_template}")
        seen_actions.add(rule.action)
        if rule.effect == CapabilityEffect.CONDITIONAL and (
            rule.predicate not in BODY_PREDICATES and rule.predicate not in STATE_PREDICATES
        ):
            errors.append(f"unknown predicate {rule.predicate!r} for {rule.action}")
        if rule.proof == ShellProofFormat.REQUEST and not (rule.title and rule.risk):
            errors.append(f"dialog title and risk text required for {rule.action}")
        if any(_UNSAFE_DIALOG_TEXT.search(text) for text in (rule.title, rule.risk, rule.current)):
            errors.append(f"dialog texts must be plain ASCII without quotes: {rule.action}")
    return errors


def validate_shell_proof_inventory(app: FastAPI) -> None:
    """Every mutating route must have its own capability-effect rule."""
    problems = shell_proof_table_errors()
    routes: set[tuple[str, str]] = set()
    for route in app.routes:
        if not isinstance(route, APIRoute) or route.path in _FRAMEWORK_MANAGED_PATHS:
            continue
        for method in sorted((route.methods or set()) & _MUTATING_METHODS):
            routes.add((method, route.path))
            rule = classify_shell_proof(method, route.path)
            if rule is None or rule.path_template != route.path:
                problems.append(f"{method} {route.path}")
    problems.extend(
        f"stale rule {rule.method} {rule.path_template}"
        for rule in _SHELL_PROOF_RULES
        if (rule.method, rule.path_template) not in routes
    )
    if problems:
        formatted = ", ".join(sorted(problems))
        raise RuntimeError(f"Unclassified capability effect for backend routes: {formatted}")


__all__ = [
    "CapabilityEffect",
    "RouteAccessCategory",
    "RouteAccessRule",
    "ShellProofFormat",
    "ShellProofRule",
    "classify_route_access",
    "classify_shell_proof",
    "describe_route_inventory",
    "route_access_rules",
    "shell_proof_rules",
    "shell_proof_table_errors",
    "validate_route_inventory",
    "validate_shell_proof_inventory",
]
