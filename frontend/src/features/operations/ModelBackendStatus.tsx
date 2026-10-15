import { useQueries } from "@tanstack/react-query";
import { ChevronDown, RefreshCw, Server } from "lucide-react";
import { useState, type ReactNode } from "react";
import { useBackendClient, useBackendHealth } from "../../lib/backend/BackendProvider";
import type { ForecastModel, ModelStatus } from "../../types/soundings";
import { Action } from "../../ui/Controls";
import { compactUtc, forecastLabel, formatUtc } from "../sounding/soundingTime";
import styles from "./OperationsWorkspace.module.css";

const LABELS: Record<ModelStatus["state"], string> = { available: "Available", partial: "Partial", unavailable: "Not found", unknown: "Unconfirmed", configured: "Configured" };
const FILTERS = [{ key: "all", label: "All models" }, { key: "available", label: "Available" }, { key: "attention", label: "Needs attention" }] as const;

export function ModelBackendStatus({ models }: { models: readonly ForecastModel[] }): ReactNode {
  const client = useBackendClient();
  const health = useBackendHealth();
  const [filter, setFilter] = useState<(typeof FILTERS)[number]["key"]>("all");
  const enabled = health.data?.bridge.status === "up";
  const queries = useQueries({ queries: models.map((model) => ({
    queryKey: ["models", "status", model.key, model.suggestedRun, model.defaultForecastHour],
    queryFn: () => client.getModelStatus(model.key, model.suggestedRun, model.defaultForecastHour),
    enabled, staleTime: 2 * 60_000, retry: false, refetchOnWindowFocus: false,
  })) });
  const checking = queries.some((query) => query.isFetching);
  const rows = models.map((model, index) => {
    const query = queries[index];
    const status = query?.data;
    // Keep the last result visible during refresh so filtered rows stay in place.
    const state = query?.isError ? "error" : status?.state ?? (query?.isFetching ? "checking" : "waiting");
    const label = state === "error" ? "Check failed" : status === undefined ? (state === "checking" ? "Checking…" : "Waiting") : LABELS[status.state];
    const message = query?.isError ? query.error.message : status?.message ?? "Waiting for a provider check.";
    const attention = ["partial", "unavailable", "unknown", "error"].includes(state);
    return { model, query, status, state, label, message, attention };
  });
  const counts = { all: rows.length, available: rows.filter((row) => row.state === "available").length, attention: rows.filter((row) => row.attention).length };
  const visible = rows.filter((row) => filter === "all" || (filter === "attention" ? row.attention : row.state === "available"));
  return <section className={styles.backends} aria-labelledby="model-backends-title">
    <div className={styles.sectionHeading}><span className={styles.iconTile}><Server aria-hidden="true" /></span><div><h2 id="model-backends-title">Model backends</h2><p>Latest cycle availability</p></div><Action aria-label="Refresh statuses" disabled={!enabled || checking} onClick={() => { void Promise.all(queries.map((query) => query.refetch())); }}><RefreshCw className="icon-sm" aria-hidden="true" />{checking ? "Checking…" : "Refresh"}</Action></div>
    <div className={styles.backendToolbar}><div className={styles.backendFilters} role="group" aria-label="Filter model statuses">{FILTERS.map((item) => <button key={item.key} type="button" aria-pressed={filter === item.key} onClick={() => { setFilter(item.key); }}>{item.label}<span aria-hidden="true">{counts[item.key]}</span></button>)}</div><span className={styles.backendProgress} role="status">{checking ? `${String(queries.filter((query) => !query.isFetching && (query.data !== undefined || query.isError)).length)} of ${String(models.length)} checked` : "Select a model for details"}</span></div>
    <ul className={styles.backendList} aria-label="Model backend statuses">{visible.map(({ model, query, status, state, label, message }) => <li key={model.key}>
      <details className={styles.backendModel}>
        <summary><span className={styles.backendIdentity}><strong>{model.label}</strong><small>{status?.provider ?? "Provider check pending"}</small></span><span className={styles.backendState} data-state={state}><i aria-hidden="true" />{label}</span><ChevronDown className="icon-sm" aria-hidden="true" /></summary>
        <div className={styles.backendDetail}>
          <p>{message}</p>
          <dl><div><dt>Cycle · UTC</dt><dd><time dateTime={model.suggestedRun} title={formatUtc(model.suggestedRun)}>{compactUtc(model.suggestedRun)}</time></dd></div><div><dt>Forecast hour</dt><dd>{forecastLabel(model.defaultForecastHour)}</dd></div></dl>
          <div className={styles.backendChecked}><span>{status === undefined ? "Not checked yet" : <>Last checked <time dateTime={status.checkedAt} title={formatUtc(status.checkedAt)}>{status.checkedAt.slice(11, 19)} UTC</time></>}</span><Action aria-label={`Recheck ${model.label}`} disabled={!enabled || query?.isFetching} onClick={() => { void query?.refetch(); }}>{query?.isFetching ? "Checking…" : "Check again"}</Action></div>
        </div>
      </details>
    </li>)}</ul>
    {visible.length === 0 ? <div className={styles.backendEmpty}><p>{checking ? "Checks are still running. Results will appear here." : "No models match this filter."}</p><Action onClick={() => { setFilter("all"); }}>Show all models</Action></div> : null}
    <p className={styles.backendHint}>Suggested cycles · Expand a model for the checked hour and provider response. Open-Meteo is checked when you fetch a sounding.</p>
  </section>;
}
