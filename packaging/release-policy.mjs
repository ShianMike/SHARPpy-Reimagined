// Published versions are permanent; candidate rebuilds use their original tag.
import { appendFileSync, readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";
import { parseArgs } from "node:util";

function version(tag) {
  const match = /^v(\d+)\.(\d+)\.(\d+)(?:-[0-9A-Za-z.-]+)?$/.exec(tag);
  return match === null ? null : match.slice(1, 4).map(BigInt);
}

function compare(left, right) {
  for (let index = 0; index < 3; index++) {
    if (left[index] !== right[index]) return left[index] < right[index] ? -1 : 1;
  }
  return 0;
}

export function releasePolicy(releases, tag, publish) {
  const candidate = version(tag);
  if (candidate === null) throw new Error("Release tag must use vMAJOR.MINOR.PATCH");
  const existing = releases.find((release) => release.tag_name === tag);
  if (publish && existing !== undefined && !existing.draft) {
    throw new Error("Published releases cannot be replaced; use a higher application version");
  }
  const stable = releases.filter((release) => !release.draft && !release.prerelease)
    .map((release) => ({ tag: release.tag_name, version: version(release.tag_name) }))
    .filter((release) => release.version !== null);
  if (publish && stable.some((release) => compare(release.version, candidate) >= 0)) {
    throw new Error("A new release must have a higher application version than published stable releases");
  }
  const previous = stable.filter((release) => compare(release.version, candidate) < 0)
    .sort((left, right) => compare(right.version, left.version))[0];
  if (previous === undefined) throw new Error("Release validation requires an earlier stable upgrade baseline");
  return previous.tag;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const { values } = parseArgs({ options: { tag: { type: "string" }, releases: { type: "string" }, publish: { type: "boolean", default: false } } });
  const pages = JSON.parse(readFileSync(values.releases, "utf8"));
  if (!Array.isArray(pages)) throw new Error("Expected a GitHub release list");
  const releases = pages.flat();
  if (!releases.every((release) => release !== null && typeof release === "object"
    && typeof release.tag_name === "string" && typeof release.draft === "boolean" && typeof release.prerelease === "boolean")) {
    throw new Error("Invalid GitHub release metadata");
  }
  const baseline = releasePolicy(releases, values.tag, values.publish);
  appendFileSync(process.env.GITHUB_OUTPUT, `previous_tag=${baseline}\n`);
}
