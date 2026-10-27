import { CACHE_KEYS, type CacheClearResult, type CacheUsage, type StorageUsage } from "../../types/storage";
import { at, ContractError, expectArray, expectExactKeys, expectInteger, expectObject, expectOneOf } from "./guards";

const bytes = (value: unknown, path: string): number => expectInteger(value, path, { min: 0, max: Number.MAX_SAFE_INTEGER });

export function parseStorageUsage(value: unknown, path = "storage"): StorageUsage {
  const obj = expectObject(value, path);
  expectExactKeys(obj, path, ["caches"]);
  const caches = expectArray(obj.caches, at(path, "caches"), (item, p): CacheUsage => {
    const row = expectObject(item, p);
    expectExactKeys(row, p, ["key", "bytes", "files", "clearableBytes"]);
    const result = { key: expectOneOf(row.key, at(p, "key"), CACHE_KEYS), bytes: bytes(row.bytes, at(p, "bytes")),
      files: bytes(row.files, at(p, "files")), clearableBytes: bytes(row.clearableBytes, at(p, "clearableBytes")) };
    if (result.clearableBytes > result.bytes) throw new ContractError(p, "clearable bytes exceed the cache size");
    return result;
  });
  if (caches.length !== CACHE_KEYS.length || new Set(caches.map((row) => row.key)).size !== CACHE_KEYS.length) {
    throw new ContractError(path, "each cache category must appear once");
  }
  return { caches };
}

export function parseCacheClearResult(value: unknown): CacheClearResult {
  const obj = expectObject(value, "clearCache");
  expectExactKeys(obj, "clearCache", ["usage", "freedBytes", "failedFiles"]);
  return { usage: parseStorageUsage(obj.usage), freedBytes: bytes(obj.freedBytes, "freedBytes"), failedFiles: bytes(obj.failedFiles, "failedFiles") };
}

export function parseCachedHours(value: unknown): number[] {
  const hours = expectArray(value, "cachedHours", (hour, path) => expectInteger(hour, path, { min: 0, max: 384 }));
  if (hours.length > 385 || hours.some((hour, index) => index > 0 && hour <= (hours[index - 1] ?? -1))) throw new ContractError("cachedHours", "hours must be unique and sorted");
  return hours;
}
