export const CACHE_KEYS = ["models", "maps", "outlooks", "radar", "satellite", "observations", "alerts"] as const;
export type CacheKey = (typeof CACHE_KEYS)[number];
export type CacheTarget = CacheKey | "all";
export interface CacheUsage {
  key: CacheKey;
  bytes: number;
  files: number;
  clearableBytes: number;
}
export interface StorageUsage { caches: CacheUsage[] }
export interface CacheClearResult { usage: StorageUsage; freedBytes: number; failedFiles: number }
