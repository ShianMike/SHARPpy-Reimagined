import { useIsMutating, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import { useState, type CSSProperties, type ReactNode } from "react";
import { useBackendClient } from "../../lib/backend/BackendProvider";
import { QUERY_KEYS } from "../../lib/backend/queryOptions";
import type { CacheTarget } from "../../types/storage";
import { Action, IconAction } from "../../ui/Controls";
import styles from "./SettingsView.module.css";

const LABELS = {
  models: { title: "Forecast downloads", description: "Model data used to open forecast soundings." },
  maps: { title: "Map images", description: "Forecast maps, downloaded frames, wind barbs and contours." },
  outlooks: { title: "SPC outlooks", description: "Downloaded severe weather outlooks." },
};
const CLEAR_KEY = ["storage", "clear"];

function formatStorageBytes(bytes: number): string {
  const unit = bytes < 1024 ? 0 : Math.min(4, Math.floor(Math.log2(bytes) / 10));
  return `${(bytes / 1024 ** unit).toLocaleString(undefined, { maximumFractionDigits: unit === 0 ? 0 : 1 })} ${["B", "KiB", "MiB", "GiB", "TiB"][unit] ?? "B"}`;
}

export function StorageSettings(): ReactNode {
  const client = useBackendClient();
  const queryClient = useQueryClient();
  const [confirm, setConfirm] = useState(false);
  const usage = useQuery({ queryKey: QUERY_KEYS.storage, queryFn: () => client.getStorageUsage(), retry: false });
  const clearing = useIsMutating({ mutationKey: CLEAR_KEY }) > 0;
  const mutation = useMutation({ mutationKey: CLEAR_KEY, mutationFn: (cache: CacheTarget) => client.clearCache(cache),
    onSuccess: (result) => { queryClient.setQueryData(QUERY_KEYS.storage, result.usage); setConfirm(false); } });
  const busy = clearing || usage.isFetching;
  const total = usage.data?.caches.reduce((sum, row) => sum + row.bytes, 0) ?? 0;
  const clearable = usage.data?.caches.reduce((sum, row) => sum + row.clearableBytes, 0) ?? 0;
  const error = mutation.error ?? usage.error;
  return <div className={styles.storage} aria-busy={busy}>
    <section className={styles.section} aria-label="Cache usage">
      <div className={styles.storageHeading}><h3>Cached weather data</h3><IconAction icon={RefreshCw} size="sm" label="Refresh storage usage" disabled={busy} onClick={() => { mutation.reset(); void usage.refetch(); }} /></div>
      <div className={styles.storageSummary}><div>
        <p className={styles.storageTotal}>{usage.data === undefined ? "—" : formatStorageBytes(total)}</p>
        <p className={styles.hint}>{clearing ? "Clearing cached weather data…" : usage.isPending ? "Calculating cache usage…" : total === 0 && usage.data !== undefined ? "No cached weather data on this device." : usage.data === undefined ? "Cache usage is unavailable." : `${formatStorageBytes(clearable)} can be cleared`}</p>
      </div><Action disabled={busy || clearable === 0 || confirm} onClick={() => { mutation.reset(); setConfirm(true); }}>Clear all caches…</Action></div>
      {usage.data === undefined ? null : <div className={styles.storageBar} aria-hidden="true">{usage.data.caches.map((row) => <span key={row.key} data-cache={row.key} style={{ "--cache-bytes": row.bytes } as CSSProperties} />)}</div>}
      {confirm ? <div className={styles.cacheConfirm}><p>Clear all available caches?</p><div className={styles.updateActions}><Action disabled={clearing} onClick={() => { setConfirm(false); }}>Cancel</Action><Action tone="accent" disabled={busy || clearable === 0} onClick={() => { mutation.mutate("all"); }}>{clearing ? "Clearing…" : "Clear now"}</Action></div></div> : null}
    </section>
    {error === null ? mutation.data === undefined ? null : <p className={styles.storageFeedback} role="status">{formatStorageBytes(mutation.data.freedBytes)} cleared.{mutation.data.failedFiles > 0 ? " Some files couldn’t be removed. Refresh and try again." : ""}</p>
      : <div className={styles.storageFeedback} role="alert"><p>{error.message}</p><Action disabled={busy} onClick={() => { mutation.reset(); void usage.refetch(); }}>Try again</Action></div>}
    {usage.data === undefined ? null : <section className={styles.section} aria-label="Cache categories">{usage.data.caches.map((row) => <div className={styles.cacheRow} key={row.key}>
      <div className={styles.cacheLabel}><h3><span data-cache={row.key} aria-hidden="true" />{LABELS[row.key].title}</h3><p className={styles.hint}>{LABELS[row.key].description}</p>
        {row.bytes > row.clearableBytes ? <p className={styles.cacheKept}>{formatStorageBytes(row.bytes - row.clearableBytes)} kept while in use or pinned</p> : null}</div>
      <div className={styles.cacheActions}><span className="tabular">{formatStorageBytes(row.bytes)}</span><Action aria-label={`Clear ${LABELS[row.key].title.toLowerCase()}`} disabled={busy || row.clearableBytes === 0} onClick={() => { mutation.mutate(row.key); }}>{clearing && mutation.variables === row.key ? "Clearing…" : "Clear"}</Action></div>
    </div>)}</section>}
    <p className={styles.hint}>Your files, open soundings and settings are kept. Cleared weather data downloads again when needed.</p>
  </div>;
}
