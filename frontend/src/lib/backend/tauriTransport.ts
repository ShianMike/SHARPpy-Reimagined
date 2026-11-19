// The only module that imports @tauri-apps/api (boundary rule 3). Backend
// command rejections reach BackendClient as Tauri's raw values.

import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import type { Transport } from "./transport";
import { Update } from "@tauri-apps/plugin-updater";

export { getCurrentWindow } from "@tauri-apps/api/window";

export type DesktopPlatform = "windows" | "linux" | "macos" | "unknown";

export async function getDesktopPlatform(): Promise<DesktopPlatform> {
  const platform = await invoke<unknown>("check_for_updates", { action: "getPlatform" });
  return platform === "windows" || platform === "linux" || platform === "macos" ? platform : "unknown";
}

export function openUpdateReleases(): Promise<void> {
  return invoke("check_for_updates", { action: "openReleases" });
}

export async function checkForUpdates(options: { timeout?: number } = {}): Promise<Update | null> {
  const metadata = await invoke<ConstructorParameters<typeof Update>[0] | null>("check_for_updates", options);
  return metadata === null ? null : new Update(metadata);
}

/** Sends encoded GIF bytes as Tauri's binary request body. */
export function copyForecastGif(bytes: Uint8Array): Promise<void> {
  return invoke("copy_forecast_gif", bytes);
}

export function createTransport(): Transport {
  return {
    call: (command, args) => invoke(command, args === undefined ? undefined : { ...args }),
    listen: (event, handler) =>
      listen(event, (message) => {
        handler(message.payload);
      }),
  };
}
