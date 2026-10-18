import { QueryObserver, type QueryClient, type QueryExecuteOptions } from "@tanstack/react-query";

/** Stop an imperative preload only when no other preload or visible panel owns it. */
export async function queryFrame<T>(client: QueryClient, options: QueryExecuteOptions<T>, signal?: AbortSignal): Promise<T> {
  signal?.throwIfAborted();
  // A real observer also protects this owner when the last visible panel leaves.
  const observer = new QueryObserver<T>(client, { ...options, enabled: false });
  const release = observer.subscribe(() => undefined);
  let abort = (): void => undefined;
  const stopped = new Promise<never>((_resolve, reject) => {
    abort = () => {
      release();
      reject(signal?.reason instanceof Error ? signal.reason : new DOMException("Preload cancelled", "AbortError"));
    };
    signal?.addEventListener("abort", abort, { once: true });
  });
  try { return await Promise.race([client.query(options), stopped]); }
  finally { signal?.removeEventListener("abort", abort); release(); }
}
