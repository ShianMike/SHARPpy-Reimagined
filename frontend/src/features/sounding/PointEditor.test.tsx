// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { DEFAULT_UNITS } from "../../lib/displayUnits";
import { useUiStore } from "../../stores/uiStore";
import type { ViewPoint } from "../../types/soundingView";
import { PointEditor } from "./PointEditor";

afterEach(() => { cleanup(); useUiStore.getState().setUnits(DEFAULT_UNITS); });

it("edits in selected units, validates the canonical range and keeps untouched precision", async () => {
  useUiStore.getState().setUnits({ temperature: "°F", wind: "m/s" });
  const onApply = vi.fn();
  const point: ViewPoint = { id: "wind:0", label: "Level", x: 0.5, y: 0.5, values: [
    { key: "temp", label: "Temperature", value: 21.123456, original: 20, unit: "°C", min: -150, max: 65 },
    { key: "u", label: "U wind", value: 350, original: 350, unit: "kt", min: -350, max: 350 },
  ] };
  render(<PointEditor point={point} busy={false} onApply={onApply} onClose={() => undefined} />);
  const user = userEvent.setup();
  const temperature = screen.getByRole<HTMLInputElement>("spinbutton", { name: "Temperature °F" });
  const wind = screen.getByRole<HTMLInputElement>("spinbutton", { name: "U wind m/s" });
  expect(temperature.value).toBe("70.02");
  expect(wind.value).toBe("180.06");
  expect(screen.getByLabelText("Temperature before").textContent).toBe("68 °F");
  expect(screen.getByLabelText("U wind before").textContent).toBe("180.06 m/s");
  expect(temperature.closest("label")?.getAttribute("data-edited")).toBe("true");
  expect(wind.closest("label")?.getAttribute("data-edited")).toBe("false");
  await user.click(screen.getByRole("button", { name: "Apply & recalculate" }));
  expect(onApply).toHaveBeenLastCalledWith({ temp: 21.123456, u: 350 });
  await user.clear(temperature);
  await user.type(temperature, "86");
  expect(screen.getByLabelText("Temperature before").textContent).toBe("68 °F");
  await user.click(screen.getByRole("button", { name: "Apply & recalculate" }));
  expect(onApply).toHaveBeenLastCalledWith({ temp: 30, u: 350 });
  await user.clear(temperature);
  await user.type(temperature, "150");
  expect(screen.getByRole<HTMLButtonElement>("button", { name: "Apply & recalculate" }).disabled).toBe(true);
  await user.clear(temperature);
  expect(screen.getByRole<HTMLButtonElement>("button", { name: "Apply & recalculate" }).disabled).toBe(true);
});

it("keeps an unavailable original value explicit", () => {
  render(<PointEditor point={{ id: "wind:0", label: "Wind", x: 0.5, y: 0.5,
    values: [{ key: "u", label: "U wind", value: 10, original: null, unit: "kt", min: -350, max: 350 }] }}
    busy={false} onApply={() => undefined} onClose={() => undefined} />);
  expect(screen.getByLabelText("U wind before").textContent).toBe("Unavailable");
});
