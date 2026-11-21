import type { ReactNode } from "react";
import { formatUtc } from "../../features/sounding/soundingTime";
import { useLiveTrackingStore } from "../../stores/liveTrackingStore";
import { Action } from "../../ui/Controls";
import { useSpotterPositions } from "../spotterQueries";
import { OverlayErrorNotice } from "./OverlayErrorNotice";
import styles from "./LayerPanel.module.css";

export function SpotterNetworkStatus(): ReactNode {
  const { followingLive, positions, query } = useSpotterPositions(true);
  if (!followingLive) {
    return <div className={styles.extra}>
      <p className={styles.hint}>Spotter positions are available at the live time.</p>
      <Action onClick={() => {
        const live = useLiveTrackingStore.getState();
        live.setPlaying(false);
        live.selectTime(null);
      }}>Return to Live</Action>
    </div>;
  }
  const snapshot = query.data;
  if (query.isError && (positions === null || positions.length === 0)) {
    return <OverlayErrorNotice error={query.error} operation="get_spotter_positions"
      onRetry={() => { void query.refetch(); }} busy={query.isFetching} testId="spotter-network-error" />;
  }
  if (snapshot === undefined || positions === null) {
    return <p className={styles.hint} role="status">Checking Spotter Network…</p>;
  }
  const chaseCams = positions.filter((position) => position.chaseCam).length;
  return <div className={styles.extra} data-testid="spotter-network-shown">
    <dl className={styles.feedRows}>
      <div><dt className={styles.spotterKey}><span aria-hidden="true" />Active spotters</dt><dd>{positions.length - chaseCams}</dd></div>
      <div><dt className={styles.spotterKey} data-chase-cam="true"><span aria-hidden="true" />Chase-cam spotters</dt><dd>{chaseCams}</dd></div>
      <div><dt>Last checked (UTC)</dt><dd><time dateTime={snapshot.checkedAt} title={formatUtc(snapshot.checkedAt)}>
        {formatUtc(snapshot.checkedAt, { includeYear: false })}
      </time></dd></div>
    </dl>
    <p className={styles.hint}>{positions.length === 0
      ? "No positions reported in the last 10 minutes."
      : "Showing positions updated in the last 10 minutes. Select a marker for details."}</p>
    {snapshot.streamersAvailable ? null : <p className={styles.hint}>Chase-cam feed unavailable.</p>}
    {query.isRefetchError ? <p className={styles.warning} role="status">Spotter Network refresh failed.</p> : null}
  </div>;
}
