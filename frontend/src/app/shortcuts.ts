// Global keyboard shortcuts (DESIGN-frontend F8 keyboard map).
// - Ctrl+K, or "/" outside text fields, focuses the station search.
// - Esc closes the innermost popover first: popovers and the search stop or
//   claim the event, so it is ignored here. Otherwise Esc clears the selection.

import { useEffect } from "react";
import { STATION_SEARCH_INPUT_ID, STATION_SEARCH_TRIGGER_ID } from "../features/search/StationSearch";
import { useAppStore } from "../stores/appStore";

export function isPlainKey(event: KeyboardEvent): boolean {
  return !event.altKey && !event.ctrlKey && !event.metaKey && !event.shiftKey;
}

/** True when typing into the target would insert text. */
export function isTextField(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) {
    return false;
  }
  if (target.isContentEditable || target instanceof HTMLTextAreaElement || target instanceof HTMLSelectElement) {
    return true;
  }
  if (target instanceof HTMLInputElement) {
    return !["button", "checkbox", "radio", "range", "reset", "submit"].includes(target.type);
  }
  return false;
}

function focusSearch(event: KeyboardEvent): void {
  const candidate = document.getElementById(STATION_SEARCH_INPUT_ID);
  const input = candidate?.closest("[inert], [hidden]") === null ? candidate : null;
  if (input instanceof HTMLInputElement && !input.disabled) {
    event.preventDefault();
    input.focus();
    input.select();
  } else if (input === null) {
    const trigger = document.getElementById(STATION_SEARCH_TRIGGER_ID);
    if (trigger instanceof HTMLButtonElement) {
      event.preventDefault();
      trigger.click();
    }
  }
}

export function handleGlobalKey(event: KeyboardEvent): void {
  if (event.defaultPrevented) {
    return;
  }
  const ctrlK = event.key.toLowerCase() === "k" && (event.ctrlKey || event.metaKey) && !event.altKey && !event.shiftKey;
  if (ctrlK || (event.key === "/" && isPlainKey(event) && !isTextField(event.target))) {
    focusSearch(event);
    return;
  }
  const { selectedStationId, selectedPoint } = useAppStore.getState();
  if (event.key === "Escape" && isPlainKey(event) && (selectedStationId !== null || selectedPoint !== null)) {
    event.preventDefault();
    useAppStore.getState().clearSelection();
  }
}

/** Installs the document-level shortcuts for the life of the shell. */
export function useGlobalShortcuts(): void {
  useEffect(() => {
    document.addEventListener("keydown", handleGlobalKey);
    return () => {
      document.removeEventListener("keydown", handleGlobalKey);
    };
  }, []);
}
