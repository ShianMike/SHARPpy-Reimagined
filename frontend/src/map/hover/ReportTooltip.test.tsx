// @vitest-environment jsdom
import { act, cleanup, render, screen, within } from "@testing-library/react";
import fixture from "@contracts/public/map/reports.json";
import surface from "@contracts/public/map/surface.json";
import { afterEach, describe, expect, it } from "vitest";
import { ReportTooltip } from "./ReportTooltip";
import { useHoverStore, type HoveredReport } from "./hoverStore";
import { DEFAULT_UNITS } from "../../lib/displayUnits";
import { useUiStore } from "../../stores/uiStore";

afterEach(() => { cleanup(); useHoverStore.getState().clear(); useUiStore.getState().setUnits(DEFAULT_UNITS); });
function show(overrides: Partial<HoveredReport> = {}) {
  const report = fixture.reports[0];
  if (report === undefined) throw new Error("Missing report fixture");
  useHoverStore.getState().setReport({ ...report, x: 100, y: 50, ...overrides });
  render(<ReportTooltip />);
  return screen.getByRole("tooltip");
}

describe("report popup paragraphs", () => {
  it("updates observation readings and gusts without changing timing, missing status or provider text", () => {
    const plot = surface.plots[0];
    if (plot === undefined) throw new Error("Missing observation");
    const tooltip = show({ ...plot, caption: "" });
    act(() => { useUiStore.getState().setUnits({ temperature: "°F", wind: "km/h" }); });
    expect(tooltip.textContent).toContain("70.7 °F");
    expect(tooltip.textContent).toContain("54 °F");
    expect(tooltip.textContent).toContain("270° at 37 km/h, gusting 51.9 km/h");
    expect(tooltip.textContent).toContain("2026-10-04 21:00:00Z");
    expect(tooltip.textContent).toContain("Source: NOAA/NWS API station observation");
  });
  it("separates location, labeled facts, narrative and attribution without repeating the magnitude", () => {
    const tooltip = show();
    const header = tooltip.querySelector("header"), footer = tooltip.querySelector("footer");
    if (header === null || footer === null) throw new Error("Missing report sections");
    expect(within(header).getByText("Wind 68 mph")).toBeTruthy();
    expect(within(header).getByText("Norman, Cleveland, OK")).toBeTruthy();
    expect(tooltip.querySelector("dl")?.textContent).toBe("Event time04 Oct 2005ZSourceMESONET (report source)");
    expect(screen.queryByText("Magnitude")).toBeNull();
    expect(screen.queryByText("68 mph")).toBeNull();
    expect(screen.getByText("Measured wind gust").tagName).toBe("P");
    expect(within(footer).getByText("Issuing office: NWS WFO OUN")).toBeTruthy();
    expect(within(footer).getByText("Provider: NWS Local Storm Reports via Iowa Environmental Mesonet")).toBeTruthy();
  });

  it("retains a magnitude absent from the title, unknown lines, multiple paragraphs and escaped provider text", () => {
    const tooltip = show({ label: "Wind", description: "Event time: 14 Oct 2038Z\r\nMagnitude: 64 mph\r\n\r\nMeasured gust.\r\nDamage: a fence fell.\r\n<img src=x onerror=alert(1)>" });
    expect(tooltip.querySelector("header")?.textContent).toBe("Wind");
    expect(screen.getByText("64 mph").tagName).toBe("DD");
    for (const line of ["Measured gust.", "Damage: a fence fell.", "<img src=x onerror=alert(1)>"]) {
      expect(screen.getByText(line).tagName).toBe("P");
    }
    expect(tooltip.querySelector("img")).toBeNull();
  });

  it("separates the station, observation time, individual readings and source", () => {
    const plot = surface.plots[0];
    if (plot === undefined) throw new Error("Missing surface fixture");
    const tooltip = show({ ...plot, caption: "" });
    const header = tooltip.querySelector("header"), footer = tooltip.querySelector("footer");
    if (header === null || footer === null) throw new Error("Missing observation sections");
    expect([...header.querySelectorAll("p")].map((p) => p.textContent)).toEqual(["KOUN", "Norman"]);
    const sections = [...tooltip.querySelectorAll("dl")];
    expect(sections).toHaveLength(2);
    const [timing, readings] = sections;
    if (timing === undefined || readings === undefined) throw new Error("Missing observation detail groups");
    expect([...timing.querySelectorAll("dd")].map((dd) => dd.textContent)).toEqual(["2026-10-04 21:00:00Z", "+0 min from requested"]);
    expect([...readings.querySelectorAll("dd")].map((dd) => dd.textContent)).toEqual(["21.5 °C", "12.2 °C", "270° at 20 kt, gusting 28 kt"]);
    expect(tooltip.querySelectorAll("dt")).toHaveLength(5);
    expect(within(footer).getByText("Source: NOAA/NWS API station observation")).toBeTruthy();
  });

  it.each([
    [1, "calm (0 kt)"], [2, "12 kt; direction missing"], [3, "180° at 8 kt"],
  ] as const)("keeps the complete wind reading and status for surface fixture %s", (index, value) => {
    const plot = surface.plots[index];
    if (plot === undefined) throw new Error("Missing surface fixture");
    const tooltip = show({ ...plot, caption: "" });
    expect(screen.getByText(value).tagName).toBe("DD");
    if (plot.stale) expect(screen.getByText("Stale/mismatched: more than 30 minutes from requested time").tagName).toBe("P");
    expect(tooltip.querySelectorAll("dt")).toHaveLength(5);
  });

  it.each([4, 5])("preserves unavailable and failed observation messages for fixture %s", (index) => {
    const plot = surface.plots[index];
    if (plot === undefined) throw new Error("Missing surface fixture");
    const tooltip = show({ ...plot, caption: "" });
    expect(tooltip.querySelector("dl")).toBeNull();
    expect(screen.getByText(plot.description.split("\n")[0] ?? "").tagName).toBe("P");
    expect(tooltip.querySelector("footer")?.textContent).toBe("Source: NOAA/NWS API station catalogue");
  });

  it("preserves missing readings, extra paragraphs and times without an offset", () => {
    const plot = surface.plots[2];
    if (plot === undefined) throw new Error("Missing surface fixture");
    const description = plot.description.replace(/ \(\+0 min from requested\)/, "").replace("33.0 °C", "missing") + "\nAdditional note: test.";
    const tooltip = show({ ...plot, description, caption: "" });
    expect(screen.getByText("missing").tagName).toBe("DD");
    expect(screen.getByText("Additional note: test.").tagName).toBe("P");
    expect(screen.queryByText("Time offset")).toBeNull();
    expect(tooltip.querySelectorAll("dt")).toHaveLength(4);
  });

  it("renders sparse reports and clears when the pointer leaves", () => {
    const tooltip = show({ description: "", caption: "TOR", label: "Tornado" });
    expect(tooltip.textContent).toBe("Tornado");
    expect(tooltip.style.getPropertyValue("--tooltip-x")).toBe("100px");
    act(() => { useHoverStore.getState().clear(); });
    expect(screen.queryByRole("tooltip")).toBeNull();
  });
});
