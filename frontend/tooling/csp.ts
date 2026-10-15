// Builds the Content-Security-Policy header for `vite preview` from the CSP in
// src-tauri/tauri.conf.json, so Playwright runs under the app's own policy.

import { readFileSync } from "node:fs";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Serializes a Tauri CSP object (directive name to source list) as a header value. */
export function cspHeaderFromObject(csp: Readonly<Record<string, unknown>>): string {
  const directives = Object.entries(csp).map(([name, sources]) => {
    if (typeof sources !== "string" || sources.trim() === "") {
      throw new Error(`tauri.conf.json CSP directive ${name} must be a non-empty string`);
    }
    return `${name} ${sources.trim()}`;
  });
  if (directives.length === 0) {
    throw new Error("tauri.conf.json CSP has no directives");
  }
  return directives.join("; ");
}

/** Reads app.security.csp from tauri.conf.json and returns the header value. */
export function cspHeaderFromTauriConfig(configPath: string): string {
  const config: unknown = JSON.parse(readFileSync(configPath, "utf8"));
  const csp =
    isRecord(config) && isRecord(config.app) && isRecord(config.app.security)
      ? config.app.security.csp
      : undefined;
  if (!isRecord(csp)) {
    throw new Error(`${configPath} has no app.security.csp object`);
  }
  return cspHeaderFromObject(csp);
}
