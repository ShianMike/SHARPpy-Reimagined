// The only module that imports @tauri-apps/api (boundary rule 3). It forwards
// and holds no logic: a rejection reaches BackendClient as Tauri's raw value.

import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import type { Transport } from "./transport";

export function createTransport(): Transport {
  return {
    call: (command, args) => invoke(command, args === undefined ? undefined : { ...args }),
    listen: (event, handler) =>
      listen(event, (message) => {
        handler(message.payload);
      }),
  };
}
