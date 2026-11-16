/** Copy the encoded animation itself; never substitute a still PNG. */
export async function copyForecastGif(blob: Blob): Promise<void> {
  if ("__TAURI_INTERNALS__" in window) {
    // Keep unusually large animations available for saving without copying
    // them into the native clipboard process.
    if (blob.size > 16 * 1024 * 1024) throw new Error("GIFs over 16 MiB are too large to copy safely. Save the GIF instead.");
    const bytes = new Uint8Array(await blob.arrayBuffer());
    const { copyForecastGif: copyNativeGif } = await import("../../lib/backend/tauriTransport");
    await copyNativeGif(bytes);
    return;
  }
  if (typeof ClipboardItem === "undefined" || !("clipboard" in navigator) || typeof navigator.clipboard.write !== "function" ||
    (typeof ClipboardItem.supports === "function" && !ClipboardItem.supports("image/gif"))) throw new Error("GIF clipboard unavailable");
  await navigator.clipboard.write([new ClipboardItem({ "image/gif": blob })]);
}
