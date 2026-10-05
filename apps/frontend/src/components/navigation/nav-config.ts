import {
  ActivityIcon,
  BrainIcon,
  HomeIcon,
  LibraryIcon,
  SettingsIcon,
  type LucideIcon,
} from "lucide-react";

/**
 * One navigation for the person who opened the app (LOCUS-353): no user /
 * builder modes and no role gating. The backend still authorizes every call;
 * the UI only decides where things live.
 */
export type NavLink = {
  href: string;
  label: string;
  /** Extra path prefixes that also mark this entry active. */
  matches?: string[];
};

export type NavItem = NavLink & {
  icon: LucideIcon;
  /** Shown under the item while it is the active area. */
  children?: NavLink[];
};

export const LIBRARY_LINKS: NavLink[] = [
  { href: "/library/skills", label: "Skills" },
  { href: "/library/playbooks", label: "Playbooks" },
  { href: "/library/workflows", label: "Workflows", matches: ["/workflows"] },
  { href: "/library/agents", label: "Agents" },
  { href: "/library/connections", label: "Connectors" },
  { href: "/library/knowledge", label: "Knowledge" },
  { href: "/library/templates", label: "Templates" },
  { href: "/library/guardrails", label: "Guardrails" },
  { href: "/library/nodes", label: "Node library" },
  { href: "/library/releases", label: "Published revisions" },
];

export const ACTIVITY_LINKS: NavLink[] = [
  { href: "/activity", label: "Runs" },
  { href: "/activity/traces", label: "Traces" },
  { href: "/artifacts", label: "Artifacts" },
];

export const PRIMARY_NAV: NavItem[] = [
  { href: "/home", label: "Home", icon: HomeIcon },
  { href: "/activity", label: "Activity", icon: ActivityIcon, matches: ["/artifacts"], children: ACTIVITY_LINKS },
  { href: "/memory", label: "Memory", icon: BrainIcon },
  { href: "/library", label: "Library", icon: LibraryIcon, matches: ["/workflows"], children: LIBRARY_LINKS },
  { href: "/settings", label: "Settings", icon: SettingsIcon },
];

function pathMatches(pathname: string, prefix: string): boolean {
  return pathname === prefix || pathname.startsWith(`${prefix}/`);
}

/** Whether a nav entry is the current location. Child links match their own
 * page and its sub-pages; `/activity` (Runs) matches only itself. */
export function isNavLinkActive(pathname: string, link: NavLink, { exact = false }: { exact?: boolean } = {}): boolean {
  const ownMatch = exact ? pathname === link.href : pathMatches(pathname, link.href);
  return ownMatch || (link.matches ?? []).some((prefix) => pathMatches(pathname, prefix));
}

/** The primary area that owns `pathname`, if any. */
export function activeNavItem(pathname: string): NavItem | null {
  return PRIMARY_NAV.find((item) => isNavLinkActive(pathname, item)) ?? null;
}
