import type { OperatorSession, PlatformSettings, SecurityPolicyResponse } from "@/types/frontier";

export type BuilderSettingsSectionKey = "guardrails" | "network" | "runtime" | "governance";

type BuilderSettingsCapability = keyof OperatorSession["capabilities"];

export type BuilderSettingsSection = {
  key: BuilderSettingsSectionKey;
  href: string;
  navLabel: string;
  title: string;
  summary: string;
  detail: string;
  requiredCapability?: BuilderSettingsCapability;
  allowedRoles?: string[];
};

export const builderSettingsSections: readonly BuilderSettingsSection[] = [
  {
    key: "guardrails",
    href: "/builder/settings/guardrails",
    navLabel: "Guardrails",
    title: "Guardrails & Approvals",
    summary: "Baseline safety and approval gates.",
    detail: "Set the default safety envelope for builders, agents, and workflows.",
  },
  {
    key: "network",
    href: "/builder/settings/network",
    navLabel: "Network",
    title: "Network & Retrieval",
    summary: "Egress, retrieval, and MCP boundaries.",
    detail: "Control where runtime traffic and retrieval are allowed to go.",
  },
  {
    key: "runtime",
    href: "/builder/settings/runtime",
    navLabel: "Runtime",
    title: "Runtime & Inference",
    summary: "Engines, limits, and model backends.",
    detail: "Set orchestration ceilings and inference backends for the platform.",
  },
  {
    key: "governance",
    href: "/builder/settings/governance",
    navLabel: "Governance",
    title: "Approvals & Governance",
    summary: "Admin controls and emergency switches.",
    detail: "Manage admin-only approvals, auth gates, and emergency operating controls.",
    requiredCapability: "can_admin",
  },
] as const;

export function getBuilderSettingsSection(key: BuilderSettingsSectionKey): BuilderSettingsSection {
  const section = builderSettingsSections.find((item) => item.key === key);
  if (!section) {
    throw new Error(`Unknown builder settings section: ${key}`);
  }
  return section;
}

function hasRoleAccess(session: OperatorSession | null, section: BuilderSettingsSection): boolean {
  if (!section.allowedRoles?.length) {
    return true;
  }
  if (!session) {
    return false;
  }
  return section.allowedRoles.some((role) => session.roles.includes(role));
}

export function isBuilderSettingsSectionVisible(section: BuilderSettingsSection, session: OperatorSession | null): boolean {
  if (section.requiredCapability && !session?.capabilities[section.requiredCapability]) {
    return false;
  }
  return hasRoleAccess(session, section);
}

export function getVisibleBuilderSettingsSections(session: OperatorSession | null): BuilderSettingsSection[] {
  return builderSettingsSections.filter((section) => isBuilderSettingsSectionVisible(section, session));
}

export function getBuilderSettingsNavBadge(
  sectionKey: BuilderSettingsSectionKey,
  settings: PlatformSettings | null,
  policy: SecurityPolicyResponse | null,
): string | null {
  switch (sectionKey) {
    case "guardrails": {
      if (!settings) {
        return null;
      }
      const blockedCount = settings.global_blocked_keywords?.length ?? 0;
      if (blockedCount > 0) {
        return `${blockedCount} blocked`;
      }
      return settings.enable_foss_guardrail_signals ? "signals on" : "signals off";
    }
    case "network": {
      if (!settings) {
        return null;
      }
      if (settings.enforce_local_network_only) {
        return "local only";
      }
      if (settings.enforce_egress_allowlist) {
        return `${settings.allowed_egress_hosts?.length ?? 0} hosts`;
      }
      return "open";
    }
    case "runtime": {
      if (!settings) {
        return null;
      }
      return `${settings.allowed_runtime_engines?.length ?? 0} engines`;
    }
    case "governance": {
      if (!settings || !policy) {
        return null;
      }
      if (settings.emergency_read_only_mode) {
        return "read-only";
      }
      if (settings.require_human_approval) {
        return "review all";
      }
      const blockCount = [settings.block_new_runs, settings.block_graph_runs, settings.block_tool_calls, settings.block_retrieval_calls].filter(Boolean).length;
      if (blockCount > 0) {
        return `${blockCount} blocks`;
      }
      return policy.platform_defaults.require_human_approval_for_high_risk_tools ? "review gated" : "monitored";
    }
    default:
      return null;
  }
}