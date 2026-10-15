// Offline component CSS checks: no server, app navigation, backend or weather data.
/* global document, getComputedStyle, requestAnimationFrame */
import assert from "node:assert/strict";
import { mkdirSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { chromium } from "@playwright/test";

const root = resolve(import.meta.dirname, "..");
const css = ["styles/tokens.css", "styles/base.css", "styles/glass.css", "styles/motion.css",
  "ui/Controls.module.css", "ui/HoverHint.module.css", "components/Popover.module.css",
  "map/controls/ParameterScale.module.css", "app/Shell.module.css", "map/WeatherMap.module.css"].map((path) => readFileSync(resolve(root, "src", path), "utf8")).join("\n");
const browser = await chromium.launch({ channel: "msedge", headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1000, height: 650 }, reducedMotion: "no-preference" });
  await page.route("**/*", (route) => route.abort());
  await page.setContent(`<html data-graphics-profile="balanced"><body>
    <main style="position:relative;height:100%;padding:32px">
      <h1>Motion component checks</h1><p id="muted" style="color:var(--text-secondary)">Isolated controls; no map or application data.</p>
      <button id="hint-target" class="action target">Hover or focus<span class="hint" data-side="bottom">A brief, readable hint</span></button>
      <button id="switch" class="toggle" role="switch" aria-checked="false" style="width:240px"><span>Layer toggle</span><span class="toggleTrack"><span class="toggleThumb"></span></span></button>
      <aside id="drawer" hidden inert data-motion="panel" style="position:absolute;top:190px;left:32px;width:300px;padding:24px;background:var(--surface-1)"><h2>Details drawer</h2><button class="action">Panel action</button></aside>
      <aside id="menu" class="popover" data-open="false" inert style="--popover-x:380px;--popover-y:190px;--popover-measured-w:300px;--popover-measured-h:160px;width:300px;padding:24px;background:var(--surface-1)"><h2>Forecast menu</h2><button class="action">Choose a value</button></aside>
      <aside class="panel"><div style="flex:1;background:var(--text-secondary);width:12px;align-self:center;border-radius:12px"></div><button id="eye" class="iconAction visibility" data-size="sm" aria-pressed="true" aria-label="Model layer"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/></svg></button></aside>
      <div id="tooltip" role="tooltip" style="position:absolute;left:32px;top:430px">Immediate pointer value: 72.4 °F</div>
      <div id="loading"><svg data-loading="true" width="16" height="16" viewBox="0 0 24 24"><path d="M12 2a10 10 0 1 1-10 10" fill="none" stroke="currentColor"/></svg></div>
    </main></body></html>`);
  await page.addStyleTag({ content: css });
  const finish = async (selector) => page.locator(selector).evaluate((element) => { element.getAnimations().forEach((animation) => animation.finish()); });
  const drawer = page.locator("#drawer"), menu = page.locator("#menu");
  await page.evaluate(() => new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done))));

  await menu.evaluate((element) => { element.dataset.open = "true"; element.inert = false; });
  const motion = await menu.evaluate((element) => {
    const animations = element.getAnimations();
    animations.forEach((animation) => { animation.pause(); animation.currentTime = 90; });
    return { count: animations.length, opacity: Number(getComputedStyle(element).opacity), width: element.offsetWidth,
      properties: animations.map((animation) => animation.transitionProperty), duration: animations.map((animation) => animation.effect.getTiming().duration) };
  });
  assert(motion.count > 0 && motion.opacity > 0 && motion.opacity < 1, "Popover must visibly interpolate");
  assert(motion.properties.every((property) => ["opacity", "translate"].includes(property)), "Popover must avoid layout/paint animation");
  assert(motion.duration.every((duration) => duration <= 200), "Feedback must finish within 200 ms");
  assert.equal(motion.width, 300, "Motion must not change measured popup width");
  await finish("#menu");
  await menu.evaluate((element) => { element.dataset.open = "false"; element.inert = true; });
  assert(await menu.evaluate((element) => element.getAnimations().length > 0), "Closing must animate too");
  await finish("#menu");
  assert.equal(await menu.evaluate((element) => getComputedStyle(element).opacity), "0");

  await drawer.evaluate((element) => { element.hidden = false; element.inert = false; });
  assert(await drawer.evaluate((element) => element.getAnimations().length > 0), "Drawer must enter from hidden");
  await finish("#drawer");
  await drawer.evaluate((element) => { element.hidden = true; element.inert = true; });
  assert(await drawer.evaluate((element) => element.inert && getComputedStyle(element).display !== "none"), "Closed drawer must be inert during its visual exit");
  await finish("#drawer");
  assert.equal(await drawer.evaluate((element) => getComputedStyle(element).display), "none");

  const thumb = page.locator(".toggleThumb");
  const before = await thumb.evaluate((element) => element.getBoundingClientRect().left);
  await page.locator("#switch").evaluate((element) => element.setAttribute("aria-checked", "true"));
  const travel = await thumb.evaluate((element) => {
    const animations = element.getAnimations();
    animations.forEach((animation) => { animation.pause(); animation.currentTime = 60; });
    return { moving: animations.some((animation) => animation.transitionProperty === "transform"), left: element.getBoundingClientRect().left };
  });
  assert(travel.moving && travel.left > before && travel.left < before + 14, "Switch thumb must slide between its endpoints");
  await finish(".toggleThumb");
  assert.equal(Math.round(await thumb.evaluate((element) => element.getBoundingClientRect().left) - before), 14);
  await page.locator("#hint-target").hover();
  assert.equal(await page.locator(".hint").evaluate((element) => getComputedStyle(element).transitionDelay), "0.18s");
  await page.keyboard.press("Tab");
  assert(await page.locator("#hint-target").evaluate((element) => element.matches(":focus-visible")));
  assert.equal(await page.locator(".hint").evaluate((element) => getComputedStyle(element).transitionDelay), "0s");
  assert.equal(await page.locator("#tooltip").evaluate((element) => getComputedStyle(element).transitionProperty), "all");
  assert.equal(await page.locator("#tooltip").evaluate((element) => getComputedStyle(element).transitionDuration), "0s");
  assert.equal(await page.locator("#eye").evaluate((element) => element.getBoundingClientRect().width), 28);
  assert.equal(await page.locator("#eye").evaluate((element) => getComputedStyle(element).color), await page.locator("#muted").evaluate((element) => getComputedStyle(element).color));
  assert(await page.locator("#loading svg").evaluate((element) => element.getAnimations().length > 0));
  await page.locator("#loading").evaluate((element) => { element.inert = true; });
  assert.equal(await page.locator("#loading svg").evaluate((element) => element.getAnimations().length), 0);

  await page.locator("main").evaluate((element) => {
    element.insertAdjacentHTML("beforeend", `<div id="workspace-check">
      <section id="workspace" class="workspace" data-motion="panel" style="inset:auto;position:relative;transform:none;width:300px;height:80px">Workspace surface</section>
      <div id="viewer" class="viewer" data-motion="fade" hidden inert style="position:relative;inset:auto">Viewer controls</div>
      <div id="map-tools" class="mapTools" data-motion="fade" hidden inert>Map tools</div></div>`);
  });
  await finish("#workspace");
  for (let visit = 0; visit < 3; visit += 1) {
    const reveal = await page.locator("#workspace-check").evaluate((element) => {
      const workspace = element.querySelector("#workspace"), viewer = element.querySelector("#viewer"), tools = element.querySelector("#map-tools");
      workspace.hidden = true; workspace.inert = true;
      viewer.hidden = false; viewer.inert = false; tools.hidden = false; tools.inert = false;
      return [workspace, viewer, tools].map((surface) => {
        const animations = surface.getAnimations();
        animations.forEach((animation) => { animation.pause(); animation.currentTime = 60; });
        return { count: animations.length, opacity: Number(getComputedStyle(surface).opacity), display: getComputedStyle(surface).display,
          translate: getComputedStyle(surface).translate, duration: animations.map((animation) => animation.effect.getTiming().duration) };
      });
    });
    assert(reveal.every((sample) => sample.count > 0 && sample.opacity > 0 && sample.opacity < 1), "Every return must animate the outgoing workspace and persistent viewer controls");
    assert(reveal.every((sample) => sample.duration.every((duration) => duration <= 200)), "Workspace navigation must finish within 200 ms");
    assert.notEqual(reveal[0].display, "none", "Outgoing workspace must remain drawn through its exit");
    assert.equal(reveal[0].translate, "0px", "Workspace exit must not move or resize its contents");
    for (const selector of ["#workspace", "#viewer", "#map-tools"]) await finish(selector);
    assert.equal(await page.locator("#workspace").evaluate((element) => getComputedStyle(element).display), "none");
    await page.locator("#workspace-check").evaluate((element) => {
      for (const surface of element.querySelectorAll("#viewer, #map-tools")) { surface.hidden = true; surface.inert = true; getComputedStyle(surface).display; }
      const workspace = element.querySelector("#workspace"); workspace.hidden = false; workspace.inert = false;
    });
    await finish("#workspace");
  }

  // Isolated layout grid, with no map runtime, application navigation, or network.
  await page.evaluate(() => {
    const fixture = document.createElement("div");
    fixture.id = "layout-check";
    fixture.style.cssText = "position:absolute;right:32px;top:190px;width:280px;height:220px";
    fixture.innerHTML = '<div id="panel-grid" class="grid" data-layout="1"><div style="background:var(--surface-1)">Panel 1</div></div><button id="layout-control" style="position:absolute;right:8px;top:8px;z-index:1">Layout control</button>';
    fixture.querySelector("button").addEventListener("click", (event) => { event.currentTarget.dataset.clicked = "true"; });
    document.body.append(fixture);
  });
  for (const fallback of [false, true]) for (const layout of [2, 4, 2, 1]) {
    const animations = await page.locator("#panel-grid").evaluate(async (grid, { layout, fallback }) => {
      const update = () => {
        grid.dataset.layout = String(layout);
        while (grid.children.length > layout) grid.lastElementChild.remove();
        while (grid.children.length < layout) {
          const cell = document.createElement("div");
          cell.textContent = `Panel ${grid.children.length + 1}`;
          cell.style.background = grid.children.length % 2 ? "var(--accent-subtle)" : "var(--surface-1)";
          grid.append(cell);
        }
      };
      if (fallback) {
        update();
        grid.removeAttribute("data-motion");
        grid.getAnimations();
        grid.dataset.motion = "fade";
      } else await grid.startViewTransition(update).ready;
      return grid.getAnimations({ subtree: true }).map((animation) => {
        animation.pause(); animation.currentTime = 90;
        return { duration: animation.effect.getTiming().duration, progress: animation.effect.getComputedTiming().progress,
          frames: animation.effect.getKeyframes().map((frame) => frame.opacity).filter((value) => value !== undefined) };
      });
    }, { layout, fallback });
    assert(animations.some(({ frames, progress }) => frames.length > 1 && progress > 0 && progress < 1), `Panel changes must visibly fade (fallback=${fallback})`);
    assert(animations.every(({ duration }) => duration === 180), "Panel transitions must use the 180 ms motion token");
    await page.locator("#layout-control").click();
    assert.equal(await page.locator("#layout-control").getAttribute("data-clicked"), "true", "Chrome must remain interactive during the map transition");
    await page.locator("#panel-grid").evaluate((grid) => { grid.getAnimations({ subtree: true }).forEach((animation) => animation.finish()); });
    await page.locator("#layout-control").evaluate((button) => { delete button.dataset.clicked; });
  }
  await page.locator("#layout-check").evaluate((fixture) => { fixture.remove(); });

  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.locator("#workspace-check").evaluate((element) => {
    for (const surface of element.children) { surface.hidden = false; surface.inert = false; }
  });
  await drawer.evaluate((element) => { element.hidden = false; element.inert = false; });
  await menu.evaluate((element) => { element.dataset.open = "true"; element.inert = false; });
  await page.evaluate(() => new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done))));
  assert.equal(await menu.evaluate((element) => getComputedStyle(element).opacity), "1");
  assert.equal(await page.locator(".hint").evaluate((element) => getComputedStyle(element).transitionDelay), "0s");
  assert.equal(await page.locator("#loading svg").evaluate((element) => element.getAnimations().length), 0);
  const running = await page.evaluate(() => document.getAnimations().filter((animation) => animation.playState === "running").map((animation) => ({
    target: animation.effect.target.outerHTML.slice(0, 120), name: animation.animationName ?? animation.transitionProperty, timing: animation.effect.getTiming(),
  })));
  assert.deepEqual(running, [], "Reduced motion must leave no running transitions or loops");
  assert.equal(await page.locator("#workspace-check").evaluate((element) => element.getAnimations({ subtree: true }).length), 0, "Reduced motion must reveal the viewer immediately");
  await page.locator("#workspace-check").evaluate((element) => { element.remove(); });
  mkdirSync(resolve(root, "test-results", "motion"), { recursive: true });
  for (const theme of ["dark", "light"]) {
    await page.evaluate((theme) => { document.documentElement.dataset.theme = theme; }, theme);
    await page.screenshot({ path: resolve(root, "test-results", "motion", `${theme}.png`) });
  }
  console.log("Motion checks passed: entry, exit, repeated viewer returns, panel crossfades and CSS fallback, interactive layout controls, display, toggle travel, hints, immediate values, compact eye, hidden loaders, reduced motion.");
} finally {
  await browser.close();
}
