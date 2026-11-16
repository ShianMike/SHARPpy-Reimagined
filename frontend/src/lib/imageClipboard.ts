/** Write the complete PNG to the browser or desktop webview clipboard. */
export async function copyPngToClipboard(image: Blob | (() => Promise<Blob>)): Promise<void> {
  if (typeof ClipboardItem === "undefined" || !("clipboard" in navigator) || typeof navigator.clipboard.write !== "function" ||
    (typeof ClipboardItem.supports === "function" && !ClipboardItem.supports("image/png"))) throw new Error("PNG clipboard unavailable");
  await navigator.clipboard.write([new ClipboardItem({ "image/png": typeof image === "function" ? image() : image })]);
}
