import assert from "node:assert/strict";
import { test } from "node:test";
import { releasePolicy } from "./release-policy.mjs";

const stable = (tag_name, other = {}) => ({ tag_name, draft: false, prerelease: false, ...other });
const releases = [stable("v3.4.0", { published_at: "2026-10-30" }), stable("v3.5.0"), stable("v3.5.1")];

test("existing-tag candidates use the greatest earlier version, regardless of publication dates", () => {
  assert.equal(releasePolicy(releases, "v3.5.1", false), "v3.5.0");
  assert.equal(releasePolicy(releases, "v3.5.0", false), "v3.4.0");
});
test("changed releases require a greater application version", () => {
  assert.throws(() => releasePolicy(releases, "v3.5.1", true), /cannot be replaced/);
  assert.throws(() => releasePolicy(releases, "v3.4.1", true), /higher application version/);
  assert.equal(releasePolicy(releases, "v3.5.2", true), "v3.5.1");
});
test("a failed initial upload can resume its draft without touching a published release", () => {
  assert.equal(releasePolicy([...releases, stable("v3.5.2", { draft: true })], "v3.5.2", true), "v3.5.1");
});
test("drafts and prereleases cannot become upgrade baselines", () => {
  assert.equal(releasePolicy([...releases, stable("v3.6.0", { draft: true }), stable("v3.6.0-beta.1", { prerelease: true })], "v3.7.0", false), "v3.5.1");
  assert.throws(() => releasePolicy([], "v3.5.2", false), /earlier stable/);
  assert.throws(() => releasePolicy(releases, "../unsafe", false), /Release tag/);
});
