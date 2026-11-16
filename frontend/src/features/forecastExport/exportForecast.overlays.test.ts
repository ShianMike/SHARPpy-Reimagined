// @vitest-environment jsdom
import { expect, it, vi } from "vitest";
import { state, setup as setupExport } from "../../test/forecastExportHarness";
import { createTransport } from "../../lib/backend/fixtureTransport";
import { mapCaptureSources } from "../../map/runtime/mapCapture";
import { useMapStore } from "../../stores/mapStore";
import { exportForecast } from "./exportForecast";

const setup = () => setupExport(createTransport());

it("preloads each panel's reports and all enabled outlooks and isobars before a layout PNG capture", async () => {
  const { client, queryClient, options } = setup();
  const store = useMapStore.getState();
  store.setPanelLayout(2);
  for (const panel of [0, 1]) {
    store.setActivePanel(panel);
    store.setLayerVisible("storm-reports", true);
    store.setReportFilter(panel === 0 ? "tornado" : "hail");
    store.setReportWindowS(panel === 0 ? 7200 : 43200);
    store.setLayerVisible("spc-torn", true);
    store.setLayerVisible("isobars", true);
  }
  const fetchReports = client.getStormReports.bind(client);
  const releases: (() => void)[] = [];
  const reports = vi.spyOn(client, "getStormReports").mockImplementation((...args) => new Promise((resolve, reject) => {
    releases.push(() => { void fetchReports(...args).then(resolve, reject); });
  }));
  const outlooks = vi.spyOn(client, "getSpcOutlook"), contours = vi.spyOn(client, "getFieldContours");
  const captures = [0, 1].map(panel => {
    const captureFrame = vi.fn(() => Promise.resolve(document.createElement("canvas")));
    mapCaptureSources.set(panel, { captureFrame }); return captureFrame;
  });
  const job = exportForecast({ ...options, hours: [2], format: "png",
    panels: [0, 1].map(panel => ({ panel, product: options.product })) }, client, queryClient,
    new AbortController().signal, document.createElement("canvas"), () => undefined);
  try {
    await vi.waitFor(() => { expect(reports).toHaveBeenCalledTimes(2); });
    expect(outlooks).toHaveBeenCalledWith("torn", "2026-10-04T14:00:00Z", expect.any(AbortSignal));
    expect(contours).toHaveBeenCalledWith("mslp", options.run, 2, "hrrr", expect.any(AbortSignal));
    expect(reports).toHaveBeenCalledWith("2026-10-04T14:00:00Z", 7200, "tornado", expect.any(AbortSignal));
    expect(reports).toHaveBeenCalledWith("2026-10-04T14:00:00Z", 43200, "hail", expect.any(AbortSignal));
    for (const capture of captures) expect(capture).not.toHaveBeenCalled();
  } finally { releases.forEach(release => { release(); }); }
  await job;
  for (const capture of captures) expect(capture).toHaveBeenCalledOnce();
  queryClient.clear();
});

it("waits for reports at the final selected hour before capturing the first GIF frame", async () => {
  const { client, queryClient, options, captureFrame } = setup();
  useMapStore.getState().setLayerVisible("storm-reports", true);
  const fetchReports = client.getStormReports.bind(client);
  let release = (): void => undefined;
  const reports = vi.spyOn(client, "getStormReports").mockImplementation((...args) => args[0] === "2026-10-04T15:00:00Z"
    ? new Promise((resolve, reject) => { release = () => { void fetchReports(...args).then(resolve, reject); }; }) : fetchReports(...args));
  const job = exportForecast(options, client, queryClient, new AbortController().signal, document.createElement("canvas"), () => undefined);
  try {
    await vi.waitFor(() => { expect(reports).toHaveBeenCalledTimes(3); });
    expect(captureFrame).not.toHaveBeenCalled();
    expect(state.events).not.toContain("encoder:start");
  } finally { release(); }
  await job;
  expect(captureFrame).toHaveBeenCalledTimes(3);
  queryClient.clear();
});
