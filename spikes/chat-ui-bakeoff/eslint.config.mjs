import js from "@eslint/js";
import reactHooks from "eslint-plugin-react-hooks";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["**/dist/**", "**/node_modules/**", "results/**"] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["**/*.{ts,tsx}"],
    plugins: { "react-hooks": reactHooks },
    rules: { ...reactHooks.configs.recommended.rules },
  },
  {
    files: ["bench/**/*.mjs"],
    rules: { "@typescript-eslint/no-unused-vars": ["error", { ignoreRestSiblings: true }] },
    languageOptions: { globals: { process: "readonly", Buffer: "readonly", URL: "readonly", console: "readonly", window: "readonly", document: "readonly", performance: "readonly", getComputedStyle: "readonly", requestAnimationFrame: "readonly" } },
  },
);
