// @vitest-environment jsdom
import catalog from "@contracts/public/map/catalog.json";
import models from "@contracts/public/soundings/models.catalog.json";
import { cleanup, fireEvent, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { createTransport } from "../../lib/backend/fixtureTransport";
import { parseMapCatalog } from "../../lib/contracts/map";
import { parseModelCatalog } from "../../lib/contracts/soundings";
import { renderWithBackend } from "../../test/backendHarness";
import { resolveForecast } from "../forecast/useForecast";
import { ForecastExportDialog } from "./ForecastExportDialog";

afterEach(cleanup);
it("offers hours, resolution and timing without pixel, loop or transition controls", () => {
  const inventory = parseMapCatalog(catalog), product = inventory.products[0];
  const context = resolveForecast(parseModelCatalog(models).models, inventory, { modelKey: "hrrr", runTime: null, forecastHour: 0 });
  if (context === null || product === undefined) throw new Error("Forecast fixture missing");
  const anchor = document.createElement("button"); document.body.append(anchor);
  const { view, queryClient } = renderWithBackend(<ForecastExportDialog anchor={anchor} context={context} product={product} panel={0} onClose={vi.fn()} />, createTransport());
  expect(screen.queryByRole("combobox", { name: "Map pixels" })).toBeNull();
  expect(screen.queryByRole("switch")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Clear" }));
  expect(screen.getByRole("button", { name: "Create GIF" }).hasAttribute("disabled")).toBe(true);
  fireEvent.click(screen.getByRole("combobox", { name: "GIF resolution" }));
  const menu = screen.getByRole("listbox", { name: "GIF resolution" });
  expect(menu.dataset.nested).toBe("true");
  expect(menu.style.getPropertyValue("--popover-depth")).toBe("1");
  fireEvent.click(screen.getByRole("option", { name: "1920 × 1080 · Full HD" }));
  expect(screen.getByRole("combobox", { name: "GIF resolution" }).textContent).toContain("1920 × 1080");
  expect(screen.getByRole("dialog", { name: "Export forecast GIF" })).toBeTruthy();
  fireEvent.click(screen.getByRole("combobox", { name: "Time per forecast hour" }));
  fireEvent.click(screen.getByRole("option", { name: "0.5 seconds" }));
  expect(screen.getByRole("combobox", { name: "Time per forecast hour" }).textContent).toContain("0.5 seconds");
  view.unmount(); queryClient.clear(); anchor.remove();
});
