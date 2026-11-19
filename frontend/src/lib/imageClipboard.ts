import { useAppStore } from "../stores/appStore";

export function canWriteClipboardImage(type: "image/png" | "image/gif"): boolean {
  const clipboard = navigator.clipboard as Clipboard | undefined;
  return typeof ClipboardItem !== "undefined" && typeof clipboard?.write === "function" &&
    (typeof ClipboardItem.supports !== "function" || ClipboardItem.supports(type));
}

export function useCanCopyPng(): boolean {
  const platform = useAppStore((state) => state.platform);
  return platform === "windows" || platform === "browser" || canWriteClipboardImage("image/png");
}

/** Write the complete PNG to the browser or desktop webview clipboard. */
export async function copyPngToClipboard(image: Blob | (() => Promise<Blob>)): Promise<void> {
  if (!canWriteClipboardImage("image/png")) throw new Error("PNG clipboard unavailable");
  await navigator.clipboard.write([new ClipboardItem({ "image/png": typeof image === "function" ? image() : image })]);
}
