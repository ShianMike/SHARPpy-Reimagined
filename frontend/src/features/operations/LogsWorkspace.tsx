import { ChevronDown, Copy, Download, Search, SquareTerminal, Trash2, X } from "lucide-react";
import { useState, type ReactNode } from "react";
import { formatDiagnosticSummary } from "../../lib/diagnostics";
import { MAX_LOG_ENTRIES, useLogStore, type LogEntry, type LogLevel } from "../../stores/logStore";
import { Action, EmptyPanel, IconAction } from "../../ui/Controls";
import { HealthIndicator } from "../toolbar/HealthIndicator";
import styles from "./LogsWorkspace.module.css";

const LEVELS = [{ value: "all", label: "All events" }, { value: "error", label: "Errors" }, { value: "warning", label: "Warnings" }, { value: "info", label: "Info" }] as const;
const LEVEL_LABELS = { error: "Error", warning: "Warning", info: "Info" };

export function LogsWorkspace(): ReactNode {
  const entries = useLogStore((state) => state.entries);
  const [level, setLevel] = useState<LogLevel | "all">("all");
  const [search, setSearch] = useState("");
  const [exportError, setExportError] = useState("");
  const [copyStatus, setCopyStatus] = useState<"copying" | "copied" | "error" | null>(null);
  const query = search.trim().toLocaleLowerCase();
  const filtered = entries.filter((entry) => (level === "all" || level === entry.level) && `${entry.source} ${entry.message} ${entry.detail}`.toLocaleLowerCase().includes(query));
  const copyDiagnostic = async (entry: LogEntry): Promise<void> => {
    setCopyStatus("copying");
    try {
      await navigator.clipboard.writeText(formatDiagnosticSummary(entry.time, entry.diagnostic));
      setCopyStatus("copied");
    } catch { setCopyStatus("error"); }
  };
  const exportLogs = (): void => {
    try {
      const text = filtered.map((entry) => `${entry.time} [${entry.level.toUpperCase()}] ${entry.source}: ${entry.message}${entry.detail ? `\n  ${entry.detail}` : ""}`).join("\n");
      const url = URL.createObjectURL(new Blob([text], { type: "text/plain;charset=utf-8" }));
      const link = document.createElement("a");
      link.href = url; link.download = `sharppy-session-${new Date().toISOString().slice(0, 10)}.log`;
      document.body.append(link); link.click(); link.remove();
      window.setTimeout(() => { URL.revokeObjectURL(url); }, 1000);
      setExportError("");
    } catch { setExportError("The log could not be exported. Try again."); }
  };
  return <div className={styles.page}>
    <header className={styles.heading}><div><h1>Logs</h1><p>Requests, errors, and connection changes in this session.</p></div><HealthIndicator /><Action onClick={exportLogs} disabled={filtered.length === 0}><Download className="icon-sm" aria-hidden="true" />Export view</Action></header>
    <div className={styles.tools}>
      <div className={styles.filters} role="group" aria-label="Log level">{LEVELS.map((item) => <button key={item.value} type="button" aria-pressed={level === item.value} onClick={() => { setLevel(item.value); }}>{item.label}<span aria-hidden="true">{item.value === "all" ? entries.length : entries.filter((entry) => entry.level === item.value).length}</span></button>)}</div>
      <label className={styles.search}><Search className="icon-sm" aria-hidden="true" /><input type="search" aria-label="Search logs" placeholder="Search messages or sources" value={search} onChange={(event) => { setSearch(event.currentTarget.value); }} /></label>
      <IconAction icon={Trash2} label="Clear session log" disabled={entries.length === 0} onClick={() => { useLogStore.getState().clear(); }} />
    </div>
    {exportError ? <p role="alert" className={styles.exportError}>{exportError}</p> : null}
    {copyStatus === null ? null : <p role={copyStatus === "error" ? "alert" : "status"} className={copyStatus === "error" ? styles.exportError : styles.copyNotice}>{copyStatus === "copying" ? "Copying diagnostic summary…" : copyStatus === "copied" ? "Diagnostic summary copied." : "Clipboard access was unavailable. Try copying the diagnostic summary again."}</p>}
    <div className={styles.logBody} data-empty={filtered.length === 0}>
      {filtered.length === 0 ? <EmptyPanel icon={SquareTerminal} title={entries.length === 0 ? "No events yet" : "No matching events"} message={entries.length === 0 ? "New requests and errors will appear here as you use the workspace." : "Try a different log level or search term."}>{entries.length === 0 ? null : <Action onClick={() => { setLevel("all"); setSearch(""); }}><X className="icon-sm" aria-hidden="true" />Reset filters</Action>}</EmptyPanel> :
        <table className={styles.table} aria-label="Session events"><thead><tr><th scope="col">Time · UTC</th><th scope="col">Level</th><th scope="col">Source</th><th scope="col">Event</th></tr></thead><tbody>{filtered.map((entry) => <tr key={entry.id} data-level={entry.level}>
          <td><time dateTime={entry.time} title={entry.time}>{entry.time.slice(11, 19)}</time></td><td><span className={styles.level}>{LEVEL_LABELS[entry.level]}</span></td><td className={styles.source}>{entry.source.replaceAll("_", " ")}</td>
          <td><div className={styles.eventRow}><details className={styles.event}><summary><span>{entry.message}</span><ChevronDown className="icon-sm" aria-hidden="true" /></summary><div className={styles.detail}><p>{entry.message}</p>{entry.detail ? <p>{entry.detail}</p> : null}<code>{entry.source} · {entry.time}</code></div></details>{entry.level === "info" ? null : <IconAction icon={Copy} size="sm" label="Copy diagnostic summary" disabled={copyStatus === "copying"} onClick={() => { void copyDiagnostic(entry); }} />}</div></td>
        </tr>)}</tbody></table>}
    </div>
    <footer className={styles.footer}><span role="status">{filtered.length} of {entries.length} events</span><span>Newest first · Last {MAX_LOG_ENTRIES} events · Resets when the app reloads</span></footer>
  </div>;
}
