// Exercises the real Python lifecycle, including Windows venv launchers.
import { existsSync } from "node:fs";
import { resolve } from "node:path";
import { createServer, preview } from "vite";
import { expect, it, vi } from "vitest";
import { parseBackendHealth } from "../src/lib/contracts/health";
import { liveBackend } from "./liveBackendPlugin";

const root = resolve(import.meta.dirname, "..");
const suffix = process.platform === "win32" ? "Scripts/python.exe" : "bin/python";
const hasPython = process.env.SHARPPY_PYTHON !== undefined || [".gribenv", ".venv"]
  .some((venv) => existsSync(resolve(root, "..", venv, suffix)));

it.skipIf(!hasPython)("starts, restarts, and closes the actual Python process tree", async () => {
  const server = await createServer({
    configFile: false, root, cacheDir: resolve(root, "node_modules/.vite-live-backend-test"), plugins: [liveBackend()],
    server: { host: "127.0.0.1", port: 5175, strictPort: true },
  });
  const health = async () => {
    const response = await fetch("http://127.0.0.1:5175/__backend", {
      method: "POST", headers: { "Content-Type": "application/json", "Connection": "close" },
      body: JSON.stringify({ id: 1, method: "get_backend_health", params: {} }),
    });
    const raw: unknown = await response.json();
    const bridge = parseBackendHealth(raw).bridge;
    if (bridge.status !== "up") throw new Error("Python did not become ready.");
    return bridge.pid;
  };
  let backendUrl = "";
  try {
    await server.listen();
    const firstPid = await health();
    await server.restart();
    expect(await health()).not.toBe(firstPid);
    const proxy = server.config.server.proxy?.["/__backend"];
    if (typeof proxy !== "object" || typeof proxy.target !== "string") throw new Error("Missing backend proxy.");
    backendUrl = proxy.target;
  } finally {
    await server.close();
  }
  await vi.waitFor(async () => {
    await expect(fetch(`${backendUrl}/__backend`)).rejects.toThrow();
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
