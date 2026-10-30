import { OverlayErrorNotice } from "./OverlayErrorNotice";
import { formatUtc } from "../../features/sounding/soundingTime";
import { useWeatherTime } from "../../features/tracking/useWeatherTime";
import { useWeatherAlerts } from "../weatherAlertsQuery";
import styles from "./LayerPanel.module.css";

export function WeatherAlertStatus({ kind }: { kind: "warning" | "watch" }) {
  const alerts = useWeatherAlerts(useWeatherTime(), true);
  const count = alerts.data?.alerts.filter((alert) => alert.kind === kind).length ?? 0;
  return <div className={styles.extra}>
    {alerts.isError ? <OverlayErrorNotice error={alerts.error} operation="get_weather_alerts" onRetry={() => { void alerts.refetch(); }} busy={alerts.isFetching} testId="weather-alerts-error" />
      : alerts.data === undefined ? <p className={styles.hint}>Checking NWS {kind === "warning" ? "warnings" : "watches"}…</p>
      : <p className={styles.hint} data-testid={`${kind}-shown`}>{`${String(count)} active ${kind === "warning" ? (count === 1 ? "warning" : "warnings") : (count === 1 ? "watch" : "watches")} · NWS · checked ${formatUtc(alerts.data.fetchedAt)}. Hover an area for its issue and expiry times.`}</p>}
    <p className={styles.hint}>NWS updates about every 5 minutes. Past alerts are available for snapshots cached during this session.</p>
  </div>;
}
