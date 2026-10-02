import type { NextConfig } from "next";
import path from "node:path";

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
    const backend = process.env.FRONTIER_BACKEND_PROXY_URL || "http://127.0.0.1:8000";
    return [{ source: "/api/:path*", destination: `${backend}/:path*` }];
  },
  async redirects() {
    return [
      {
        source: "/builder/agent/:id",
        destination: "/builder/agents/:id",
        permanent: false,
      },
      {
        source: "/builder/workflow/:id",
        destination: "/builder/workflows/:id",
        permanent: false,
      },
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
