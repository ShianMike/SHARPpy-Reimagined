// @vitest-environment jsdom
import viewExample from "@contracts/public/soundings/view.example.json";
import summaryExample from "@contracts/public/soundings/summary.example.json";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { createTransport } from "../../lib/backend/fixtureTransport";
import { parseSoundingSummary } from "../../lib/contracts/soundings";
import { parseSoundingView } from "../../lib/contracts/soundingView";
import { useSoundingStore } from "../../stores/soundingStore";
import { renderWithBackend } from "../../test/backendHarness";
import { SoundingImage } from "./SoundingImage";
import { SoundingExport } from "./SoundingExport";
import { useSoundingView } from "./useSoundingView";

const summary = parseSoundingSummary(summaryExample);
const response = (hd = false) => ({ ...structuredClone(viewExample), width: hd ? 3260 : 4564, height: hd ? 2198 : 3077,
  image: btoa("\x89PNG\r\n\x1a\n".padEnd(hd ? 524_288 : 1_048_576, "\0")) });
beforeEach(() => {
  useSoundingStore.setState(useSoundingStore.getInitialState(), true);
  useSoundingStore.getState().add(summary);
  vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
  Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn((blob: Blob) => `blob:${String(blob.size)}`) });
  Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
});
afterEach(() => { cleanup(); useSoundingStore.setState(useSoundingStore.getInitialState(), true); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it("exports the current HD/UHD bytes, blocks stale previews, and reuses an unchanged render", async () => {
  const base = createTransport();
  const pending: ((value: ReturnType<typeof response>) => void)[] = [];
  const render = vi.fn(() => new Promise<ReturnType<typeof response>>((resolve) => { pending.push(resolve); }));
  const downloads: { href: string; filename: string }[] = [];
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) { downloads.push({ href: this.href, filename: this.download }); });
  const { view, queryClient } = renderWithBackend(<SoundingImage soundingId={summary.soundingId} choice="MU" title="Test sounding" />, {
    ...base, call: (command, args) => command === "render_sounding_view" ? render() : base.call(command, args),
  });
  const status = screen.getByRole("status", { name: "Sounding export status" });
  expect(status.textContent).toContain("Preparing…");
  expect(screen.getByRole("button", { name: "Export PNG" }).hasAttribute("disabled")).toBe(true);
  await waitFor(() => { expect(render).toHaveBeenCalledOnce(); });
  await act(async () => { pending[0]?.(response()); await Promise.resolve(); });
  await waitFor(() => { expect(status.textContent).toContain("Ready"); });
  expect(status.querySelector("svg")).toBeNull();
  expect(status.textContent).toContain("~1.00 MiB");
  expect(status.textContent).toContain("4564 × 3077");
  fireEvent.click(screen.getByRole("button", { name: "Export PNG" }));
  expect(downloads.at(-1)?.href).toBe("blob:1048576");
  expect(status.textContent).toContain("Exported");
  expect(screen.getAllByText("Exported")).toHaveLength(1);
  expect(downloads.at(-1)?.filename).toBe("Test-sounding-MU.png");
  expect(render).toHaveBeenCalledOnce();
  await userEvent.click(screen.getByRole("combobox", { name: "Image resolution" }));
  await userEvent.click(screen.getByRole("option", { name: "HD · lossless" }));
  await waitFor(() => { expect(render).toHaveBeenCalledTimes(2); });
  expect(screen.getByTestId("sounding-image")).toBeTruthy();
  expect(status.textContent).toContain("Preparing…");
  expect(status.textContent).toContain("—");
  expect(status.textContent).not.toContain("Ready");
  expect(screen.queryByText("Exported")).toBeNull();
  expect(screen.getByRole("button", { name: "Export PNG" }).hasAttribute("disabled")).toBe(true);
  expect(screen.getByRole("button", { name: "Copy sounding image" }).hasAttribute("disabled")).toBe(true);
  await act(async () => { pending[1]?.(response(true)); await Promise.resolve(); });
  await waitFor(() => { expect(status.textContent).toContain("Ready"); });
  expect(status.textContent).toContain("~0.50 MiB");
  fireEvent.click(screen.getByRole("button", { name: "Export PNG" }));
  expect(downloads.at(-1)?.href).toBe("blob:524288");
  await userEvent.click(screen.getByRole("combobox", { name: "Image resolution" }));
  await userEvent.click(screen.getByRole("option", { name: "UHD · lossless" }));
  await waitFor(() => { expect(status.textContent).toContain("~1.00 MiB"); });
  fireEvent.click(screen.getByRole("button", { name: "Export PNG" }));
  expect(downloads.at(-1)?.href).toBe("blob:1048576");
  expect(render).toHaveBeenCalledTimes(2);
  view.unmount(); queryClient.clear();
});

it("copies the prepared PNG once, disables concurrent exports, and keeps download recovery available", async () => {
  class TestClipboardItem { constructor(readonly data: Record<string, Blob>) {} }
  let finish: () => void = () => undefined;
  const pending = new Promise<void>((resolve) => { finish = resolve; });
  const write = vi.fn<(items: TestClipboardItem[]) => Promise<void>>(() => pending);
  vi.stubGlobal("navigator", { clipboard: { write } });
  vi.stubGlobal("ClipboardItem", TestClipboardItem);
  const image = parseSoundingView(response());
  render(<SoundingExport view={image} quality="uhd" onQualityChange={() => undefined} ready busy={false} filename="test.png" />);
  fireEvent.click(screen.getByRole("button", { name: "Copy sounding image" }));
  expect(write).toHaveBeenCalledOnce();
  expect(write.mock.calls[0]?.[0][0]?.data["image/png"]).toBe(image.image);
  expect(screen.getByRole("combobox", { name: "Image resolution" }).hasAttribute("disabled")).toBe(true);
  expect(screen.getByRole("button", { name: "Copy sounding image" }).hasAttribute("disabled")).toBe(true);
  expect(screen.getByRole("button", { name: "Export PNG" }).hasAttribute("disabled")).toBe(true);
  await act(async () => { finish(); await pending; });
  expect(screen.getByText("Copied")).toBeTruthy();
  expect(screen.getByRole("combobox", { name: "Image resolution" }).hasAttribute("disabled")).toBe(false);
  write.mockRejectedValueOnce(new Error("Clipboard denied"));
  fireEvent.click(screen.getByRole("button", { name: "Copy sounding image" }));
  await screen.findByRole("alert");
  expect(screen.getByRole("alert").textContent).toBe("Copy unavailable. Use Export PNG.");
  expect(screen.getByRole("button", { name: "Export PNG" }).hasAttribute("disabled")).toBe(false);
});

it("clears readiness after an edit fails and restores it after a cached undo", async () => {
  const base = createTransport();
  let calls = 0;
  vi.spyOn(console, "warn").mockImplementation(() => undefined);
  function Probe() {
    const { image, edit, request, ready, busy, state, update } = useSoundingView(summary.soundingId, "MU", "uhd");
    return <><SoundingExport view={image.data} quality="uhd" onQualityChange={() => undefined} ready={ready} busy={busy} filename="test.png" />
      <button onClick={() => { edit.mutate({ ...request, edits: [{ index: 0, values: { tmpc: 30 } }] }); }}>Apply edit</button>
      <button onClick={() => { update(state); }}>Undo failed edit</button></>;
  }
  const { view, queryClient } = renderWithBackend(<Probe />, { ...base, call: (command, args) => command !== "render_sounding_view" ? base.call(command, args)
    : ++calls === 1 ? Promise.resolve(response()) : Promise.reject(new Error("Render failed")) });
  const status = screen.getByRole("status", { name: "Sounding export status" });
  await waitFor(() => { expect(status.textContent).toContain("Ready"); });
  fireEvent.click(screen.getByRole("button", { name: "Apply edit" }));
  await waitFor(() => { expect(status.textContent).toContain("Unavailable"); });
  expect(screen.getByRole("button", { name: "Export PNG" }).hasAttribute("disabled")).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "Undo failed edit" }));
  await waitFor(() => { expect(status.textContent).toContain("Ready"); });
  expect(calls).toBe(2);
  view.unmount(); queryClient.clear();
});
