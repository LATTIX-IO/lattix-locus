import type { NextConfig } from "next";
import path from "node:path";

// Installs from before the xFrontier -> Locus rename still set FRONTIER_* variables.
// Alias them to LOCUS_* (an explicit LOCUS_* value wins) before Next reads env.
for (const [key, value] of Object.entries(process.env)) {
  const current = key.startsWith("FRONTIER_")
    ? `LOCUS_${key.slice("FRONTIER_".length)}`
    : key === "NEXT_PUBLIC_FRONTIER_ACTOR"
      ? "NEXT_PUBLIC_LOCUS_ACTOR"
      : null;
  if (current && process.env[current] === undefined) {
    process.env[current] = value;
  }
}

const nextConfig: NextConfig = {
  output: "standalone",
  reactCompiler: true,
  turbopack: {
    root: path.resolve(__dirname),
  },
  async rewrites() {
    // Same-origin API proxy for deployments without an external gateway (e.g. the
    // native desktop app): the browser calls /api on this origin and the Next
    // server proxies to the backend, so the operator session cookie is first-party
    // and just works. In the Docker/hosted stack Caddy handles /api before it ever
    // reaches Next, so this rewrite is a harmless no-op there.
    const backend = process.env.LOCUS_BACKEND_PROXY_URL || "http://127.0.0.1:8000";
    return [{ source: "/api/:path*", destination: `${backend}/:path*` }];
  },
  async redirects() {
    // One navigation, no modes (LOCUS-353): the pre-unification routes keep
    // working as links and bookmarks. Next carries the query string over.
    const temporary = (source: string, destination: string) => ({ source, destination, permanent: false });
    const libraryMoves = ["agents", "workflows", "playbooks", "templates", "skills", "knowledge", "nodes", "guardrails", "releases"];
    return [
      temporary("/inbox", "/activity"),
      temporary("/runs/:id", "/activity?session=:id&details=1"),
      temporary("/tasks/:id", "/activity?session=:id"),
      temporary("/targets", "/activity"),
      temporary("/playbooks", "/library/playbooks"),
      temporary("/guardrails", "/library/guardrails"),
      temporary("/workflows", "/workflows/start"),
      temporary("/builder", "/library"),
      temporary("/builder/agent/:id", "/library/agents/:id"),
      temporary("/builder/workflow/:id", "/library/workflows/:id"),
      ...libraryMoves.flatMap((name) => [
        temporary(`/builder/${name}`, `/library/${name}`),
        temporary(`/builder/${name}/:path*`, `/library/${name}/:path*`),
      ]),
      temporary("/builder/integrations", "/library/connections"),
      temporary("/builder/observability", "/activity/traces"),
      temporary("/builder/models", "/settings?section=engines"),
      // The old builder settings tabs and sub-pages all live in one Settings page.
      ...["guardrails", "network", "runtime", "governance"].map((tab) =>
        temporary(`/builder/settings/${tab}`, "/settings?section=policies"),
      ),
      {
        source: "/builder/settings",
        has: [{ type: "query" as const, key: "tab", value: "providers" }],
        destination: "/settings?section=engines",
        permanent: false,
      },
      temporary("/builder/settings", "/settings"),
    ];
  },
  async headers() {
    return [
      {
        source: "/(.*)",
        headers: [
          { key: "X-Frame-Options", value: "DENY" },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
        ],
      },
      {
        source: "/_next/static/(.*)",
        headers: [
          { key: "Cache-Control", value: "public, max-age=31536000, immutable" },
        ],
      },
    ];
  },
};

export default nextConfig;
