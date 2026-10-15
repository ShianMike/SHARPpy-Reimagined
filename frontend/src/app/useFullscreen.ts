import { useCallback, useEffect, useState } from "react";
import { recordLog } from "../stores/logStore";
import { isPlainKey } from "./shortcuts";

export function useFullscreen() {
  const [nativeWindow] = useState(() => "__TAURI_INTERNALS__" in window
    ? import("../lib/backend/tauriTransport").then(({ getCurrentWindow }) => getCurrentWindow())
    : null);
  const [fullscreen, setFullscreen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    const sync = (): void => {
      if (nativeWindow === null) setFullscreen(Boolean(document.fullscreenElement));
      else void nativeWindow.then((current) => current.isFullscreen()).then((value) => {
        if (active) setFullscreen(value);
      }).catch((reason: unknown) => {
        if (active) recordLog("error", "Window", "Could not read fullscreen state", String(reason));
      });
    };
    sync();
    window.addEventListener("resize", sync);
    document.addEventListener("fullscreenchange", sync);
    return () => {
      active = false;
      window.removeEventListener("resize", sync);
      document.removeEventListener("fullscreenchange", sync);
    };
  }, [nativeWindow]);

  const toggle = useCallback(async (): Promise<void> => {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      if (nativeWindow !== null) {
        const current = await nativeWindow;
        await current.setFullscreen(!await current.isFullscreen());
        setFullscreen(await current.isFullscreen());
      } else {
        if (document.fullscreenElement) await document.exitFullscreen();
        else await document.documentElement.requestFullscreen();
        setFullscreen(Boolean(document.fullscreenElement));
      }
    } catch (reason) {
      setError("Could not change fullscreen. Try again.");
      recordLog("error", "Window", "Could not change fullscreen", String(reason));
    } finally { setBusy(false); }
  }, [busy, nativeWindow]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent): void => {
      if (event.defaultPrevented || event.repeat || !isPlainKey(event)) return;
      if (event.key === "F11" || (event.key === "Escape" && fullscreen && nativeWindow !== null)) {
        event.preventDefault();
        void toggle();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("keydown", onKey); };
  }, [fullscreen, nativeWindow, toggle]);

  return { fullscreen, busy, error, toggle };
}
