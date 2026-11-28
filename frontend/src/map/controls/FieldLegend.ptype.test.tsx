// @vitest-environment jsdom
import catalog from "@contracts/public/map/catalog.json";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { parseMapCatalog } from "../../lib/contracts/map";
import { FieldLegend } from "./FieldLegend";

beforeEach(() => { vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null); });
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("shows the HRRR reflectivity type groups in dBZ with REFS icons when vertical", () => {
  const refdPtype = parseMapCatalog(catalog).products.find((item) => item.key === "refd-1km-ptype")?.palette;
  if (refdPtype === undefined) throw new Error("Missing refd-1km-ptype palette in catalog fixture");

  const view = render(<FieldLegend palette={refdPtype} />);
  const legend = screen.getByTestId("field-legend");
  expect(legend.getAttribute("data-ptype")).toBe("true");
  expect(legend.getAttribute("data-ptype-icons")).toBeNull();
  expect(screen.getAllByRole("img").map((ramp) => ramp.getAttribute("aria-label"))).toEqual(["Rain", "Snow", "Frzr", "Sleet"]);
  for (const label of ["Rain", "Snow", "Frzr", "Sleet"]) {
    expect(screen.getByRole("img", { name: label }).querySelectorAll("span[style]")).toHaveLength(17);
  }
  expect(within(screen.getByRole("list", { name: "Scale ticks, dBZ" })).getAllByRole("listitem").map((tick) => tick.textContent))
    .toEqual(["5", "10", "20", "30", "40", "50+"]);

  view.rerender(<FieldLegend palette={refdPtype} vertical />);
  expect(legend.getAttribute("data-ptype-icons")).toBe("true");
  expect(screen.queryByText("dBZ")).toBeNull();
  for (const label of ["Rain", "Snow", "Frzr", "Sleet"]) {
    const ramp = screen.getByRole("img", { name: label });
    expect(ramp.firstElementChild?.tagName).toBe("svg");
    expect(ramp.querySelector("[data-side='left']")?.textContent).toBe(label);
  }
  const slider = screen.getByRole("slider", { name: "Explore parameter scale" });
  expect(slider.getAttribute("aria-valuemin")).toBe("5");
  expect(slider.getAttribute("aria-valuemax")).toBe("50");
  expect(within(screen.getByRole("list", { name: "Scale ticks, dBZ" })).getAllByRole("listitem").map((tick) => tick.textContent))
    .toEqual(["50+", "40", "30", "20", "10", "5"]);
  expect(slider.getAttribute("aria-valuetext")).toBe("25 dBZ");
  fireEvent.keyDown(slider, { key: "End" });
  expect(slider.getAttribute("aria-valuetext")).toBe("50 dBZ or higher");
  fireEvent.keyDown(slider, { key: "Home" });
  expect(slider.getAttribute("aria-valuetext")).toBe("5 dBZ");
});
