import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Same config for both variants. `modulePreload.polyfill: false` keeps the HTML free of inline
// script so the strict CSP meta tests the library, not the bundler.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    modulePreload: { polyfill: false },
    sourcemap: false,
    reportCompressedSize: true,
    assetsInlineLimit: 0,
  },
  define: { "process.env.NODE_ENV": JSON.stringify("production") },
});
