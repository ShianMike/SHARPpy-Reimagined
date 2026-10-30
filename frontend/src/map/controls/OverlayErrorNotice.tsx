import { Notice } from "../../components/Notice";
import { SoundingErrorNotice } from "../../features/sounding/SoundingErrorNotice";
import { failureOf, overlayAvailability } from "../../lib/backend/errors";
import { useLiveTrackingStore } from "../../stores/liveTrackingStore";
import { useMapStore } from "../../stores/mapStore";
import { Action } from "../../ui/Controls";
import type { SoundingErrorNoticeProps } from "../../features/sounding/SoundingErrorNotice";

/** Missing history is useful context, with recovery beside the affected layer. */
export function OverlayErrorNotice(props: SoundingErrorNoticeProps) {
  const failure = failureOf(props.error);
  const reason = failure === null ? null : overlayAvailability(failure);
  const live = useMapStore((state) => state.view === "live");
  const historical = useLiveTrackingStore((state) => state.selectedTime !== null);
  if (reason === null) return <SoundingErrorNotice {...props} />;
  return <Notice tone="warning" title={reason === "historyUnavailable" ? "Alert history unavailable" : "Radar scan unavailable"}
    message={reason === "historyUnavailable"
      ? "No alert snapshot was saved for this time in this session. Alert status at this time is unknown."
      : "No radar scan matches this site, elevation and time. Choose another scan, elevation or site, or retry when a new scan is published."}
    testId={props.testId} actions={<>
      {live && historical ? <Action tone="accent" onClick={() => {
        const clock = useLiveTrackingStore.getState();
        clock.setPlaying(false); clock.tick(); clock.selectTime(null);
      }}>Return to Live</Action> : null}
      {reason === "scanMissing" ? <Action disabled={props.busy} onClick={props.onRetry}>Retry</Action> : null}
    </>} />;
}
