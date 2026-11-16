# Use SHARPpy sounding images in another application

Use `sharpmod-render` as a headless command in your own worker or build step.
It accepts a local sounding file and writes a PNG ready to serve, attach, or
embed. Either render a bundled example or pass a profile from the
[observed or model extraction commands](USAGE.md).

In the SHARPpy Reimagined source checkout, install into an active virtual
environment. Keep `sharpmod` and `sharpmod-rs` at the same version. Install a
matching native wheel for your Python and platform, or build it from this
checkout with `sharpmod-rust-sync` and the toolchain in the
[Rust setup guide](RUST_BACKEND.md#build-the-local-rust-extension).

```shell
python -m pip install -e ".[render,rust-build]"
python scripts/install_sharppy_compat.py
sharpmod-rust-sync
sharpmod-rust-sync --check
sharpmod-render --version
```

Render a Severe or Winter image. `--out` and the existing positional output
path are alternatives. Each invocation accepts one input and writes one image.

```shell
sharpmod-render examples/soundings/14061619.OAX --out severe.png --json
sharpmod-render examples/soundings/14061619.OAX --out winter.png --sounding-mode winter --uhd --json
```

The default is HD Severe. `--uhd` is larger; `--lossless` uses the original
plot dimensions. All three modes write PNGs. The CLI omits the locator map
unless you request an overlay, and Winter omits parcels.

The desktop and browser viewer can compose a locator from the currently
displayed Model Viewer map, including its OpenStreetMap basemap and weather
layers. That composition uses the visible map context and is included in
the viewer's PNG save/copy actions. The inset zooms to 6.4 times the displayed
map scale and renders the crop at a density based on the selected image size.
It includes the point marker and parameter scale, and shows the location on
the sounding header above the map. Standalone CLI workers have no displayed
map context; use the explicit locator-overlay option for those jobs.

With `--json`, a successful invocation exits `0` and writes one JSON record to
stdout. The record has `schemaVersion` (`1`), the absolute `path`, `contentType`
(`image/png`), `soundingMode`, `width`, `height`, and `byteLength`. For a failed
render, check the nonzero exit code and read the diagnostic on stderr. A failed
render does not replace an existing PNG. Qt diagnostics may also appear on
stderr during a successful render.

For example, from Node.js, pass arguments without a shell and serve the
completed file with the returned content type:

```js
import { execFile } from "node:child_process";
import { readFile } from "node:fs/promises";
import { promisify } from "node:util";

const run = promisify(execFile);
const { stdout } = await run("sharpmod-render", [
  inputPath, "--out", jobOutputPath, "--sounding-mode", "winter", "--json",
], { encoding: "utf8", timeout: 180_000, windowsHide: true });
const report = JSON.parse(stdout);
const png = await readFile(report.path);
const response = new Response(png, { headers: { "Content-Type": report.contentType } });
```

Give each job a separate directory and choose its input and output paths in
your worker. This avoids collisions when jobs run at the same time. Keep the
renderer and its desktop bridge inside your process boundary; the app's local
bridge is an internal interface. Keep the label and source attribution on the
sounding when you embed it. For in-process Python use, see the
[rendering API](USAGE.md#5-rendering-soundings-sharpmod-render).
