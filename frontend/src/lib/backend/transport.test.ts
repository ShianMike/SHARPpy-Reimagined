import { expect, it, vi } from "vitest";
import { callWithSignal, type Transport } from "./transport";

it("passes cancellation ownership and aborts immediately without waiting for the provider", async () => {
  const job = new AbortController();
  const call = vi.fn<Transport["call"]>((command) => command === "cancel_request"
    ? Promise.resolve(null) : new Promise(() => undefined));
  const transport: Transport = { call, listen: () => Promise.resolve(() => undefined) };
  const pending = callWithSignal(transport, "get_weather_field", { product: "tmp-2m" }, job.signal);
  const failed = expect(pending).rejects.toMatchObject({ name: "AbortError" });
  const requestId = call.mock.calls[0]?.[1]?.requestId;
  expect(requestId).toMatch(/^[a-f\d]{32}$/);
  job.abort();
  await failed;
  expect(call).toHaveBeenLastCalledWith("cancel_request", { requestId });
  expect(call).toHaveBeenCalledTimes(2);
});
