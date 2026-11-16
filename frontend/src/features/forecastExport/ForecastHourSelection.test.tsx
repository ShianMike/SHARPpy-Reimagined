// @vitest-environment jsdom
import { useState } from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it } from "vitest";
import { ForecastHourSelection } from "./ForecastHourSelection";

afterEach(cleanup);
const hours = Array.from({ length: 19 }, (_, hour) => hour);
const checkbox = (hour: number) => screen.getByRole<HTMLInputElement>("checkbox", { name: `Export forecast hour ${String(hour)}` });
const selected = () => screen.getAllByRole<HTMLInputElement>("checkbox").filter(input => input.checked)
  .map(input => Number(input.getAttribute("aria-label")?.split(" ").at(-1)));
function Selection({ offered = hours, current = 5, initial = [0, 1, 2], disabled = false }: {
  offered?: number[]; current?: number; initial?: number[]; disabled?: boolean;
}) {
  const [selection, setSelection] = useState(initial);
  return <ForecastHourSelection hours={offered} currentHour={current} selected={selection} disabled={disabled} onChange={setSelection} />;
}

it("selects time ranges from the current hour and thins the selection by actual three-hour intervals", () => {
  render(<Selection />);
  fireEvent.click(screen.getByRole("button", { name: "Next 6 hours" }));
  expect(selected()).toEqual([5, 6, 7, 8, 9, 10, 11]);
  fireEvent.click(screen.getByRole("button", { name: "Every 3 hours" }));
  expect(selected()).toEqual([5, 8, 11]);
  fireEvent.click(screen.getByRole("button", { name: "Next 12 hours" }));
  expect(selected()).toEqual([5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17]);
  fireEvent.click(screen.getByRole("button", { name: "Every 3 hours" }));
  expect(selected()).toEqual([5, 8, 11, 14, 17]);
  expect(screen.getByRole("group", { name: "Hours to export" }).getAttribute("aria-describedby")).toBeTruthy();
});

it("keeps sparse hours and the end of the forecast within the offered catalogue", () => {
  render(<Selection offered={[0, 3, 6, 9, 12]} current={8} initial={[]} />);
  fireEvent.click(screen.getByRole("button", { name: "Next 6 hours" }));
  expect(selected()).toEqual([9, 12]);
  fireEvent.click(screen.getByRole("button", { name: "Next 12 hours" }));
  expect(selected()).toEqual([9, 12]);
  fireEvent.click(screen.getByRole("button", { name: "Select all" }));
  fireEvent.click(screen.getByRole("button", { name: "Every 3 hours" }));
  expect(selected()).toEqual([0, 3, 6, 9, 12]);
  fireEvent.click(screen.getByRole("button", { name: "Clear" }));
  expect(selected()).toEqual([]);
  expect(screen.getByRole<HTMLButtonElement>("button", { name: "Every 3 hours" }).disabled).toBe(true);
});

it("Shift-clicks forward and backward to select or clear a range while preserving other selected hours", () => {
  render(<Selection initial={[0, 18]} />);
  fireEvent.click(checkbox(2));
  fireEvent.click(checkbox(6), { shiftKey: true });
  expect(selected()).toEqual([0, 2, 3, 4, 5, 6, 18]);
  fireEvent.click(checkbox(9));
  fireEvent.click(checkbox(4), { shiftKey: true });
  expect(selected()).toEqual([0, 2, 3, 18]);
  fireEvent.click(checkbox(1), { shiftKey: true });
  expect(selected()).toEqual([0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 18]);
});

it("resets the range anchor after shortcuts and never fills unavailable forecast hours", () => {
  render(<Selection offered={[0, 2, 5, 8, 11]} initial={[]} />);
  fireEvent.click(checkbox(2));
  fireEvent.click(checkbox(11), { shiftKey: true });
  expect(selected()).toEqual([2, 5, 8, 11]);
  fireEvent.click(screen.getByRole("button", { name: "Clear" }));
  fireEvent.click(checkbox(8), { shiftKey: true });
  expect(selected()).toEqual([8]);
});

it("disables shortcuts and hour inputs while an export is running", () => {
  render(<Selection disabled />);
  for (const input of screen.getAllByRole("checkbox")) expect(input.matches(":disabled")).toBe(true);
  for (const button of screen.getAllByRole("button")) expect(button.matches(":disabled")).toBe(true);
});

it("keeps native Space and Shift-Space checkbox interaction available from the keyboard", async () => {
  const user = userEvent.setup();
  render(<Selection initial={[]} />);
  checkbox(2).focus();
  await user.keyboard(" ");
  checkbox(5).focus();
  await user.keyboard("{Shift>} {/Shift}");
  expect(selected()).toEqual([2, 3, 4, 5]);
});
