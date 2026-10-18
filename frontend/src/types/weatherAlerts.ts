export interface WeatherAlert {
  id: string;
  event: string;
  kind: "warning" | "watch";
  office: string;
  issued: string;
  expires: string;
  colour: string;
  sourceUrl: string;
  polygons: readonly (readonly (readonly (readonly [number, number])[])[])[];
}

export interface WeatherAlerts {
  validTime: string;
  fetchedAt: string;
  alerts: readonly WeatherAlert[];
}
