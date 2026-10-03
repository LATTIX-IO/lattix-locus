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


__all__ = [
    "RouteAccessCategory",
    "RouteAccessRule",
    "classify_route_access",
    "describe_route_inventory",
    "route_access_rules",
    "validate_route_inventory",
]
