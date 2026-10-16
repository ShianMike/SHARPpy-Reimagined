// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState, type ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Popover, type PopoverCloseReason, type PopoverProps } from "./Popover";
import { useUiStore } from "../stores/uiStore";

beforeEach(() => { useUiStore.setState({ popupPosition: null }); });

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

interface HarnessProps {
  initialFocus: PopoverProps["initialFocus"];
  withButtons?: boolean;
  onClose?: (reason: PopoverCloseReason) => void;
  movable?: boolean;
  side?: PopoverProps["side"];
  width?: PopoverProps["width"];
}

/** A toolbar-like anchor button that toggles a non-modal dialog, plus an outside target. */
function Harness({ initialFocus, withButtons = true, onClose, movable = false, side = "bottom", width = "content" }: HarnessProps): ReactNode {
  const [anchor, setAnchor] = useState<HTMLButtonElement | null>(null);
  const [open, setOpen] = useState(false);
  return (
    <div>
      <button
        ref={setAnchor}
        type="button"
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => {
          setOpen((value) => !value);
        }}
      >
        Backend ready
      </button>
      <input aria-label="Elsewhere" />
      <Popover
        open={open}
        anchor={anchor}
        role="dialog"
        label="Backend status"
        initialFocus={initialFocus}
        movable={movable}
        side={side}
        width={width}
        onClose={(reason) => {
          onClose?.(reason);
          setOpen(false);
        }}
      >
        {movable ? <header data-popover-drag><button type="button" data-popover-move>Move popup</button><button type="button">Other header action</button></header> : null}
        <p>Python 3.11.14</p>
        {withButtons ? (
          <>
            <button type="button">Restart backend</button>
            <button type="button">Copy log path</button>
          </>
        ) : null}
      </Popover>
    </div>
  );
}

async function openPopover(props: HarnessProps) {
  const user = userEvent.setup();
  render(<Harness {...props} />);
  const anchor = screen.getByRole("button", { name: "Backend ready" });
  await user.click(anchor);
  return { user, anchor, dialog: screen.getByRole("dialog", { name: "Backend status" }) };
}

describe("Popover", () => {
  it("coalesces resize notifications after delivery and cancels positioning when closed", async () => {
    let resized = (): void => undefined;
    const disconnect = vi.fn();
    vi.stubGlobal("ResizeObserver", class {
      constructor(callback: () => void) { resized = callback; }
      observe() {}
      disconnect = disconnect;
    });
    const frames = new Map<number, FrameRequestCallback>();
    let next = 0;
    vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => { frames.set(++next, callback); return next; });
    vi.stubGlobal("cancelAnimationFrame", (id: number) => { frames.delete(id); });
    const { user, anchor, dialog } = await openPopover({ initialFocus: "first", width: "anchor" });
    vi.spyOn(anchor, "getBoundingClientRect").mockReturnValue(new DOMRect(100, 200, 244, 40));
    const before = dialog.style.getPropertyValue("--popover-anchor-w");
    resized(); resized(); resized();
    expect(dialog.style.getPropertyValue("--popover-anchor-w")).toBe(before);
    expect(frames.size).toBe(1);
    const pending = [...frames.values()]; frames.clear(); pending.forEach((callback) => { callback(0); });
    expect(dialog.style.getPropertyValue("--popover-anchor-w")).toBe("244px");
    resized();
    await user.keyboard("{Escape}");
    expect(disconnect).toHaveBeenCalledOnce();
    expect(frames.size).toBe(0);
  });

  it("opens beside the anchor and flips left only when the right side has no room", async () => {
    const { anchor, dialog } = await openPopover({ initialFocus: "none", side: "right" });
    dialog.style.setProperty("--chrome-gap", "8px");
    const rect = vi.spyOn(anchor, "getBoundingClientRect").mockReturnValue(new DOMRect(100, 200, 244, 40));
    vi.spyOn(dialog, "getBoundingClientRect").mockReturnValue(new DOMRect(0, 0, 320, 380));
    fireEvent.resize(window);
    expect(dialog.style.getPropertyValue("--popover-x")).toBe("352px");
    expect(dialog.style.getPropertyValue("--popover-y")).toBe("200px");
    expect(dialog.style.getPropertyValue("--popover-gap")).toBe("calc(var(--space-1) * 0)");
    rect.mockReturnValue(new DOMRect(window.innerWidth - 140, 200, 120, 40));
    fireEvent.resize(window);
    expect(dialog.style.getPropertyValue("--popover-x")).toBe(`${String(window.innerWidth - 468)}px`);
  });

  it("keeps dropdown width in sync with its trigger after resizing", async () => {
    const { anchor, dialog } = await openPopover({ initialFocus: "none", width: "anchor" });
    const rect = vi.spyOn(anchor, "getBoundingClientRect").mockReturnValue(new DOMRect(100, 200, 244, 40));
    fireEvent.resize(window);
    expect(dialog.getAttribute("data-width")).toBe("anchor");
    expect(dialog.style.getPropertyValue("--popover-anchor-w")).toBe("244px");
    rect.mockReturnValue(new DOMRect(100, 200, 180, 40));
    fireEvent.resize(window);
    expect(dialog.style.getPropertyValue("--popover-anchor-w")).toBe("180px");
  });

  it("renders at the end of body with its role and label, and closes when toggled", async () => {
    const { user, anchor, dialog } = await openPopover({ initialFocus: "first" });
    expect(document.body.lastElementChild).toBe(dialog);
    expect(dialog.getAttribute("aria-modal")).toBeNull();
    expect(anchor.getAttribute("aria-expanded")).toBe("true");
    expect(dialog.style.getPropertyValue("--popover-x")).not.toBe("");
    await user.click(anchor);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("moves focus to the first button on open", async () => {
    await openPopover({ initialFocus: "first" });
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Restart backend" }));
  });

  it("focuses the dialog itself when it has no focusable element", async () => {
    const { dialog } = await openPopover({ initialFocus: "first", withButtons: false });
    expect(document.activeElement).toBe(dialog);
    expect(dialog.tabIndex).toBe(-1);
  });

  it("leaves focus alone with initialFocus none", async () => {
    const { anchor } = await openPopover({ initialFocus: "none" });
    expect(document.activeElement).toBe(anchor);
  });

  it("closes on Esc and returns focus to the anchor", async () => {
    const onClose = vi.fn();
    const { user, anchor } = await openPopover({ initialFocus: "first", onClose });
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(anchor);
    expect(onClose).toHaveBeenCalledWith("escape");
  });

  it("closes on Tab past the last element and returns focus to the anchor", async () => {
    const onClose = vi.fn();
    const { user, anchor } = await openPopover({ initialFocus: "first", onClose });
    await user.tab();
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Copy log path" }));
    expect(screen.getByRole("dialog")).toBeTruthy();
    await user.tab();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(anchor);
    expect(onClose).toHaveBeenCalledWith("tab");
  });

  it("closes on Shift+Tab before the first element and returns focus to the anchor", async () => {
    const { user, anchor } = await openPopover({ initialFocus: "first" });
    await user.tab({ shift: true });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(anchor);
  });

  it("closes on an outside click without moving focus", async () => {
    const onClose = vi.fn();
    const { user, anchor } = await openPopover({ initialFocus: "first", onClose });
    const restart = screen.getByRole("button", { name: "Restart backend" });
    expect(document.activeElement).toBe(restart);
    // A press on a non-focusable area outside: the popover closes and focus is not sent to the anchor.
    await user.pointer({ keys: "[MouseLeft]", target: document.body });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(onClose).toHaveBeenCalledWith("outside");
    expect(document.activeElement).not.toBe(anchor);
  });

  it("does not treat a press inside the popover as outside", async () => {
    const onClose = vi.fn();
    const { user } = await openPopover({ initialFocus: "first", onClose });
    await user.click(screen.getByText("Python 3.11.14"));
    expect(screen.getByRole("dialog")).toBeTruthy();
    expect(onClose).not.toHaveBeenCalled();
  });

  it("centers movable popups as their size and viewport change", async () => {
    vi.stubGlobal("innerWidth", 1280);
    vi.stubGlobal("innerHeight", 800);
    const { dialog } = await openPopover({ initialFocus: "first", movable: true });
    const rect = vi.spyOn(dialog, "getBoundingClientRect").mockReturnValue(new DOMRect(0, 0, 320, 380));
    fireEvent.resize(window);
    expect(dialog.style.getPropertyValue("--popover-x")).toBe("480px");
    expect(dialog.style.getPropertyValue("--popover-y")).toBe("210px");
    expect(dialog.style.getPropertyValue("--popover-gap")).toBe("calc(var(--space-1) * 0)");
    vi.stubGlobal("innerWidth", 1920);
    vi.stubGlobal("innerHeight", 1080);
    rect.mockReturnValue(new DOMRect(0, 0, 500, 600));
    fireEvent.resize(window);
    expect(dialog.style.getPropertyValue("--popover-x")).toBe("710px");
    expect(dialog.style.getPropertyValue("--popover-y")).toBe("240px");
  });

  it("moves only from the header handle, stays within the viewport, and retains position after scroll", async () => {
    const { user, dialog, anchor } = await openPopover({ initialFocus: "first", movable: true });
    dialog.style.setProperty("--chrome-gap", "8px");
    dialog.style.setProperty("--space-5", "20px");
    vi.spyOn(anchor, "getBoundingClientRect").mockReturnValue(new DOMRect(400, 60, 100, 40));
    vi.spyOn(dialog, "getBoundingClientRect").mockImplementation(() => new DOMRect(
      Number.parseFloat(dialog.style.getPropertyValue("--popover-x")) || 400,
      Number.parseFloat(dialog.style.getPropertyValue("--popover-y")) || 100, 300, 200));
    dialog.setPointerCapture = vi.fn();
    dialog.hasPointerCapture = () => true;
    dialog.releasePointerCapture = vi.fn();
    fireEvent.resize(window);
    const centerX = (window.innerWidth - 300) / 2;
    const centerY = (window.innerHeight - 200) / 2;
    expect(dialog.style.getPropertyValue("--popover-x")).toBe(`${String(centerX)}px`);
    expect(dialog.style.getPropertyValue("--popover-y")).toBe(`${String(centerY)}px`);
    const handle = screen.getByRole("button", { name: "Move popup" });
    await user.pointer([{ keys: "[MouseLeft>]", target: handle, coords: { clientX: centerX + 20, clientY: centerY + 20 } }, { target: handle, coords: { clientX: 170, clientY: 250 } }, { keys: "[/MouseLeft]" }]);
    expect(dialog.style.getPropertyValue("--popover-x")).toBe("150px");
    expect(dialog.style.getPropertyValue("--popover-y")).toBe("230px");
    fireEvent.scroll(window);
    expect(dialog.style.getPropertyValue("--popover-x")).toBe("150px");
    await user.keyboard("{ArrowRight}");
    expect(dialog.style.getPropertyValue("--popover-x")).toBe("170px");
    await user.pointer([{ keys: "[MouseLeft>]", target: handle, coords: { clientX: 190, clientY: 250 } }, { target: handle, coords: { clientX: -900, clientY: -900 } }, { keys: "[/MouseLeft]" }]);
    expect(dialog.style.getPropertyValue("--popover-x")).toBe("8px");
    expect(dialog.style.getPropertyValue("--popover-y")).toBe("8px");
    await user.keyboard("{Home}");
    expect(dialog.style.getPropertyValue("--popover-x")).toBe(`${String(centerX)}px`);
    expect(dialog.style.getPropertyValue("--popover-y")).toBe(`${String(centerY)}px`);
    await user.pointer([{ keys: "[MouseLeft>]", target: screen.getByRole("button", { name: "Other header action" }), coords: { clientX: 600, clientY: 120 } }, { coords: { clientX: 50, clientY: 200 } }, { keys: "[/MouseLeft]" }]);
    expect(dialog.style.getPropertyValue("--popover-x")).toBe(`${String(centerX)}px`);
  });

  it("restores a moved popup after unmounting and shares the location with other movable pickers", async () => {
    useUiStore.getState().setPopupPosition({ x: 150, y: 230 });
    const { user, dialog } = await openPopover({ initialFocus: "first", movable: true });
    expect(dialog.style.getPropertyValue("--popover-x")).toBe("150px");
    expect(dialog.style.getPropertyValue("--popover-y")).toBe("230px");
    vi.spyOn(dialog, "getBoundingClientRect").mockReturnValue(new DOMRect(150, 230, 300, 200));
    await user.keyboard("{ArrowRight}");
    const movedX = dialog.style.getPropertyValue("--popover-x");
    cleanup();
    const reopened = await openPopover({ initialFocus: "first", movable: true });
    expect(reopened.dialog.style.getPropertyValue("--popover-x")).toBe(movedX);
    expect(reopened.dialog.style.getPropertyValue("--popover-y")).toBe("230px");
    await reopened.user.keyboard("{Home}");
    expect(useUiStore.getState().popupPosition).toBeNull();
  });
});
