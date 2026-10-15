// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { RunCalendar } from "./RunCalendar";
import { SourceDate } from "../sounding/SourceFields";

afterEach(cleanup);

it("restores the calendar's focused day when reopened before its closing animation ends", async () => {
  render(<SourceDate value="2026-10-13" max="2026-10-20" onChange={() => undefined} />);
  const user = userEvent.setup();
  const trigger = screen.getByRole("button", { name: "Date (UTC)" });
  await user.click(trigger);
  expect(document.activeElement?.getAttribute("aria-label")).toBe("Tuesday, October 13, 2026");
  await user.keyboard("{ArrowRight}");
  const dialog = screen.getByRole("dialog");
  let finish = (): void => undefined;
  const finished = new Promise<void>((resolve) => { finish = resolve; });
  Object.defineProperty(dialog, "getAnimations", { value: () => [{ finished }] });
  await user.click(screen.getByRole("button", { name: "Done" }));
  expect(document.activeElement).toBe(trigger);
  await user.click(trigger);
  expect(document.activeElement?.getAttribute("aria-label")).toBe("Wednesday, October 14, 2026");
  await act(async () => { finish(); await finished; });
  expect(screen.getByRole("dialog")).toBe(dialog);
});

it("keeps ERA5 dates inside the archive and labels the shared calendar correctly", async () => {
  render(<RunCalendar value="1939-12-31" min="1940-01-01" max="2026-10-13" label="Date (UTC)" closeLabel="Done" onChange={() => undefined} onClose={() => undefined} />);
  expect(screen.getByRole("region", { name: "Date (UTC)" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Done" })).toBeTruthy();
  expect(screen.getByRole<HTMLButtonElement>("button", { name: "Previous month" }).disabled).toBe(true);
  await userEvent.keyboard("{ArrowLeft}");
  expect(document.activeElement?.getAttribute("aria-label")).toBe("Monday, January 1, 1940");
  await userEvent.click(screen.getByRole("button", { name: "Choose month and year" }));
  fireEvent.change(screen.getByRole("spinbutton", { name: "Calendar year" }), { target: { value: "1939" } });
  expect(screen.getByRole<HTMLButtonElement>("button", { name: "Dec" }).disabled).toBe(true);
});

it("navigates leap days with the keyboard, clamps to the newest UTC day, and commits only on selection", async () => {
  const change = vi.fn();
  render(<RunCalendar value="2024-03-31" max="2024-04-01" onChange={change} onClose={() => undefined} />);
  const user = userEvent.setup();
  await user.keyboard("{PageUp}");
  expect(document.activeElement?.getAttribute("aria-label")).toBe("Thursday, February 29, 2024");
  await user.keyboard("{ArrowRight}{Home}");
  expect(document.activeElement?.getAttribute("aria-label")).toBe("Sunday, February 25, 2024");
  await user.keyboard("{End}");
  expect(document.activeElement?.getAttribute("aria-label")).toBe("Saturday, March 2, 2024");
  await user.keyboard("{PageDown}{ArrowRight}");
  expect(document.activeElement?.getAttribute("aria-label")).toBe("Monday, April 1, 2024");
  expect(change).not.toHaveBeenCalled();
  await user.keyboard("{Enter}");
  expect(change).toHaveBeenCalledWith("2024-04-01");
});

it("rejects invalid archive years and future months, and preserves padded UTC years", async () => {
  render(<RunCalendar value="2026-10-13" max="2026-10-13" onChange={() => undefined} onClose={() => undefined} />);
  await userEvent.click(screen.getByRole("button", { name: "Choose month and year" }));
  const year = screen.getByRole("spinbutton", { name: "Calendar year" });
  expect(screen.getByRole<HTMLButtonElement>("button", { name: "Nov" }).disabled).toBe(true);
  fireEvent.change(year, { target: { value: "0" } });
  expect(screen.getByRole<HTMLButtonElement>("button", { name: "Jan" }).disabled).toBe(true);
  fireEvent.change(year, { target: { value: "1" } });
  await userEvent.click(screen.getByRole("button", { name: "Jan" }));
  expect(screen.getByRole<HTMLButtonElement>("button", { name: "Previous month" }).disabled).toBe(true);
  await userEvent.keyboard("{ArrowLeft}");
  expect(document.activeElement?.textContent).toBe("1");
  await userEvent.click(screen.getByRole("button", { name: "Next month" }));
  expect(screen.getByRole("grid").getAttribute("aria-label")).toBe("February 0001");
});
