// Exercises the real Python lifecycle, including Windows venv launchers.
import { existsSync, readdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { resolve } from "node:path";
import { createServer, preview } from "vite";
import { expect, it, vi } from "vitest";
import { parseBackendHealth } from "../src/lib/contracts/health";
import { liveBackend } from "./liveBackendPlugin";

const root = resolve(import.meta.dirname, "..");
const suffix = process.platform === "win32" ? "Scripts/python.exe" : "bin/python";
const hasPython = process.env.SHARPPY_PYTHON !== undefined || [".gribenv", ".venv"]
  .some((venv) => existsSync(resolve(root, "..", venv, suffix)));
if (process.env.CI === "true" && !hasPython) throw new Error("CI must provide SHARPPY_PYTHON; live boundary tests cannot be skipped.");

it.skipIf(!hasPython)("starts, restarts, and closes the actual Python process tree", async () => {
  const before = new Set(readdirSync(tmpdir()).filter((name) => name.startsWith("sharppy-browser-")));
  const server = await createServer({
    configFile: false, root, cacheDir: resolve(root, "node_modules/.vite-live-backend-test"), plugins: [liveBackend()],
    server: { host: "127.0.0.1", port: 0, strictPort: true },
  });
  const health = async () => {
    const base = server.resolvedUrls?.local[0];
    if (base === undefined) throw new Error("Missing test server URL.");
    const response = await fetch(new URL("/__backend", base), {
      method: "POST", headers: { "Content-Type": "application/json", "Connection": "close" },
      body: JSON.stringify({ id: 1, method: "get_backend_health", params: {} }),
    });
    const raw: unknown = await response.json();
    const bridge = parseBackendHealth(raw).bridge;
    if (bridge.status !== "up") throw new Error("Python did not become ready.");
    return bridge.pid;
  };
  let backendUrl = "";
  let sessionDir = "";
  try {
    await server.listen();
    const firstPid = await health();
    await server.restart();
    expect(await health()).not.toBe(firstPid);
    const proxy = server.config.server.proxy?.["/__backend"];
    if (typeof proxy !== "object" || typeof proxy.target !== "string") throw new Error("Missing backend proxy.");
    backendUrl = proxy.target;
    const response = await fetch(`${backendUrl}/__backend`, {
      method: "POST", headers: { "Content-Type": "application/json", "Connection": "close", "Origin": "http://127.0.0.1:0" },
      body: JSON.stringify({ id: 2, method: "get_storage_usage", params: {} }),
    });
    expect(response.ok).toBe(true);
    const sessions = readdirSync(tmpdir()).filter((name) => name.startsWith("sharppy-browser-") && !before.has(name));
    expect(sessions).toHaveLength(1);
    sessionDir = resolve(tmpdir(), sessions[0] ?? "missing");
    expect(readdirSync(sessionDir).some((name) => name.startsWith("sharpmod-bridge-"))).toBe(true);
  } finally {
    await server.close();
  }
  await vi.waitFor(async () => {
    await expect(fetch(`${backendUrl}/__backend`)).rejects.toThrow();
    expect(existsSync(sessionDir)).toBe(false);
  }, { timeout: 2000 });
}, 45_000);

it.skipIf(!hasPython)("serves live data from the browser build preview and closes Python with it", async () => {
  const server = await preview({
    configFile: false, root, plugins: [liveBackend()],
    preview: { host: "127.0.0.1", port: 4175, strictPort: true },
  });
  let backendUrl = "";
  try {
    const response = await fetch("http://127.0.0.1:4175/__backend", {
      method: "POST", headers: { "Content-Type": "application/json", "Connection": "close", "Origin": "http://127.0.0.1:4175" },
      body: JSON.stringify({ id: 1, method: "get_backend_health", params: {} }),
    });
    expect(response.ok).toBe(true);
    const raw: unknown = await response.json();
    expect(parseBackendHealth(raw).bridge.status).toBe("up");
    const proxy = server.config.preview.proxy?.["/__backend"];
    if (typeof proxy !== "object" || typeof proxy.target !== "string") throw new Error("Missing backend proxy.");
    backendUrl = proxy.target;
  } finally {
    await server.close();
  }
  await vi.waitFor(async () => {
    await expect(fetch(`${backendUrl}/__backend`)).rejects.toThrow();
  }, { timeout: 2000 });
}, 45_000);
