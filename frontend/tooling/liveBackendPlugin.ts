// Vite owns the local Python process; its native proxy keeps fetches same-origin.
import { spawn, spawnSync } from "node:child_process";
import { existsSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { isAbsolute, resolve } from "node:path";
import type { Plugin, PreviewOptions, PreviewServer, ServerOptions, ViteDevServer } from "vite";

export function liveBackend(): Plugin {
  return {
    name: "sharppy-live-backend",
    apply: "serve",
    configureServer: (server) => connectBackend(server, server.config.server),
    configurePreviewServer: (server) => connectBackend(server, server.config.preview),
  };
}

async function connectBackend(server: ViteDevServer | PreviewServer, options: ServerOptions | PreviewOptions): Promise<void> {
  const repo = resolve(server.config.root, "..");
  const suffix = process.platform === "win32" ? "Scripts/python.exe" : "bin/python";
  const python = process.env.SHARPPY_PYTHON ?? [".gribenv", ".venv"]
    .map((venv) => resolve(repo, venv, suffix)).find(existsSync);
  if (python === undefined || !isAbsolute(python) || !existsSync(python)) {
    throw new Error("Set SHARPPY_PYTHON to the absolute path of the project's Python interpreter.");
  }
  const cacheDir = mkdtempSync(resolve(tmpdir(), "sharppy-browser-"));
  const child = spawn(python, ["-u", "-m", "sharpmod.bridge.browser", "--origin",
    `http://127.0.0.1:${String(options.port)}`],
  { cwd: repo, env: { ...process.env, SHARPMOD_BRIDGE_CACHE: cacheDir }, windowsHide: true, stdio: ["ignore", "pipe", "inherit"] });
  const clearSession = (): void => {
    try { rmSync(cacheDir, { recursive: true, force: true, maxRetries: 3 }); }
    catch (error) { server.config.logger.warn(`The browser session cache could not be removed: ${String(error)}`); }
  };
  child.once("exit", clearSession);
  child.once("error", clearSession);
  let stopped = false;
  const stop = (): void => {
    if (stopped || child.exitCode !== null || child.signalCode !== null) return;
    stopped = true;
    // Windows venv launchers own a second Python process, which also needs closing.
    if (process.platform === "win32" && child.pid !== undefined) {
      spawnSync("taskkill", ["/PID", String(child.pid), "/T", "/F"], { windowsHide: true, stdio: "ignore" });
      clearSession(); // Forced Windows shutdown bypasses Python's atexit handlers.
    } else {
      child.kill();
    }
  };
  process.once("exit", stop);
  server.httpServer?.once("close", () => {
    process.off("exit", stop);
    stop();
  });
  const target = await new Promise<string>((resolveReady, reject) => {
    const timeout = setTimeout(() => {
      stop();
      reject(new Error("The live Python backend did not start within 30 seconds."));
    }, 30_000);
    child.once("error", (error) => {
      clearTimeout(timeout);
      reject(error);
    });
    child.once("exit", (code) => {
      clearTimeout(timeout);
      reject(new Error(`The live Python backend exited (${String(code)}).`));
    });
    let output = "";
    child.stdout.on("data", (chunk: Buffer) => {
      output += chunk.toString();
      const port = /SHARPPY_BROWSER_READY ([0-9]{1,5})\r?\n/.exec(output)?.[1];
      if (port !== undefined) {
        clearTimeout(timeout);
        child.stdout.removeAllListeners("data");
        child.stdout.resume();
        resolveReady(`http://127.0.0.1:${port}`);
      }
    });
  });
  options.proxy ??= {};
  options.proxy["/__backend"] = { target, changeOrigin: true };
}
