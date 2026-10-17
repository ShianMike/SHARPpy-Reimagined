# Code structure

`sharpmod/` keeps only package metadata, application entry points, and a few
paths used directly by the desktop launcher at its root. Production code lives
in packages grouped by responsibility:

| Package | Contents |
| --- | --- |
| `analysis/` | Box sampling, profile metrics, ensembles, threshold rules, metric control bounds, and analysis workflows |
| `bridge/` | Headless JSON-lines bridge serving the Tauri frontend |
| `io/` | Sounding and observation file formats |
| `maps/` | Map overlays, geography, layers, and locator data |
| `models/` | Model fields, caches, transfer, and transport |
| `providers/` | HRRR, ECCC, OpenMeteo, radar, satellite, SPC, and observation providers |
| `tools/` | Command-line extraction and maintenance tools |
| `viz/`, `rendering/`, `render_patches/` | Plot widgets, headless image output, and vendored widget patches |
| `sharptab/`, `backends/`, `upstream/` | Meteorological calculations, optimized decoders, and upstream compatibility |

The desktop entry point is the Tauri application in `src-tauri/`, with its
React interface in `frontend/`. `packaging/sharpmod_backend.spec` freezes the
Python bridge and render worker. The legacy Qt interface, `sharpmod-gui` entry
point, `ui/`, `state/` and `core/` packages and GUI import aliases are retired;
their source remains in the
[v2.0.0 Legacy release](https://github.com/ShianMike/SHARPpy-Reimagined/releases/tag/v2.0.0).
Scientific top-level imports such as `sharpmod.box_analysis` and
`sharpmod.render` still resolve through `sharpmod/_compat/`; new implementation
work belongs in the grouped packages above.

Keep production Python modules below 1,000 physical lines. Move cohesive
behavior into adjacent modules when a file approaches that limit, and keep
public imports stable through the compatibility layer.

The package list in `pyproject.toml` is explicit. Add any new package directory
there so wheels include it. The compatibility directory is added to the
package's module search path, so the old import names remain available after
installation as well as from a source checkout.
