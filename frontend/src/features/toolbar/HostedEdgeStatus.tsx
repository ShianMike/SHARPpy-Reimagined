import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { edgeStatusQuery } from "../../lib/backend/edge";
import styles from "./HealthIndicator.module.css";

export function HostedEdgeStatus({ path }: { path: string }): ReactNode {
  const { data } = useQuery({ ...edgeStatusQuery(path), enabled: false });

  if (data === undefined) return null;
  if (data === null) return <span className={styles.edgeStatus}>Hosted service status unavailable</span>;

  const asOf = data.checkedAtMs === null ? null : new Date(data.checkedAtMs).toISOString();
  const stale = data.stale || asOf === null;
  const originDetails = [
    data.origin.status === null ? null : `HTTP ${String(data.origin.status)}`,
    data.origin.version === null ? null : `Version ${data.origin.version}`,
  ].filter(Boolean).join(" · ");
  return <section className={styles.edgeStatus} aria-label="Hosted services">
    <strong title={asOf === null ? undefined : `Checked ${asOf}`}>{stale ? "Last reported" : "Hosted services"}</strong>
    <span title={originDetails || undefined}>Origin {data.origin.state}</span>
    <span title={data.models.source || undefined}>Model probe {data.models.state}</span>
    <span title={data.radar.source || undefined}>Radar probe {data.radar.state}</span>
    {stale || data.release === null ? null : <a href={data.release.url} target="_blank" rel="noreferrer">
      Release {data.release.version} available
    </a>}
  </section>;
}
