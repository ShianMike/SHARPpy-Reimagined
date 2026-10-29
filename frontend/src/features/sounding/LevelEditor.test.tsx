// @vitest-environment jsdom
import viewExample from "@contracts/public/soundings/view.example.json";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, expect, it, vi } from "vitest";
import { DEFAULT_UNITS } from "../../lib/displayUnits";
import { useUiStore } from "../../stores/uiStore";
import type { ViewPoint } from "../../types/soundingView";
import { LevelEditor } from "./LevelEditor";

afterEach(() => { cleanup(); useUiStore.getState().setUnits(DEFAULT_UNITS); });

function Editor({ points = viewExample.points, initial = "tmpc:0", busy = false, onApply = () => undefined }: {
  points?: ViewPoint[]; initial?: string; busy?: boolean; onApply?: (point: ViewPoint, values: Record<string, number>) => void;
}) {
  const [selected, setSelected] = useState(initial);
  return <LevelEditor points={points} selected={selected} edited={new Set(["0"])} busy={busy} onSelect={setSelected} onApply={onApply} onDone={() => undefined} />;
}

it("lists each level once, preserves duplicate pressure levels and retains special controls", async () => {
  const first = viewExample.points[0];
  if (first === undefined) throw new Error("Missing surface fixture");
  const points = [...viewExample.points, { ...first, id: "tmpc:9999" }];
  render(<Editor points={points} />);
  await userEvent.click(screen.getByRole("combobox", { name: "Profile level" }));
  const menu = screen.getByRole("listbox", { name: "Profile level" });
  const ids = new Set(points.map((point) => point.id.split(":")[1]));
  expect(within(menu).getAllByRole("option")).toHaveLength(ids.size);
  expect(within(menu).getByRole("option", { name: "986 hPa · Edited" })).toBeTruthy();
  expect(within(menu).getByRole("option", { name: "986 hPa" })).toBeTruthy();
  const special = points.find((point) => point.id === "storm:-1");
  if (special === undefined) throw new Error("Missing storm-motion fixture");
  await userEvent.click(within(menu).getByRole("option", { name: special.label }));
  expect(screen.queryByRole("group", { name: "Edit variable" })).toBeNull();
  expect(screen.getByRole("form", { name: "Edit selected level" })).toBeTruthy();
});

it("keeps the selected variable when stepping through levels and applies to the exact source level", async () => {
  const apply = vi.fn<(point: ViewPoint, values: Record<string, number>) => void>();
  render(<Editor initial="wind:0" onApply={apply} />);
  expect(screen.getByRole<HTMLButtonElement>("button", { name: "Previous level" }).disabled).toBe(true);
  await userEvent.click(screen.getByRole("button", { name: "Next level" }));
  expect(screen.getByRole("combobox", { name: "Profile level" }).textContent).toBe("975 hPa");
  expect(within(screen.getByRole("group", { name: "Edit variable" })).getByRole("button", { name: "Wind" }).getAttribute("aria-pressed")).toBe("true");
  const input = screen.getByRole("spinbutton", { name: "U wind kt" });
  await userEvent.clear(input); await userEvent.type(input, "14");
  await userEvent.click(screen.getByRole("button", { name: "Apply changes" }));
  expect(apply.mock.calls[0]?.[0].id).toBe("wind:1");
  expect(apply.mock.calls[0]?.[1]).toMatchObject({ u: 14 });
  await userEvent.click(within(screen.getByRole("group", { name: "Edit variable" })).getByRole("button", { name: "Temperature" }));
  await userEvent.click(screen.getByRole("button", { name: "Previous level" }));
  expect(screen.getByRole("spinbutton", { name: "Temperature °C" })).toBeTruthy();
});

it("selects an available variable across missing fields and blocks navigation while busy", async () => {
  const points = viewExample.points.filter((point) => ["tmpc:0", "wind:0", "tmpc:1"].includes(point.id));
  const { rerender } = render(<Editor points={points} initial="wind:0" />);
  await userEvent.click(screen.getByRole("button", { name: "Next level" }));
  expect(screen.getByRole("spinbutton", { name: "Temperature °C" })).toBeTruthy();
  expect(screen.getByRole<HTMLButtonElement>("button", { name: "Next level" }).disabled).toBe(true);
  rerender(<Editor points={points} initial="wind:0" busy />);
  expect(screen.getByRole<HTMLButtonElement>("button", { name: "Previous level" }).disabled).toBe(true);
  expect(screen.getByRole<HTMLButtonElement>("combobox", { name: "Profile level" }).disabled).toBe(true);
  expect(screen.getByRole<HTMLButtonElement>("button", { name: "Apply changes" }).disabled).toBe(true);
});
