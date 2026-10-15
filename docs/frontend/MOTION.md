# Interface motion

Motion should help users follow a change without delaying weather work. The
existing tokens provide 120 ms feedback and 180 ms entrances with the standard
ease-out curve. No animation library or new dependency is required.

| Surface | Behavior |
| --- | --- |
| Buttons, fields, tabs, selected controls | Brief local color transitions; a subtle button press |
| Layer toggles | Thumb translates between the existing track endpoints |
| Forecast, parameter, search, source, calendar and health popovers | Opacity and a small translation on entry and exit |
| Layers / Point drawer | A short horizontal entrance and exit; contents retain their last view while closing |
| Workspace navigation | Workspace surfaces fade in and out; returning to Model Viewer reveals the persistent controls without rebuilding the map |
| Sounding sources, menus and editor | Fade or small entrance on the controls, preserving the scientific image |
| Notices, empty states, disclosures, scales and panel titles | Brief reveal without animating dimensions |
| Map hover cards | Fade once on appearance; position and readings update immediately |
| Loading icon | Spins only while loading in visible, interactive map UI |

Popovers become inert and leave the accessibility tree as soon as they close.
Their DOM remains only until the browser's CSS transitions finish. Reopening
cancels cleanup from the previous close, preserving live content and focus.
The platform animation promises avoid duplicated JavaScript duration timers.
Workspace exits use the same cleanup rule: they become inert immediately and
unmount once their fade completes. Rapid navigation cancels stale cleanup.

Only small controls transition paint properties. Larger surfaces use opacity
and transforms. Discrete `display` transitions keep the drawer visible during
its exit without interpolating width, height, or map layout. Engines without
discrete display support hide it immediately. Entrance effects release their
animation state when complete; no permanent `will-change` layer is requested.

The map canvas, weather frames, palettes, numeric values, pointer coordinates,
log rows and sounding image have no independent decorative animation. Splitting
the map changes its layout immediately so rendering does not repeatedly resize
through an animated grid. Keyboard hints have no hover delay.

Reduced motion removes animations, transitions and delays, including effects
already running when the preference changes. Existing camera tokens also
remain zero-duration under that preference.

## Sources

- [Material 3 motion](https://m3.material.io/styles/motion): functional, restrained motion for utility interfaces.
- [web.dev animation performance](https://web.dev/articles/animations-guide): prefer opacity and transforms; avoid layout animation and premature compositing.
- [MDN starting styles](https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/At-rules/@starting-style): transition newly rendered elements and elements becoming displayed.
- [W3C reduced-motion technique C39](https://www.w3.org/WAI/WCAG22/Techniques/css/C39): honor users who request less interaction motion.

## Checks

Run `npm test`, `npm run build`, and `npm run lint` from `frontend/`.
`node scripts/check-motion.mjs` runs Edge against an isolated offline component
fixture using current source CSS. It checks actual interpolation, durations,
drawer exits, repeated returns to the viewer, switch travel, keyboard hints, immediate pointer values, the
neutral compact eye, hidden loading icons and reduced-motion cancellation.
It saves dark/light screenshots in `frontend/test-results/motion/`.
`Popover.motion.test.tsx` covers completion, interrupted exits, rapid reopening,
focus, zero-motion cleanup and unmounting. `Shell.motion.test.tsx` covers returns
from every workspace, interrupted navigation, cleanup and preservation of the
map instance. These checks do not measure live map
frame rate or replace a packaged Tauri smoke test.
