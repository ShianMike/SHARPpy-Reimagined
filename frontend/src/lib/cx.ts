/** Joins class names, skipping empty and missing ones (CSS module keys may be undefined). */
export function cx(...names: (string | false | null | undefined)[]): string {
  return names.filter((name): name is string => typeof name === "string" && name !== "").join(" ");
}
