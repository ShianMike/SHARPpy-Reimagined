"""Browser hit targets use the same transforms and edits as the Qt viewer."""
from __future__ import annotations

import logging
from copy import deepcopy

from qtpy.QtCore import QPoint

from sharpmod.bridge.params import num


def apply_edits(collection, edits):
    prof = collection.getHighlightedProf()
    arrays = {}
    for edit in edits:
        index = edit["index"]
        if index < 0:
            continue
        if index >= len(prof.pres):
            raise ValueError("The edited level does not exist in this sounding.")
        for key, value in edit["values"].items():
            if num(getattr(prof, key)[index]) is None:
                raise ValueError("Missing observations cannot be edited.")
            arrays.setdefault(key, getattr(prof, key).copy())[index] = value
    if arrays:
        temperature = arrays.get("tmpc", prof.tmpc)
        moisture = arrays.get("dwpc", prof.dwpc)
        changed = [edit["index"] for edit in edits if edit["index"] >= 0
                   and set(edit["values"]) & {"tmpc", "dwpc"}]
        if changed and (moisture[changed] > temperature[changed]).any():
            raise ValueError("Dewpoint cannot exceed temperature.")
        collection.modify(-999, **arrays)
    for edit in edits:
        if edit["index"] < 0:
            values = edit["values"]
            if set(values) != {"u", "v"}:
                raise ValueError("Storm motion needs both wind components.")
            collection.modifyStormMotion("right" if edit["index"] == -1 else "left",
                                         values["u"], values["v"])


def _origin(widget, root):
    point = widget.mapTo(root, QPoint(0, 0))
    return point.x(), point.y()


def _position(widget, root, x, y):
    ox, oy = _origin(widget, root)
    return (ox + float(x)) / root.width(), (oy + float(y)) / root.height()


def _field(key, label, value, unit):
    return {"key": key, "label": label, "value": float(value), "unit": unit,
            "min": -150 if key in ("tmpc", "dwpc") else -350,
            "max": 65 if key in ("tmpc", "dwpc") else 350}


def targets(sw):
    prof, sound, hodo = sw.default_prof, sw.sound, sw.hodo
    points = []

    def add(target, label, widget, x, y, values):
        if not (0 <= x <= widget.width() and 0 <= y <= widget.height()):
            return
        x, y = _position(widget, sw, x, y)
        if 0 <= x <= 1 and 0 <= y <= 1:
            points.append({"id": target, "label": label, "x": x, "y": y,
                           "values": values})

    for index, pressure in enumerate(prof.pres):
        if num(pressure) is None:
            continue
        for key, label in (("tmpc", "Temperature"), ("dwpc", "Dewpoint")):
            value = num(getattr(prof, key)[index])
            if value is None or not sound.pmin <= pressure <= sound.pmax:
                continue
            x = sound.originx + sound.tmpc_to_pix(value, pressure) / sound.scale
            y = sound.originy + sound.pres_to_pix(pressure) / sound.scale
            add(f"{key}:{index}", f"{label} · {pressure:.0f} hPa", sound, x, y,
                [_field(key, label, value, "°C")])
        u, v, height = num(prof.u[index]), num(prof.v[index]), num(prof.hght[index])
        if u is not None and v is not None and height is not None:
            if height - prof.hght[prof.sfc] <= 12000:
                x, y = hodo.uv_to_pix(u, v)
                add(f"wind:{index}", f"Wind · {pressure:.0f} hPa", hodo, x, y,
                    [_field("u", "U wind", u, "kt"), _field("v", "V wind", v, "kt")])
    for index, label, offset in ((-1, "Right mover", 0), (-2, "Left mover", 2)):
        u, v = (num(item) for item in hodo.srwind[offset:offset + 2])
        if u is not None and v is not None:
            x, y = hodo.uv_to_pix(u, v)
            add(f"storm:{index}", label, hodo, x, y,
                [_field("u", "U wind", u, "kt"), _field("v", "V wind", v, "kt")])
    return points


def inspection(sw):
    """Link pressure levels to native wind positions, including undrawn winds."""
    prof, sound, hodo = sw.default_prof, sw.sound, sw.hodo
    left, top = _position(sound, sw, sound.lpad, sound.tpad)
    right, bottom = _position(sound, sw, sound.brx, sound.bry)
    levels = []
    for index, pressure in enumerate(prof.pres):
        pressure = num(pressure)
        if pressure is None or not sound.pmin <= pressure <= sound.pmax:
            continue
        y = _position(sound, sw, 0, sound.originy + sound.pres_to_pix(pressure) / sound.scale)[1]
        if top <= y <= bottom:
            wind = None
            u, v = num(prof.u[index]), num(prof.v[index])
            if u is not None and v is not None:
                x, wind_y = hodo.uv_to_pix(u, v)
                if 0 <= x <= hodo.width() and 0 <= wind_y <= hodo.height():
                    x, wind_y = _position(hodo, sw, x, wind_y)
                    wind = {"x": x, "y": wind_y}
            levels.append({"index": index, "pressure": pressure, "y": y,
                           "height": num(prof.hght[index]), "tmpc": num(prof.tmpc[index]),
                           "dwpc": num(prof.dwpc[index]), "windDirection": num(prof.wdir[index]),
                           "windSpeed": num(prof.wspd[index]), "hodograph": wind})
    return {"bounds": {"left": left, "top": top, "right": right, "bottom": bottom},
            "levels": sorted(levels, key=lambda level: level["y"], reverse=True)}


def _drag(sw, drag, edits):
    point = next((item for item in targets(sw) if item["id"] == drag["target"]), None)
    if point is None:
        raise ValueError("This point is unavailable for editing.")
    kind, index = drag["target"].split(":")
    index = int(index)
    widget = sw.sound if kind in ("tmpc", "dwpc") else sw.hodo
    ox, oy = _origin(widget, sw)
    x, y = drag["x"] * sw.width() - ox, drag["y"] * sw.height() - oy
    if kind in ("tmpc", "dwpc"):
        pressure = sw.default_prof.pres[index]
        # Keep the pressure level fixed, just as the native horizontal drag does.
        value = widget.pix_to_tmpc((x - widget.originx) * widget.scale,
                                  widget.pres_to_pix(pressure))
        value = min(65, max(-150, float(value)))
        other = num(getattr(sw.default_prof, "dwpc" if kind == "tmpc" else "tmpc")[index])
        if other is not None:
            value = max(value, other) if kind == "tmpc" else min(value, other)
        values = {kind: round(value, 2)}
    else:
        u, v = widget.pix_to_uv(x, y)
        values = {"u": round(min(350, max(-350, float(u))), 2),
                  "v": round(min(350, max(-350, float(v))), 2)}
    previous = next((edit for edit in edits if edit["index"] == index), None)
    if previous is None:
        edits.append({"index": index, "values": values})
    else:
        previous["values"].update(values)
    if index < 0:
        sw.modifyVector("right" if index == -1 else "left", values["u"], values["v"])
    else:
        sw.modifyProf(index, values)


def original_values(prof):
    values = {key: [num(value) for value in getattr(prof, key)]
              for key in ("tmpc", "dwpc", "u", "v")}
    values["storm"] = [num(value) for value in prof.srwind]
    return values


def configure_view(win, view, app, original):
    sw = win.spc_widget
    edits = deepcopy(view["edits"])
    if view.get("drag") is not None:
        _drag(sw, view["drag"], edits)
    if not sw.streamwiseness.setChart(view["chart"]):
        raise ValueError("The selected height chart is unavailable.")
    if view["panel"] == "HEIGHT CHARTS":
        sw.right_inset_ob.hide()
        sw.grid3.addWidget(sw.streamwiseness.companionCharts(sw.text), 0, 4)
    elif sw.right_inset != view["panel"] and not show_sounding_panel(win, view["panel"]):
        raise ValueError("The selected diagnostic panel is unavailable.")
    for _ in range(3):
        app.processEvents()
    points = targets(sw)
    for point in points:
        kind, index = point["id"].split(":")
        index = int(index)
        for field in point["values"]:
            field["original"] = (original["storm"][(0 if index == -1 else 2) + (field["key"] == "v")]
                                 if kind == "storm" else original[field["key"]][index])
    return {"edits": edits, "points": points, "inspection": inspection(sw),
            "selection": {"chart": view["chart"], "panel": view["panel"]}, "options": [
        {"key": "chart", "label": "Height chart", "choices": [
            {"key": key, "label": label.replace("&&", "&")}
            for key, label in sw.streamwiseness.availableCharts()]},
        {"key": "panel", "label": "Diagnostic panel", "choices": [
            {"key": key, "label": label} for key, label in sw.inset_names.items()
            if key != sw.left_inset] + [{"key": "HEIGHT CHARTS", "label": "All height charts"}]},
    ]}


def show_sounding_panel(win, key: str) -> bool:
    """Show the swappable panel named ``key``. Returns whether it changed.

    Drives the vendored swap rather than reaching into the layout, because that
    path does more than move a widget: it re-creates the outgoing panel, persists
    the choice, and turns the matching Skew-T annotation on or off -- the mixing
    height for the fire panel, the dendritic growth zone band for the winter one.
    Those pairings are the reason a separate "mode" concept is unnecessary, and
    bypassing ``swapInset`` would lose them.
    """
    sw = getattr(win, "spc_widget", None)
    if sw is None or not key:
        return False
    if getattr(sw, "right_inset", None) == key:
        return False  # already showing; nothing to do
    try:
        # Rebuilt exactly as a right-click would: the vendored swap reads its
        # target from ``menu_ag``'s checked action and ``inset_to_swap``.
        sw.makeInsetMenu(sw.left_inset, sw.right_inset)
        action = next(
            (item for item in sw.menu_ag.actions() if item.data() == key), None
        )
        if action is None:
            return False
        action.setChecked(True)
        sw.inset_to_swap = "RIGHT"
        sw.swapInset()
        return True
    except Exception:
        logging.getLogger(__name__).exception("panel.swap_failed key=%s", key)
        return False
