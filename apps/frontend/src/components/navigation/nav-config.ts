export type NavMode = "user" | "builder";

export type NavIconName =
  | "inbox"
  | "workflow"
  | "artifact"
  | "studio"
  | "agent"
  | "nodes"
  | "guardrails"
  | "integrations"
  | "releases"
  | "settings"
  | "templates"
  | "playbooks"
  | "observability"
  | "admin";

export type NavItem = {
  href: string;
  label: string;
  icon: NavIconName;
};

export type NavGroup = {
  title: string;
  items: NavItem[];
};

const userNavGroups: NavGroup[] = [
  {
    title: "Work",
    items: [
      { href: "/inbox", label: "Inbox", icon: "inbox" },
      { href: "/artifacts", label: "Artifacts", icon: "artifact" },
    ],
  },
];

const builderNavGroups: NavGroup[] = [
  {
    title: "Build",
    items: [
      { href: "/builder/templates", label: "Templates", icon: "templates" },
      { href: "/builder/agents", label: "Agent Studio", icon: "agent" },
      { href: "/builder/workflows", label: "Workflow Studio", icon: "studio" },
      { href: "/builder/playbooks", label: "Playbooks", icon: "playbooks" },
    ],
  },
  {
    title: "Configure",
    items: [
      { href: "/builder/skills", label: "Skills", icon: "templates" },
      { href: "/builder/knowledge", label: "Knowledge", icon: "artifact" },
      { href: "/builder/models", label: "Models", icon: "nodes" },
      { href: "/builder/observability", label: "Observability", icon: "observability" },
      { href: "/builder/integrations", label: "Integrations", icon: "integrations" },
      { href: "/builder/nodes", label: "Node Library", icon: "nodes" },
      { href: "/builder/guardrails", label: "Guardrails", icon: "guardrails" },
      { href: "/builder/releases", label: "Releases", icon: "releases" },
    ],
  },
];

const adminNavGroup: NavGroup = {
  title: "Admin",
  items: [{ href: "/settings", label: "Administration", icon: "admin" }],
};

export function getPrimaryNavGroups(mode: NavMode, inAdmin: boolean) {
  const groups = mode === "builder" ? [...builderNavGroups] : [...userNavGroups];

  if (inAdmin) {
    groups.push(adminNavGroup);
  }

  return groups;
}

export function getPreferenceNavItem(mode: NavMode): NavItem {
  return mode === "builder"
    ? { href: "/builder/settings", label: "Settings", icon: "settings" }
    : { href: "/settings", label: "Settings", icon: "settings" };
}
