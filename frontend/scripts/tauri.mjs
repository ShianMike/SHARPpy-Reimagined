// Runs the frontend-local Tauri CLI with both project directories made explicit.
//
// `npm run tauri -- <args>` runs this script from frontend/. The Tauri CLI would
// otherwise search for src-tauri/ below its working directory and never find the
// sibling ../src-tauri, so the CLI starts from the repository root with
// TAURI_APP_PATH and TAURI_FRONTEND_PATH set. No shell is involved: the CLI's own
// entry point runs under the current Node executable, and its exit code is ours.
import { spawn } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const frontendDir = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const repoRoot = resolve(frontendDir, "..");
const cli = resolve(frontendDir, "node_modules", "@tauri-apps", "cli", "tauri.js");

const child = spawn(process.execPath, [cli, ...process.argv.slice(2)], {
  cwd: repoRoot,
  env: {
    ...process.env,
    TAURI_APP_PATH: resolve(repoRoot, "src-tauri"),
    TAURI_FRONTEND_PATH: frontendDir,
  },
  stdio: "inherit",
  shell: false,
});

child.on("error", (error) => {
  console.error(`Could not start the Tauri CLI (${cli}): ${error.message}`);
  process.exit(1);
});

child.on("exit", (code, signal) => {
  process.exit(code ?? (signal ? 1 : 0));
});
