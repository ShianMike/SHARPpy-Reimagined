import { useForecastContext } from "../forecast/useForecast";
import { liveTimestamp, useLiveTrackingStore } from "../../stores/liveTrackingStore";
import { useMapStore } from "../../stores/mapStore";

/** Live observations follow their own clock, independently of the forecast run. */
export function useWeatherTime(): string | null {
  const live = useMapStore((state) => state.view === "live");
  const time = useLiveTrackingStore((state) => state.selectedTime ?? state.now);
  const context = useForecastContext();
  return live ? liveTimestamp(time) : context?.validTime ?? null;
}
