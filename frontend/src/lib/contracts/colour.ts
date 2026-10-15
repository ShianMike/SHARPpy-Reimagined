// The `#rrggbb` colour check shared by the contract parsers
// (DESIGN-contracts C8), split out so parser modules stay under the size rule.

import { ContractError } from "./guards";

const HEX_COLOUR = /^#[0-9a-f]{6}$/i;

export function parseColour(value: unknown, path: string): string {
  const text = typeof value === "string" ? value : "";
  if (typeof value !== "string" || !HEX_COLOUR.test(text)) {
    throw new ContractError(path, "expected a #rrggbb colour");
  }
  return text;
}
