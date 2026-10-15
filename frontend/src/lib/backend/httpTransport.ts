// Same public DTOs and binary frames as Tauri, through the local Vite proxy.
import type { Transport } from "./transport";

export function createTransport(): Transport {
  return {
    async call(command, args) {
      const response = await fetch("/__backend", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: 1, method: command, params: args ?? {} }),
      });
      const raw: unknown = response.headers.get("Content-Type") === "application/octet-stream"
        ? await response.arrayBuffer()
        : await response.json();
      if (!response.ok) {
        // BackendClient parses structured rejections exactly as it does Tauri's.
        throw raw;
      }
      return raw;
    },
    listen: () => Promise.reject(new Error("Browser development uses the existing health polling.")),
  };
}
