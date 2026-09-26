# Code structure

`sharpmod/` keeps only package metadata, application entry points, and a few
paths used directly by the desktop launcher at its root. Production code lives
in packages grouped by responsibility:

| Package | Contents |
| --- | --- |
| `analysis/` | Box sampling, profile metrics, ensembles, and analysis workflows |
| `core/` | Shared runtime helpers |
| `io/` | Sounding and observation file formats |
| `maps/` | Map overlays, geography, layers, and locator data |
| `models/` | Model fields, caches, transfer, and transport |
| `providers/` | HRRR, ECCC, OpenMeteo, radar, satellite, SPC, and observation providers |
| `state/` | Saved locations and session persistence |
| `tools/` | Command-line extraction and maintenance tools |
| `ui/features/` | Application-level GUI modules and feature controllers |
| `ui/picker/`, `ui/maps/`, `ui/analysis/` | Focused picker, map, and analysis interfaces |
| `ui/styles/` | Theme tokens and stylesheet sections |
| `viz/`, `rendering/`, `render_patches/` | Plot widgets, headless image output, and vendored widget patches |
| `sharptab/`, `backends/`, `upstream/` | Meteorological calculations, optimized decoders, and upstream compatibility |

The executable desktop entry point remains `sharpmod/gui.py`. The public
`PickerWindow`, `StationMapWidget`, and `AnalysisWorkspace` imports remain
available from their established paths. Existing top-level imports such as
`sharpmod.gui_maps`, `sharpmod.box_analysis`, and `sharpmod.render` resolve
through `sharpmod/_compat/`; new implementation work belongs in the grouped
packages above.

Keep production Python modules below 1,000 physical lines. Move cohesive
behavior into adjacent modules when a file approaches that limit, and keep
public imports stable through the compatibility layer.

The package list in `pyproject.toml` is explicit. Add any new package directory
there so wheels include it. The compatibility directory is added to the
package's module search path, so the old import names remain available after
installation as well as from a source checkout.
