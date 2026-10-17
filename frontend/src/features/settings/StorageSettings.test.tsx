// @vitest-environment jsdom
import unavailable from "@contracts/public/errors/BRIDGE_UNAVAILABLE.json";
import storageFixture from "@contracts/public/storage/usage.json";
import { act, cleanup, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { createTransport } from "../../lib/backend/fixtureTransport";
import type { Transport } from "../../lib/backend/transport";
import { parseStorageUsage } from "../../lib/contracts/storage";
import { renderWithBackend, scriptedTransport } from "../../test/backendHarness";
import { SettingsView } from "./SettingsView";
import { StorageSettings } from "./StorageSettings";

afterEach(cleanup);

it("loads only when Storage is opened, clears one category and requires confirmation for all", async () => {
  const user = userEvent.setup();
  const transport = createTransport(null);
  const call = vi.spyOn(transport, "call");
  renderWithBackend(<SettingsView />, transport);
  expect(call).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "Storage" }));
  await screen.findByText("144 MiB");
  expect(screen.getByText("112 MiB can be cleared")).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "Clear all caches…" }));
  expect(call).not.toHaveBeenCalledWith("clear_cache", expect.anything());
  await user.click(screen.getByRole("button", { name: "Cancel" }));
  await user.click(screen.getByRole("button", { name: "Clear map images" }));
  await screen.findByText("12 MiB cleared.");
  expect(call).toHaveBeenCalledWith("clear_cache", { cache: "maps" });
  expect(screen.getByRole<HTMLButtonElement>("button", { name: "Clear map images" }).disabled).toBe(true);
  expect(screen.getByText("128 MiB")).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "Clear all caches…" }));
  await user.click(screen.getByRole("button", { name: "Clear now" }));
  await screen.findByText("100 MiB cleared.");
  expect(within(screen.getByRole("region", { name: "Cache usage" })).getByText("32 MiB")).toBeTruthy();
  expect(screen.getByText("32 MiB kept while in use or pinned")).toBeTruthy();
  expect(screen.getByRole<HTMLButtonElement>("button", { name: "Clear all caches…" }).disabled).toBe(true);
});

it("keeps every clear action disabled across navigation while cleanup is running", async () => {
  const user = userEvent.setup();
  const base = createTransport(null);
  let complete: (() => void) | undefined;
  const transport: Transport = { ...base, call: (command, args) => command !== "clear_cache" ? base.call(command, args)
    : new Promise((resolve) => { complete = () => { void base.call(command, args).then(resolve); }; }) };
  renderWithBackend(<SettingsView />, transport);
  await user.click(screen.getByRole("button", { name: "Storage" }));
  await screen.findByText("144 MiB");
  await user.click(screen.getByRole("button", { name: "Clear forecast downloads" }));
  expect(screen.getByRole<HTMLButtonElement>("button", { name: "Clear map images" }).disabled).toBe(true);
  await user.click(screen.getByRole("button", { name: "Appearance" }));
  await user.click(screen.getByRole("button", { name: "Storage" }));
  expect(screen.getByText("Clearing cached weather data…")).toBeTruthy();
  expect(screen.getByRole<HTMLButtonElement>("button", { name: "Clear forecast downloads" }).disabled).toBe(true);
  act(() => { complete?.(); });
  await screen.findByText("48 MiB");
  await waitFor(() => { expect(screen.getByRole<HTMLButtonElement>("button", { name: "Clear map images" }).disabled).toBe(false); });
});

it("shows an empty state and can retry after a usage failure", async () => {
  const user = userEvent.setup();
  const empty = { caches: storageFixture.caches.map((row) => ({ ...row, bytes: 0, files: 0, clearableBytes: 0 })) };
  const transport = scriptedTransport({ get_storage_usage: [{ reject: unavailable }, empty] });
  renderWithBackend(<StorageSettings />, transport);
  await screen.findByRole("alert");
  expect(screen.queryByText("0 B")).toBeNull();
  await user.click(screen.getByRole("button", { name: "Try again" }));
  await screen.findByText("No cached weather data on this device.");
  expect(screen.queryByRole("alert")).toBeNull();
  expect(screen.getByRole<HTMLButtonElement>("button", { name: "Clear all caches…" }).disabled).toBe(true);
  expect(transport.calls("get_storage_usage")).toBe(2);
});

it("reports partial cleanup without claiming that every file was removed", async () => {
  const user = userEvent.setup();
  const transport = scriptedTransport({ get_storage_usage: [storageFixture], clear_cache: [{ usage: storageFixture, freedBytes: 1024, failedFiles: 1 }] });
  renderWithBackend(<StorageSettings />, transport);
  await screen.findByText("144 MiB");
  await user.click(screen.getByRole("button", { name: "Clear map images" }));
  expect((await screen.findByRole("status")).textContent).toContain("Some files couldn’t be removed");
  expect(screen.getByRole<HTMLButtonElement>("button", { name: "Clear map images" }).disabled).toBe(false);
});

it("allows cleanup to be retried after a backend failure", async () => {
  const user = userEvent.setup();
  const transport = scriptedTransport({ get_storage_usage: [storageFixture], clear_cache: [
    { reject: unavailable }, { usage: storageFixture, freedBytes: 1024, failedFiles: 0 },
  ] });
  renderWithBackend(<StorageSettings />, transport);
  await screen.findByText("144 MiB");
  await user.click(screen.getByRole("button", { name: "Clear map images" }));
  await screen.findByRole("alert");
  expect(screen.getByRole<HTMLButtonElement>("button", { name: "Clear map images" }).disabled).toBe(false);
  await user.click(screen.getByRole("button", { name: "Clear map images" }));
  await screen.findByText("1 KiB cleared.");
  expect(screen.queryByRole("alert")).toBeNull();
});

it("rejects impossible totals or duplicate categories", () => {
  expect(() => parseStorageUsage({ caches: [storageFixture.caches[0], storageFixture.caches[0], storageFixture.caches[2]] })).toThrow();
  expect(() => parseStorageUsage({ caches: storageFixture.caches.map((row) => ({ ...row, clearableBytes: row.bytes + 1 })) })).toThrow();
  expect(() => parseStorageUsage({ caches: storageFixture.caches.map((row) => ({ ...row, bytes: -1 })) })).toThrow();
});
