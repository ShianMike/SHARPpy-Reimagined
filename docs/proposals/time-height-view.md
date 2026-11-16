# Time-height view at a selected location

Status: saved feature proposal. Not part of the sounding animation and export implementation.

## In simple words

A sounding is a picture of the air above one place at one time. A time-height
view puts many forecast hours next to one another.

- Left to right: time, such as 6 AM, 9 AM, noon and 3 PM.
- Bottom to top: height above the ground.
- Colors: a selected quantity, such as temperature or humidity.
- Wind barbs: how the wind changes with height and time.

For a winter event, you could see a layer of above-freezing air move in above a
cold surface layer. You could also follow the dendritic growth zone, moisture
and rising air through the day. This helps show when the vertical profile
changes; precipitation type guidance remains separate and can disagree.

Selecting a column would open the existing Severe or Winter sounding for that
same location, model run and forecast hour.

## Proposed first version

Use one deterministic model, one location and one model run. Start with
temperature shading, the 0 °C contour, humidity, wind barbs, and an optional
dendritic growth zone overlay. Add omega when the model actually supplies it.
Let the user switch between pressure and height above ground, inspect the exact
source values, and export the chart as a PNG.

The chart must use the same forecast-hour identity as the map and sounding.
Missing hours and levels must remain visibly missing. Do not blend across data
gaps, invent vertical resolution, mix model runs, or silently substitute another
provider. Derived quantities and interpolation belong in the Rust backend.

## Later additions

- Hovering an hour highlights its matching map time.
- A draggable time cursor opens the corresponding sounding.
- Optional wet-bulb and melting/refreezing overlays.
- Share the existing units, palette conventions and source/valid-time labels.

## Reference

ECMWF Metview includes time-height examples of wind and humidity:
https://metview.readthedocs.io/en/latest/gen_files/gallery/index.html
