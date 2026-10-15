// Parser for get_station_catalog (DESIGN-contracts C4), including the record
// rule's bounds and duplicate-id rejection.

import type { AvailabilityBasis, Station, StationCatalog } from "../../types/contracts";
import {
  ContractError,
  at,
  expectArray,
  expectExactKeys,
  expectFiniteNumber,
  expectInteger,
  expectObject,
  expectOneOf,
  expectString,
  expectTimestamp,
} from "./guards";

export function parseStation(value: unknown, path: string): Station {
  const obj = expectObject(value, path);
  expectExactKeys(obj, path, ["id", "name", "latitude", "longitude", "source", "availability"]);
  const id = expectString(obj.id, at(path, "id"), { minLength: 1, maxLength: 16 });
  if (id.trim() === "") {
    throw new ContractError(at(path, "id"), "expected a non-blank id");
  }
  return {
    id,
    name: expectString(obj.name, at(path, "name"), { maxLength: 200 }),
    latitude: expectFiniteNumber(obj.latitude, at(path, "latitude"), { min: -90, max: 90 }),
    longitude: expectFiniteNumber(obj.longitude, at(path, "longitude"), { min: -180, max: 180 }),
    source: expectString(obj.source, at(path, "source"), { minLength: 1, maxLength: 16 }),
    availability: expectOneOf(obj.availability, at(path, "availability"), ["unknown", "listed", "notListed"]),
  };
}

function parseAvailabilityBasis(value: unknown, path: string): AvailabilityBasis {
  const obj = expectObject(value, path);
  const kind = expectOneOf(obj.kind, at(path, "kind"), ["catalogOnly", "listedAt"]);
  if (kind === "catalogOnly") {
    expectExactKeys(obj, path, ["kind"]);
    return { kind };
  }
  expectExactKeys(obj, path, ["kind", "observationTime"]);
  return { kind, observationTime: expectTimestamp(obj.observationTime, at(path, "observationTime")) };
}

export function parseStationCatalog(value: unknown, path = ""): StationCatalog {
  const obj = expectObject(value, path);
  expectExactKeys(obj, path, ["stations", "origin", "availabilityBasis", "droppedCount"]);
  const stationsPath = at(path, "stations");
  const stations = expectArray(obj.stations, stationsPath, parseStation);
  const seen = new Set<string>();
  stations.forEach((station, index) => {
    if (seen.has(station.id)) {
      throw new ContractError(at(at(stationsPath, index), "id"), `duplicate id ${JSON.stringify(station.id)}`);
    }
    seen.add(station.id);
  });
  return {
    stations,
    origin: expectOneOf(obj.origin, at(path, "origin"), ["bundled", "seedFallback"]),
    availabilityBasis: parseAvailabilityBasis(obj.availabilityBasis, at(path, "availabilityBasis")),
    droppedCount: expectInteger(obj.droppedCount, at(path, "droppedCount"), { min: 0 }),
  };
}
