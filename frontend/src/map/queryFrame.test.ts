import { QueryClient, QueryObserver } from "@tanstack/react-query";
import { expect, it, vi } from "vitest";
import { queryFrame } from "./queryFrame";

it("cancels a preload only after its last imperative owner leaves", async () => {
  const client = new QueryClient();
  const first = new AbortController(), second = new AbortController();
  let signal: AbortSignal | undefined;
  const queryFn = vi.fn((context: { signal: AbortSignal }) => {
    signal = context.signal;
    return new Promise<number>(() => undefined);
  });
  const options = { queryKey: ["fields", "test"], queryFn, retry: false };
  const a = queryFrame(client, options, first.signal).catch((error: unknown) => error);
  const b = queryFrame(client, options, second.signal).catch((error: unknown) => error);
  first.abort();
  await a;
  expect(queryFn).toHaveBeenCalledTimes(1);
  expect(signal?.aborted).toBe(false);
  second.abort();
  await b;
  expect(signal?.aborted).toBe(true);
  client.clear();
});

it("keeps an imperative download alive after the last visible panel leaves", async () => {
  const client = new QueryClient();
  let signal: AbortSignal | undefined;
  let finish: (value: number) => void = () => undefined;
  const options = { queryKey: ["fields", "scrub"], retry: false, queryFn: (context: { signal: AbortSignal }) => {
    signal = context.signal;
    return new Promise<number>((resolve) => { finish = resolve; });
  } };
  const panel = new QueryObserver(client, options);
  const leave = panel.subscribe(() => undefined);
  const controller = new AbortController();
  const download = queryFrame(client, options, controller.signal).catch((error: unknown) => error);
  leave();
  expect(signal?.aborted).toBe(false);
  finish(42);
  expect(await download).toBe(42);
  client.clear();
});
