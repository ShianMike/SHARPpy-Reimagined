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
  const trigger = screen.getByRole("button", { name: "Forecast download details" });
  await user.click(trigger);
  const details = screen.getByRole("dialog", { name: "Forecast downloads" });
  expect(within(details).getByText("17 / 19")).toBeTruthy();
  expect(within(details).getByText("2 / 19")).toBeTruthy();
  expect(within(details).getByText("Failed: F003")).toBeTruthy();
  await user.click(within(details).getByRole("button", { name: "Check cache" }));
  expect(refresh).toHaveBeenCalledOnce();
  await user.click(within(details).getByRole("button", { name: "Retry missing hours" }));
  expect(toggle).toHaveBeenCalledOnce();
  await user.keyboard("{Escape}");
  expect(trigger.getAttribute("aria-expanded")).toBe("false");
  expect(document.activeElement).toBe(trigger);
});
