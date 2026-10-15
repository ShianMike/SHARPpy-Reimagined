// @vitest-environment jsdom
import { cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it } from "vitest";
import { createTransport } from "../../lib/backend/fixtureTransport";
import { renderWithBackend } from "../../test/backendHarness";
import { useSoundingStore } from "../../stores/soundingStore";
import { FileForm } from "./FileForm";

afterEach(cleanup);

it("inspects a raw WRF path and opens the chosen time and point without uploading bytes", async () => {
  const base = createTransport();
  const requests: unknown[] = [];
  const inspections: unknown[] = [];
  renderWithBackend(<FileForm />, { ...base, call: (command, args) => {
    if (command === "open_sounding") requests.push(args);
    if (command === "inspect_wrf") inspections.push(args);
    return base.call(command, args);
  } });
  await userEvent.click(screen.getByRole("combobox", { name: "Import format" }));
  await userEvent.click(screen.getByRole("option", { name: "Raw WRF dataset" }));
  await userEvent.type(screen.getByRole("textbox", { name: "WRF file path" }), "C:\\data\\wrfout_d01.nc");
  await userEvent.click(screen.getByRole("button", { name: "Read WRF file" }));
  await screen.findByRole("combobox", { name: "Forecast time (UTC)" });
  expect(inspections).toEqual([{ filePath: "C:\\data\\wrfout_d01.nc" }]);
  await userEvent.click(screen.getByRole("combobox", { name: "Forecast time (UTC)" }));
  await userEvent.keyboard("{End}{Enter}");
  const lat = screen.getByRole("textbox", { name: "Latitude, degrees north" });
  await userEvent.clear(lat);
  await userEvent.type(lat, "35.5");
  await userEvent.click(screen.getByRole("button", { name: "Open WRF sounding" }));
  await waitFor(() => { expect(requests).toEqual([{ request: {
    kind: "wrf", filePath: "C:\\data\\wrfout_d01.nc", time: "2026-06-25T07:00:00Z", latitude: 35.5, longitude: -97,
  } }]); });
  await waitFor(() => { expect(useSoundingStore.getState().activeId).not.toBeNull(); });
});

it("clears inspected metadata when the dataset path changes", async () => {
  renderWithBackend(<FileForm />, createTransport());
  await userEvent.click(screen.getByRole("combobox", { name: "Import format" }));
  await userEvent.click(screen.getByRole("option", { name: "Raw WRF dataset" }));
  const path = screen.getByRole("textbox", { name: "WRF file path" });
  await userEvent.type(path, "C:\\data\\wrfout_d01.nc");
  await userEvent.click(screen.getByRole("button", { name: "Read WRF file" }));
  await screen.findByRole("button", { name: "Open WRF sounding" });
  await userEvent.type(path, "2");
  expect(screen.queryByRole("button", { name: "Open WRF sounding" })).toBeNull();
});
