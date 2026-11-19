import { canWriteClipboardImage } from "../../lib/imageClipboard";
import { useAppStore } from "../../stores/appStore";

export function useCanCopyGif(): boolean {
  return useAppStore((state) => state.platform === "windows" || state.platform === "browser");
}

/** Copy the encoded animation itself; never substitute a still PNG. */
export async function copyForecastGif(blob: Blob): Promise<void> {
  const platform = useAppStore.getState().platform;
  if (platform === "windows") {
    // Keep unusually large animations available for saving without copying
    // them into the native clipboard process.
    if (blob.size > 16 * 1024 * 1024) throw new Error("GIFs over 16 MiB are too large to copy safely. Save the GIF instead.");
    const bytes = new Uint8Array(await blob.arrayBuffer());
    const { copyForecastGif: copyNativeGif } = await import("../../lib/backend/tauriTransport");
    await copyNativeGif(bytes);
    return;
  }
  if (platform !== "browser" || !canWriteClipboardImage("image/gif")) throw new Error("GIF clipboard unavailable");
  await navigator.clipboard.write([new ClipboardItem({ "image/gif": blob })]);
}
