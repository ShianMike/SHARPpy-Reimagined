// @vitest-environment jsdom
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import type { useFrameDownload } from "../../map/useFrameDownload";
import { ForecastDownloads } from "./ForecastDownloads";

afterEach(cleanup);
it("shows distinct disk and memory counts, missing hours, retry and keyboard dismissal", async () => {
  const user = userEvent.setup();
  const toggle = vi.fn(), refresh = vi.fn();
  const download: ReturnType<typeof useFrameDownload> = { toggle, refresh, running: false, complete: false,
    loaded: 17, total: 19, cached: 17, ready: 2, message: "17 saved; 2 in memory", started: true,
    failed: true, missing: [3, 5], failedHours: [3], cacheError: false, disabled: false };
  render(<ForecastDownloads download={download} />);
  const trigger = screen.getByRole("button", { name: "Forecast Hours" });
  await user.click(trigger);
  expect(toggle).not.toHaveBeenCalled();
  expect(trigger.getAttribute("aria-controls")).toBe(screen.getByRole("dialog").id);
  const details = screen.getByRole("dialog", { name: "Forecast Hours" });
  expect(within(details).getByText("Saved On Device").closest("dl")?.textContent).toContain("17 / 19");
  expect(within(details).getByText("2 / 19")).toBeTruthy();
  expect(within(details).getByText("Failed Hours").closest("details")?.open).toBe(true);
  expect(within(details).getByText("F003")).toBeTruthy();
  await user.click(within(details).getByRole("button", { name: "Check Cache" }));
  expect(refresh).toHaveBeenCalledOnce();
  await user.click(within(details).getByRole("button", { name: "Retry Missing Hours" }));
  expect(toggle).toHaveBeenCalledOnce();
  await user.keyboard("{Escape}");
  expect(trigger.getAttribute("aria-expanded")).toBe("false");
  expect(document.activeElement).toBe(trigger);
});

it("uses one disclosure for loading, stopping, completion and unavailable downloads", async () => {
  const user = userEvent.setup();
  const download: ReturnType<typeof useFrameDownload> = { toggle: vi.fn(), refresh: vi.fn(), running: false, complete: false,
    loaded: 0, total: 19, cached: 0, ready: 1, message: "0 of 19 hours saved", started: false,
    failed: false, missing: Array.from({ length: 19 }, (_, hour) => hour), failedHours: [], cacheError: false, disabled: false };
  const { rerender } = render(<ForecastDownloads download={download} />);
  expect(screen.getAllByRole("button")).toHaveLength(1);
  const trigger = screen.getByRole("button", { name: "Forecast Hours" });
  trigger.focus();
  await user.keyboard("{Enter}");
  expect(download.toggle).not.toHaveBeenCalled();
  const details = screen.getByRole("dialog", { name: "Forecast Hours" });
  const hours = within(details).getByText("Hours To Load").closest("details");
  expect(hours?.open).toBe(false);
  const summary = hours?.querySelector("summary");
  expect(summary).toBeTruthy();
  if (summary === undefined || summary === null) throw new Error("Missing hour disclosure");
  await user.click(summary);
  expect(hours?.open).toBe(true);
  expect(within(details).getByText("F018")).toBeTruthy();
  await user.click(within(details).getByRole("button", { name: "Load Hours" }));
  expect(download.toggle).toHaveBeenCalledOnce();

  rerender(<ForecastDownloads download={{ ...download, running: true, started: true, loaded: 3, cached: 2 }} />);
  expect(screen.getByRole("progressbar", { name: "Downloaded Forecast Hours" }).getAttribute("value")).toBe("3");
  expect(trigger.textContent).toContain("3/19");
  await user.click(within(details).getByRole("button", { name: "Stop Download" }));
  expect(download.toggle).toHaveBeenCalledTimes(2);

  rerender(<ForecastDownloads download={{ ...download, complete: true, started: true, cached: 19, missing: [] }} />);
  expect(within(details).getByText("All Hours Saved On This Device.")).toBeTruthy();
  expect(within(details).queryByRole("button", { name: "Load Hours" })).toBeNull();
  expect(screen.getByRole("progressbar", { name: "Saved Forecast Hours" }).getAttribute("value")).toBe("19");

  rerender(<ForecastDownloads download={{ ...download, cacheError: true }} />);
  expect(within(details).getByText("Unavailable")).toBeTruthy();
  expect(within(details).getByText(/Couldn’t Check Saved Hours/u)).toBeTruthy();
  expect(within(details).queryByText("Hours To Load")).toBeNull();
  expect(screen.queryByRole("progressbar")).toBeNull();
  expect(trigger.textContent).toContain("—/19");

  rerender(<ForecastDownloads download={{ ...download, disabled: true, total: 0, missing: [] }} />);
  expect(within(details).getByText(/Select A Forecast And Visible Map Layer/u)).toBeTruthy();
  expect(within(details).getByRole<HTMLButtonElement>("button", { name: "Load Hours" }).disabled).toBe(true);
  await user.click(within(details).getByRole("button", { name: "Close Forecast Hours" }));
  expect(trigger.getAttribute("aria-expanded")).toBe("false");
  expect(document.activeElement).toBe(trigger);
});
