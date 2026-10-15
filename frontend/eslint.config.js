// ESLint flat config (DESIGN-tooling T3). scripts/check-boundaries.mjs runs after it.
import js from "@eslint/js";
import reactHooks from "eslint-plugin-react-hooks";
import globals from "globals";
import tseslint from "typescript-eslint";

const TS_FILES = ["**/*.{ts,tsx}"];
const TEST_FILES = ["**/*.test.ts", "**/*.test.tsx"];

const tauriApi = {
  paths: [{ name: "@tauri-apps/api", message: "Only src/lib/backend/tauriTransport.ts talks to Tauri." }],
  patterns: [{ group: ["@tauri-apps/api/*"], message: "Only src/lib/backend/tauriTransport.ts talks to Tauri." }],
};
const fixtureTransport = {
  regex: "(^|/)fixtureTransport(\\.ts)?$",
  message: "The fixture transport is selected only in src/main.tsx (and used by tests).",
};
const cssTokens = {
  regex: "(^|/)test/cssTokens(\\.ts)?$",
  message: "src/test/cssTokens is test support; import it only from test files.",
};

function restrictedImports({ tauri, fixture, tokens }) {
  return [
    "error",
    {
      paths: tauri ? tauriApi.paths : [],
      patterns: [...(tauri ? tauriApi.patterns : []), ...(fixture ? [fixtureTransport] : []), ...(tokens ? [cssTokens] : [])],
    },
  ];
}

// Warnings fail nothing on their own, so the recommended hook rules run as errors.
const reactHooksRecommended = reactHooks.configs.flat.recommended;
const reactHooksAsErrors = Object.fromEntries(
  Object.entries(reactHooksRecommended.rules).map(([rule, level]) => [rule, level === "warn" ? "error" : level]),
);

export default tseslint.config(
  { ignores: ["dist/", "dist-browser/", "dist-fixture/", "test-results/", "playwright-report/"] },
  js.configs.recommended,
  ...tseslint.configs.strictTypeChecked.map((config) => ({ ...config, files: config.files ?? TS_FILES })),
  {
    files: TS_FILES,
    languageOptions: {
      parserOptions: { projectService: true, tsconfigRootDir: import.meta.dirname },
    },
  },
  { ...reactHooksRecommended, files: TS_FILES, rules: reactHooksAsErrors },
  {
    files: ["src/**/*.{ts,tsx}"],
    languageOptions: { globals: globals.browser },
  },
  {
    files: ["vite.config.ts", "tooling/**/*.ts", "e2e/**/*.ts", "playwright*.config.ts", "**/*.{js,mjs}"],
    languageOptions: { globals: globals.node },
  },
  { ...tseslint.configs.disableTypeChecked, files: ["**/*.{js,mjs}"] },
  // Import boundaries. Flat config does not merge one rule's options across
  // objects, so each file group states its full list; later objects win.
  { files: TS_FILES, rules: { "no-restricted-imports": restrictedImports({ tauri: true, fixture: true, tokens: true }) } },
  {
    files: ["src/lib/backend/tauriTransport.ts"],
    rules: { "no-restricted-imports": restrictedImports({ tauri: false, fixture: true, tokens: true }) },
  },
  {
    files: ["src/main.tsx"],
    rules: { "no-restricted-imports": restrictedImports({ tauri: true, fixture: false, tokens: true }) },
  },
  {
    files: TEST_FILES,
    rules: { "no-restricted-imports": restrictedImports({ tauri: true, fixture: false, tokens: false }) },
  },
  {
    files: ["src/**/*.{ts,tsx}"],
    ignores: TEST_FILES,
    rules: {
      "no-restricted-syntax": [
        "error",
        {
          selector:
            "JSXAttribute[name.name='style'] > JSXExpressionContainer > ObjectExpression > Property:not([key.value=/^--/])",
          message: "Inline styles may set CSS custom properties (--name) only; use a CSS module and tokens.",
        },
        {
          selector: "PrivateIdentifier",
          message: "Use TypeScript `private` instead of #private members (the boundary scan reads #name as a colour).",
        },
        {
          selector: "JSXAttribute[name.name=/^(size|strokeWidth|width|height)$/] Literal[raw=/^['\"]?[0-9.]/]",
          message: "Size icons and elements with the .icon classes and tokens, not numeric attributes.",
        },
      ],
    },
  },
);
