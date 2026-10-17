// A popover anchored to an element (PLAN P11, DESIGN-frontend F8, DR6-5).
// - Portal at the end of <body>, at --z-popover, clamped inside the viewport.
// - Takes a role, a label, and an initial-focus rule. A dialog is non-modal.
// - Esc, or Tab past the last focusable element (Shift+Tab before the first),
//   closes it and returns focus to the anchor.
// - A pointer press outside it and its anchor closes it without moving focus.
// It starts a new glass context: it floats as its own surface.

import { useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent, type PointerEvent as ReactPointerEvent, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { Surface } from "../ui/Surface";
import { GlassContextBoundary } from "./GlassSurface";
import { cx } from "../lib/cx";
import styles from "./Popover.module.css";
import { useUiStore } from "../stores/uiStore";

export type PopoverCloseReason = "escape" | "tab" | "outside";

export interface PopoverProps {
  open: boolean;
  anchor: HTMLElement | null;
  onClose: (reason: PopoverCloseReason) => void;
  role: "dialog" | "listbox";
  /** Accessible name; or name it by an element with labelledBy. */
  label?: string;
  labelledBy?: string;
  /** "first": data-autofocus or the first control; "none": focus stays where it is. */
  initialFocus: "first" | "container" | "none";
  /** Which anchor edge the popover lines up with. */
  align?: "start" | "end";
  /** Right opens alongside its anchor and flips left when there is no room. */
  side?: "bottom" | "right";
  /** "anchor": match the trigger; other widths use tokens or the content's own width. */
  width?: "popover" | "search" | "content" | "anchor";
  id?: string;
  className?: string | undefined;
  testId?: string;
  /** Header marked data-popover-drag; a data-popover-move button supports keyboard movement. */
  movable?: boolean;
  children: ReactNode;
}

const FOCUSABLE =
  'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

function focusables(container: HTMLElement): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE)).filter((item) => item.tabIndex >= 0);
}

export function Popover({
  open,
  anchor,
  onClose,
  role,
  label,
  labelledBy,
  initialFocus,
  align = "start",
  side = "bottom",
  width = "content",
  id,
  className,
  testId,
  movable = false,
  children,
}: PopoverProps): ReactNode {
  const [present, setPresent] = useState(open);
  if (open && !present) setPresent(true);
  const ref = useRef<HTMLElement | null>(null);
  const moved = useRef<{ x: number; y: number } | null>(null);
  const drag = useRef<{ x: number; y: number; pointer: number } | null>(null);
  const placeRef = useRef<() => void>(() => undefined);
  const onCloseRef = useRef(onClose);
  useLayoutEffect(() => {
    onCloseRef.current = onClose;
  });

  useLayoutEffect(() => {
    if (open || !present) return;
    let closing = true;
    const element = ref.current;
    const animations = element !== null && typeof element.getAnimations === "function" ? element.getAnimations() : [];
    void Promise.allSettled(animations.map((animation) => animation.finished)).then(() => {
      if (closing) setPresent(false);
    });
    return () => { closing = false; };
  }, [open, present]);

  // Movable popups start centered; the CSS clamps every popup inside the viewport.
  useLayoutEffect(() => {
    const element = ref.current;
    if (!open || anchor === null || element === null) {
      return;
    }
    moved.current = movable && id !== undefined ? useUiStore.getState().popupPositions[id] ?? null : null;
    drag.current = null;
    const place = (): void => {
      const target = anchor.getBoundingClientRect();
      if (width === "anchor") element.style.setProperty("--popover-anchor-w", `${String(target.width)}px`);
      const box = element.getBoundingClientRect();
      const gap = parseFloat(getComputedStyle(element).getPropertyValue("--chrome-gap")) || 0;
      const clamp = (value: number, extent: number, size: number): number => Math.max(gap, Math.min(value, extent - size - gap));
      if (moved.current !== null) moved.current = { x: clamp(moved.current.x, window.innerWidth, box.width), y: clamp(moved.current.y, window.innerHeight, box.height) };
      const beside = target.right + gap + box.width > window.innerWidth - gap && target.left >= box.width + 2 * gap
        ? target.left - box.width - gap : target.right + gap;
      const x = moved.current?.x ?? (movable ? (window.innerWidth - box.width) / 2 : side === "right" ? beside : align === "end" ? target.right - box.width : target.left);
      const y = moved.current?.y ?? (movable ? (window.innerHeight - box.height) / 2 : side === "right" ? target.top : target.bottom);
      element.style.setProperty("--popover-x", `${String(x)}px`);
      element.style.setProperty("--popover-y", `${String(y)}px`);
      element.style.setProperty("--popover-gap", !movable && side === "bottom" ? "var(--space-1)" : `calc(var(--space-1) * 0)`);
      element.style.setProperty("--popover-measured-w", `${String(box.width)}px`);
      element.style.setProperty("--popover-measured-h", `${String(box.height)}px`);
    };
    placeRef.current = place;
    place();
    let frame: number | null = null;
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(() => {
      // Positioning can resize this popup; wait until resize delivery has finished.
      frame ??= requestAnimationFrame(() => { frame = null; place(); });
    });
    observer?.observe(element);
    observer?.observe(anchor);
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    return () => {
      observer?.disconnect();
      if (frame !== null) cancelAnimationFrame(frame);
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
    };
  }, [open, anchor, align, side, width, movable, id]);

  // Initial focus when it opens.
  useEffect(() => {
    const element = ref.current;
    if (!open || element === null || initialFocus === "none") {
      return;
    }
    const first = initialFocus === "first" ? element.querySelector<HTMLElement>("[data-autofocus]") ?? focusables(element)[0] : undefined;
    (first ?? element).focus();
  }, [open, initialFocus]);

  // A press outside closes it; focus stays wherever the press put it.
  useEffect(() => {
    if (!open) {
      return;
    }
    const onPointerDown = (event: PointerEvent): void => {
      const target = event.target;
      if (!(target instanceof Node)) {
        return;
      }
      if (ref.current?.contains(target) === true || anchor?.contains(target) === true) {
        return;
      }
      onCloseRef.current("outside");
    };
    document.addEventListener("pointerdown", onPointerDown, true);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown, true);
    };
  }, [open, anchor]);

  if (!open && !present) {
    return null;
  }

  const closeToAnchor = (event: KeyboardEvent, reason: PopoverCloseReason): void => {
    event.preventDefault();
    event.stopPropagation();
    anchor?.focus();
    onCloseRef.current(reason);
  };

  const onKeyDown = (event: KeyboardEvent<HTMLElement>): void => {
    if (!open) return;
    const element = ref.current;
    if (element === null) {
      return;
    }
    if (event.key === "Escape") {
      closeToAnchor(event, "escape");
      return;
    }
    if (movable && event.target instanceof HTMLElement && event.target.hasAttribute("data-popover-move")) {
      const step = parseFloat(getComputedStyle(element).getPropertyValue("--space-5")) || 1;
      const delta = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] }[event.key];
      if (delta !== undefined || event.key === "Home") {
        event.preventDefault();
        const box = element.getBoundingClientRect();
        moved.current = delta === undefined ? null : { x: box.left + (delta[0] ?? 0), y: box.top + (delta[1] ?? 0) };
        placeRef.current();
        if (id !== undefined) useUiStore.getState().setPopupPosition(id, moved.current);
        return;
      }
    }
    if (event.key !== "Tab") {
      return;
    }
    const items = focusables(element);
    const active = document.activeElement;
    const leavingForward = !event.shiftKey && (items.length === 0 || active === items[items.length - 1]);
    const leavingBack = event.shiftKey && (items.length === 0 || active === items[0] || active === element);
    if (leavingForward || leavingBack) {
      closeToAnchor(event, "tab");
    }
  };

  const onPointerDown = (event: ReactPointerEvent<HTMLElement>): void => {
    const target = event.target;
    if (!movable || event.button !== 0 || !(target instanceof Element) || target.closest("[data-popover-drag]") === null) return;
    const control = target.closest("button, input, select, a");
    if (control !== null && !control.hasAttribute("data-popover-move")) return;
    const box = event.currentTarget.getBoundingClientRect();
    drag.current = { x: event.clientX - box.left, y: event.clientY - box.top, pointer: event.pointerId };
    event.currentTarget.setPointerCapture(event.pointerId);
    event.currentTarget.dataset.dragging = "true";
  };
  const endDrag = (event: ReactPointerEvent<HTMLElement>): void => {
    if (drag.current === null) return;
    if (id !== undefined) useUiStore.getState().setPopupPosition(id, moved.current);
    drag.current = null;
    delete event.currentTarget.dataset.dragging;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
  };

  return createPortal(
    <GlassContextBoundary>
    <Surface
        ref={ref}
        id={id}
        elevation="popover"
        role={role}
        aria-label={label}
        aria-labelledby={labelledBy}
        aria-hidden={open ? undefined : true}
        inert={!open}
        data-open={open}
        tabIndex={initialFocus === "none" ? undefined : -1}
        className={cx(styles.popover, className)}
        data-width={width}
        data-testid={testId}
        onKeyDown={onKeyDown}
        data-movable={movable}
        onPointerDown={onPointerDown}
        onPointerMove={(event) => {
          if (drag.current === null || drag.current.pointer !== event.pointerId) return;
          moved.current = { x: event.clientX - drag.current.x, y: event.clientY - drag.current.y };
          placeRef.current();
        }}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
        onLostPointerCapture={(event) => { drag.current = null; delete event.currentTarget.dataset.dragging; }}
      >
        {children}
    </Surface>
    </GlassContextBoundary>,
    document.body,
  );
}
