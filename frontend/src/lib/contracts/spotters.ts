import type { SpotterMotion, SpotterPositions } from "../../types/spotters";
import { at, ContractError, expectArray, expectBoolean, expectExactKeys, expectFiniteNumber, expectInteger, expectObject, expectOneOf, expectString, expectTimestamp } from "./guards";

function parseMotion(value: unknown, path: string): SpotterMotion {
  const motion = expectObject(value, path);
  const kind = expectOneOf(motion.kind, at(path, "kind"), ["stationary", "heading"]);
  expectExactKeys(motion, path, kind === "stationary" ? ["kind"] : ["kind", "degrees"]);
  return kind === "stationary" ? { kind } : { kind, degrees: expectInteger(motion.degrees, at(path, "degrees"), { min: 0, max: 359 }) };
}

export function parseSpotterPositions(value: unknown): SpotterPositions {
  const snapshot = expectObject(value, "");
  expectExactKeys(snapshot, "", ["checkedAt", "streamersAvailable", "positions"]);
  if (Array.isArray(snapshot.positions) && snapshot.positions.length > 2048) {
    throw new ContractError("positions", "has too many spotter positions");
  }
  return {
    checkedAt: expectTimestamp(snapshot.checkedAt, "checkedAt"),
    streamersAvailable: expectBoolean(snapshot.streamersAvailable, "streamersAvailable"),
    positions: expectArray(snapshot.positions, "positions", (item, path) => {
      const position = expectObject(item, path);
      expectExactKeys(position, path, ["latitude", "longitude", "updatedAt", "chaseCam"], ["displayName", "motion"]);
      return {
        latitude: expectFiniteNumber(position.latitude, at(path, "latitude"), { min: -90, max: 90 }),
        longitude: expectFiniteNumber(position.longitude, at(path, "longitude"), { min: -180, max: 180 }),
        updatedAt: expectTimestamp(position.updatedAt, at(path, "updatedAt")),
        chaseCam: expectBoolean(position.chaseCam, at(path, "chaseCam")),
        ...(position.displayName === undefined ? {} : {
          displayName: expectString(position.displayName, at(path, "displayName"), { minLength: 1, maxLength: 80 }),
        }),
        ...(position.motion === undefined ? {} : { motion: parseMotion(position.motion, at(path, "motion")) }),
      };
    }),
  };
}
