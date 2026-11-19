"""Browser hit targets use the same transforms and edits as the Qt viewer."""
from __future__ import annotations

import logging
from copy import deepcopy

from qtpy.QtCore import QPoint

from sharpmod.bridge.params import num


def apply_edits(collection, edits):
    from sharpmod_rs import apply_sounding_edits

    return apply_sounding_edits(collection, edits)


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
    from sharpmod_rs import sounding_interactive_geometry

    return sounding_interactive_geometry(sw, False)


def inspection(sw):
    from sharpmod_rs import sounding_interactive_geometry

    return sounding_interactive_geometry(sw, True)


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
    from sharpmod_rs import sounding_original_values

    return sounding_original_values(prof)


def configure_view(win, view, app, original):
    if view.get("mode") == "winter":
        from sharpmod_rs import configure_winter_view
        return configure_winter_view(win, view, app, original)
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
    from sharpmod.backends import get_backend
    bounds = None
    if get_backend().name == "rust":
        import sharpmod_rs
        locator_bounds = getattr(sharpmod_rs, "sounding_locator_bounds", None)
        bounds = locator_bounds(sw) if locator_bounds else None
    return {"edits": edits, "points": points, "inspection": inspection(sw),
            **({"locatorBounds": bounds} if bounds is not None else {}),
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
