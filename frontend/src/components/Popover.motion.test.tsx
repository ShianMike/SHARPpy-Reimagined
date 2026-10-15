// @vitest-environment jsdom
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { Popover } from "./Popover";

afterEach(cleanup);

function content(open: boolean) {
  return <Popover open={open} anchor={null} role="dialog" label="Motion check" initialFocus="first" testId="moving-popover" onClose={() => undefined}>
    <input aria-label="Choice" defaultValue="Kept while closing" />
  </Popover>;
}

function pendingExit(element: HTMLElement) {
  let finish = (): void => undefined;
  let cancel = (): void => undefined;
  const finished = new Promise<void>((resolve, reject) => {
    finish = resolve; cancel = () => { reject(new Error("Transition cancelled")); };
  });
  Object.defineProperty(element, "getAnimations", { value: () => [{ finished }], configurable: true });
  return { finish, cancel };
}

it("keeps the closing surface until CSS finishes, immediately removing it from interaction", async () => {
  const view = render(content(true));
  const dialog = screen.getByRole("dialog");
  const exit = pendingExit(dialog);
  view.rerender(content(false));
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(dialog.hasAttribute("inert")).toBe(true);
  expect(dialog.dataset.open).toBe("false");
  expect(screen.getByTestId("moving-popover")).toBe(dialog);
  await act(async () => { exit.finish(); await Promise.resolve(); });
  expect(screen.queryByTestId("moving-popover")).toBeNull();
});

it("ignores an interrupted close when the same menu reopens and preserves its content and focus", async () => {
  const view = render(content(true));
  const dialog = screen.getByRole("dialog");
  const exit = pendingExit(dialog);
  view.rerender(content(false));
  view.rerender(content(true));
  await act(async () => { exit.cancel(); await Promise.resolve(); });
  expect(screen.getByRole("dialog")).toBe(dialog);
  expect(dialog.hasAttribute("inert")).toBe(false);
  expect(screen.getByRole<HTMLInputElement>("textbox").value).toBe("Kept while closing");
  expect(document.activeElement).toBe(screen.getByRole("textbox"));
});

it("closes without a timer when reduced motion or the platform supplies no transition", async () => {
  const view = render(content(true));
  const dialog = screen.getByRole("dialog");
  Object.defineProperty(dialog, "getAnimations", { value: () => [] });
  await act(async () => { view.rerender(content(false)); await Promise.resolve(); });
  expect(screen.queryByTestId("moving-popover")).toBeNull();
});

it("cleans up a cancelled exit and safely ignores completion after unmount", async () => {
  const view = render(content(true));
  const exit = pendingExit(screen.getByRole("dialog"));
  view.rerender(content(false));
  await act(async () => { exit.cancel(); await Promise.resolve(); });
  expect(screen.queryByTestId("moving-popover")).toBeNull();
  view.rerender(content(true));
  const next = pendingExit(screen.getByRole("dialog"));
  view.rerender(content(false));
  view.unmount();
  await act(async () => { next.finish(); await Promise.resolve(); });
  expect(screen.queryByTestId("moving-popover")).toBeNull();
});
