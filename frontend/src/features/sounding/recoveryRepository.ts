import { parseSoundingSnapshot, type SoundingSnapshot } from "../../lib/contracts/soundingRecovery";
import { parseRecoverySession, type RecoverySession } from "./recoverySession";

export interface RecoveryRepository {
  start: () => Promise<RecoverySession | null>;
  source: (id: string) => Promise<SoundingSnapshot>;
  save: (session: RecoverySession | null, sources: readonly SoundingSnapshot[], previous?: RecoverySession | null) => Promise<void>;
  close: () => void;
}

/** IndexedDB transactions keep the last complete checkpoint if a write fails or the app exits. */
export function createRecoveryRepository(): RecoveryRepository {
  let previous: RecoverySession | null = null;
  const database = new Promise<IDBDatabase>((resolve, reject) => {
    const request = indexedDB.open("sharppy-sounding-recovery", 1);
    request.onupgradeneeded = () => {
      const db = request.result;
      db.createObjectStore("sessions"); db.createObjectStore("sources");
    };
    request.onsuccess = () => { request.result.onversionchange = () => { request.result.close(); }; resolve(request.result); };
    request.onerror = () => { reject(request.error ?? new Error("Recovery storage could not be opened.")); };
    request.onblocked = () => { reject(new Error("Close the other app window to open recovery storage.")); };
  });
  const read = (store: IDBObjectStore, key: IDBValidKey): Promise<unknown> => new Promise((resolve, reject) => {
    const request: IDBRequest<unknown> = store.get(key);
    request.onsuccess = () => { resolve(request.result); }; request.onerror = () => { reject(request.error ?? new Error("The recovery record could not be read.")); };
  });
  const transact = async <T,>(operation: (sessions: IDBObjectStore, sources: IDBObjectStore) => Promise<T>): Promise<T> => {
    const db = await database;
    const transaction = db.transaction(["sessions", "sources"], "readwrite", { durability: "strict" });
    let finished = false;
    const complete = new Promise<void>((resolve, reject) => {
      transaction.oncomplete = () => { finished = true; resolve(); };
      transaction.onabort = transaction.onerror = () => { finished = true; reject(transaction.error ?? new Error("Recovery changes could not be saved.")); };
    });
    // Attach a rejection handler immediately; request errors may arrive before the operation settles.
    void complete.catch(() => undefined);
    try {
      const value = await operation(transaction.objectStore("sessions"), transaction.objectStore("sources"));
      await complete;
      return value;
    } catch (error) {
      // eslint-disable-next-line @typescript-eslint/no-unnecessary-condition -- Transaction callbacks change this flag while the operation is awaiting requests.
      if (!finished) transaction.abort();
      throw error;
    }
  };
  return {
    start: async () => {
      previous = await transact(async (sessions) => {
        const current = await read(sessions, "current");
        const old = current ?? await read(sessions, "previous");
        const session = old === undefined || old === null ? null : parseRecoverySession(old);
        if (session !== null) sessions.put(session, "previous");
        sessions.delete("current");
        return session;
      });
      return previous;
    },
    source: id => transact(async (_sessions, sources) => {
      const source = parseSoundingSnapshot(await read(sources, id));
      if (source.soundingId !== id) throw new Error("The saved sounding source does not match this session.");
      return source;
    }),
    save: async (session, added, previousOverride) => {
      if (session !== null && JSON.stringify(session).length > 16 * 1024 * 1024) throw new Error("This edit history exceeds the recovery storage limit.");
      const nextPrevious = previousOverride === undefined ? previous : previousOverride;
      await transact(async (sessions, sources) => {
        if (session === null) sessions.delete("current"); else sessions.put(session, "current");
        if (previousOverride === null) sessions.delete("previous");
        else if (previousOverride !== undefined) sessions.put(previousOverride, "previous");
        for (const source of added) sources.put(source, source.soundingId);
        const keep = new Set([...(session?.open ?? []), ...(nextPrevious?.open ?? [])].map(item => item.soundingId));
        const keys = await new Promise<IDBValidKey[]>((resolve, reject) => {
          const request = sources.getAllKeys();
          request.onsuccess = () => { resolve(request.result); }; request.onerror = () => { reject(request.error ?? new Error("Saved recovery sources could not be listed.")); };
        });
        for (const key of keys) if (typeof key !== "string" || !keep.has(key)) sources.delete(key);
      });
      if (previousOverride !== undefined) previous = previousOverride;
    },
    close: () => { void database.then(db => { db.close(); }, () => undefined); },
  };
}
