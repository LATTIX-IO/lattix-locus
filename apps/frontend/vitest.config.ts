import { defineConfig } from "vitest/config";
import path from "node:path";

export default defineConfig({
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.{test,spec}.{ts,tsx}"],
    // Resource caps for local-first machines: without these, vitest forks one
    // node worker per CPU core (each loading jsdom + React), which can exhaust
    // system memory on high-core hosts. Keep the suite to two bounded workers.
    // (For a per-worker heap cap, set NODE_OPTIONS=--max-old-space-size=1024
    // in the shell; vitest 4 no longer exposes per-pool execArgv config.)
    maxWorkers: 2,
    pool: "forks",
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
});
