/// <reference types="vitest/config" />
import { fileURLToPath } from "node:url";
import { resolve } from "node:path";
import { availableParallelism } from "node:os";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import { sharppyBasemap } from "./tooling/basemapPlugin.ts";
import { cspHeaderFromTauriConfig } from "./tooling/csp.ts";
import { liveBackend } from "./tooling/liveBackendPlugin.ts";

const frontendRoot = fileURLToPath(new URL(".", import.meta.url));
const contractsDir = resolve(frontendRoot, "..", "src-tauri", "contracts");
const tauriConfig = resolve(frontendRoot, "..", "src-tauri", "tauri.conf.json");

export default defineConfig(({ command, isPreview, mode }) => ({
  plugins: [react(), sharppyBasemap(), ...(mode === "live" ? [liveBackend()] : [])],
  // Keep Rust output visible under `tauri dev`.
  clearScreen: false,
  resolve: {
    alias: { "@contracts": contractsDir },
  },
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    fs: { allow: [frontendRoot, contractsDir] },
  },
  preview: {
    host: "127.0.0.1",
    port: 4173,
    strictPort: true,
    // Read only when previewing, so dev and build never depend on it.
    headers:
      command === "serve" && isPreview === true
        ? { "Content-Security-Policy": cspHeaderFromTauriConfig(tauriConfig) }
        : {},
  },
  build: {
    target: "es2022",
    outDir: "dist",
  },
  worker: { format: "es" },
  test: {
    environment: "node",
    pool: "threads",
    // shortcut: cap at eight workers measured locally; retune for larger runners.
    maxWorkers: Math.min(8, availableParallelism()),
    include: ["src/**/*.test.{ts,tsx}", "tooling/**/*.test.ts"],
    restoreMocks: true,
    // Vitest replaces CSS with an empty module unless a file is included here;
    // canvasTokens.test.ts and the layer tests read the real tokens.css (?raw).
    css: { include: [/[\\/]src[\\/]styles[\\/]tokens\.css/] },
  },
}));
