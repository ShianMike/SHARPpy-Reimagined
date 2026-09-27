"""Viewer Locator for the sounding viewer."""

from __future__ import annotations

from datetime import datetime
from datetime import timezone
from qtpy.QtWidgets import QApplication
from sharpmod.ui.features.gui_common import _LOGGER
import weakref
from sharpmod import gui_viewer as _api


def _fill_metadata(prof_col, stn_id, model=None, run=None, loc=None) -> None:
    """Fill the metadata the title/header rendering dereferences.

    A faithful copy of the metadata block in :func:`sharpmod.rendering.cli.render`, so
    the interactive window's heading matches the rendered PNG. Never clobbers a
    value the decoder already worked out.
    """
    if model is not None:
        prof_col.setMeta("model", model)
    if run is not None:
        prof_col.setMeta("run", run)
    if loc is not None:
        prof_col.setMeta("loc", loc)

    has = lambda k: k in prof_col._meta  # noqa: E731
    base = (
        prof_col.getMeta("base_time") if has("base_time") else prof_col.getCurrentDate()
    )
    observed = prof_col.getMeta("observed") if has("observed") else True
    if not has("loc"):
        prof_col.setMeta("loc", stn_id)
    if not has("run"):
        prof_col.setMeta("run", base)
    if not has("model"):
        prof_col.setMeta("model", "Archive" if observed else "Model")
    # Match the headless renderer: generic model/coordinate station ids are
    # replaced by the cached CONUS town title before any widget paints.
    _api._render()._resolve_location_title(prof_col, explicit_loc=loc)


def _locator_overlay_point(prof_col):
    """Return the sounding's ``(lat, lon)`` for overlay coverage checks."""
    lat = lon = None
    for key, target in (("lat", "lat"), ("lon", "lon")):
        try:
            value = prof_col.getMeta(key)
        except (AttributeError, KeyError, TypeError):
            value = None
        if key == "lat":
            lat = value
        else:
            lon = value
    if lat is None or lon is None:
        profile = None
        try:
            profile = prof_col.getHighlightedProf()
        except (AttributeError, IndexError, TypeError):
            profile = None
        lat = lat if lat is not None else getattr(profile, "latitude", None)
        lon = lon if lon is not None else getattr(profile, "longitude", None)
    return lat, lon


def _controller_overlay_product(controller):
    """Return the overlay product the picker currently has selected.

    Duck-typed rather than imported so the viewer keeps no dependency on the
    picker. ``None`` means "no preference", which resolves to the categorical
    outlook.
    """
    getter = getattr(controller, "selected_overlay_product", None)
    if not callable(getter):
        return None
    try:
        return getter()
    except Exception:  # noqa: BLE001 - a preference is not worth an exception
        return None


def _controller_model_field(controller):
    """Return the gridded field image the picker is currently drawing.

    Duck-typed for the same reason :func:`_controller_overlay_product` is.
    ``None`` means there is no field on the map, which is also what a caller that
    is not the picker will answer.
    """
    getter = getattr(controller, "selected_model_field", None)
    if not callable(getter):
        return None
    try:
        return getter()
    except Exception:  # noqa: BLE001 - a preference is not worth an exception
        return None


def _controller_locator_spec(controller):
    """Return the picker's explicit locator-overlay selection, if any."""
    getter = getattr(controller, "selected_locator_spec", None)
    if not callable(getter):
        return None
    try:
        return getter()
    except Exception:  # noqa: BLE001 - a preference is not worth an exception
        return None


def _collection_locator_spec(collection):
    """Return a selection persisted with a collection, if one exists."""
    from sharpmod.maps.locator_overlay import SELECTION_META_KEY

    try:
        value = collection.getMeta(SELECTION_META_KEY)
    except Exception:
        value = None
    return value if isinstance(value, str) else None


def attach_locator_model_field(win, prof_col, controller) -> bool:
    """Carry the picker's gridded field onto this sounding's locator inset.

    Attached directly rather than through a worker, because unlike the convective
    outlook the image is already in memory -- the picker fetched it to draw the
    map the user was reading. Refetching it here would spend a GRIB subset to
    reproduce a frame we are holding.

    Returns whether anything was attached, and is silent otherwise: the field is
    context on a thumbnail, so its absence is not worth reporting.
    """
    raster = _api._controller_model_field(controller)
    if raster is None:
        return False
    try:
        from sharpmod.maps.map_overlays import attach_locator_overlay
        from sharpmod.maps.locator_presentation import set_overlay_status

        attach_locator_overlay(prof_col, raster, key=raster.key)
        set_overlay_status(
            prof_col,
            raster.key,
            "available",
            family="hrrr",
            product=str(getattr(raster, "short_name", "") or ""),
            detail="Field already loaded on the main map",
        )
    except Exception:  # noqa: BLE001 - the field is optional
        return False
    return True


def _repaint_locator_insets(win) -> None:
    """Force the hodographs to re-run their render pass.

    The locator inset is painted during the hodograph's cached-bitmap pass, so a
    layer that arrives after the window is up only appears once that pass runs
    again. Mirrors the clear/plot/update sequence
    :func:`sharpmod.viz.SPCWindow._refresh_mounted_products` already uses.
    """
    try:
        from sharppy.viz.hodo import plotHodo
    except Exception:  # pragma: no cover - vendored module always present
        return
    for hodo in win.findChildren(plotHodo):
        try:
            hodo.clearData()
            hodo.plotData()
            hodo.update()
        except Exception:  # noqa: BLE001 - a repaint failure is not fatal
            continue


def _locator_selection(spec):
    """Return the families the user asked for, or ``None`` for "not stated".

    ``None`` is distinct from an empty selection: a caller that says nothing gets
    the historical behaviour of showing whatever the picker had, while a caller
    that explicitly selects nothing gets a bare inset.
    """
    if spec is None:
        return None
    try:
        from sharpmod.maps import locator_overlay

        return {item.family: item for item in locator_overlay.parse(spec)}
    except Exception:  # noqa: BLE001 - a stored selection is not trusted
        return {}


def _track_locator_worker(win, worker) -> None:
    """Retain one application-owned worker and cancel it with its viewer."""
    window = weakref.ref(win)
    tracked = getattr(win, "_sharpmod_overlay_workers", None)
    if tracked is None:
        tracked = []
        win._sharpmod_overlay_workers = tracked
    tracked.append(worker)

    def _finished() -> None:
        host = window()
        if host is not None:
            remaining = getattr(host, "_sharpmod_overlay_workers", None)
            if isinstance(remaining, list):
                remaining[:] = [item for item in remaining if item is not worker]
        worker.deleteLater()

    worker.finished.connect(_finished)
    try:
        win.destroyed.connect(worker.requestInterruption)
    except (AttributeError, RuntimeError):
        pass
    worker.start()


def _collection_meta(collection, key, default=None):
    try:
        value = collection.getMeta(key)
    except Exception:
        value = default
    return default if value is None else value


def _risk_selection_for_product(selection, product, valid_time=None):
    """Point a bare ``risk`` selection at the hazard the picker has selected.

    The locator control only offers families, so it can never say ``risk:torn``
    -- it produces a bare ``risk``, which :func:`sharpmod.maps.locator_overlay.fetch`
    resolves to the categorical outlook. Opening a sounding while looking at the
    tornado, wind, or hail probability therefore silently switched the inset back
    to categorical.

    The substitution applies to every outlook day, and to every hazard the map
    can be showing. Two cases deliberately keep the categorical fallback:

    * a product named in the spec wins, because that is an explicit request from
      the command line or a restored session and must outrank the live map; and
    * a product SPC publishes nothing for at this valid time is left alone.
      Asked in general -- rather than by hardcoding which product covers which
      day -- because the answer is not a fixed rule: the hazard probabilities go
      unpublished from Day 3 out, and nothing at all is published beyond Day 3 or
      before the archive begins. Substituting regardless would trade a usable
      categorical outlook for an empty inset.
    """
    from sharpmod.maps import locator_overlay

    risk = selection.get(locator_overlay.FAMILY_RISK)
    if risk is None or risk.product is not None or not product:
        return selection
    from sharpmod.providers import spc_outlook

    if not spc_outlook.product_publishes(valid_time, product):
        return selection
    resolved = dict(selection)
    resolved[locator_overlay.FAMILY_RISK] = locator_overlay.Selection(
        locator_overlay.FAMILY_RISK, str(product)
    )
    return resolved


def _start_explicit_locator_fetch(
    win, prof_col, selection, controller, product=None
) -> None:
    """Fetch every explicitly selected family on one cooperative worker."""
    from sharpmod.maps import locator_overlay
    from sharpmod.ui.features.gui_locator_fetch import LocatorOverlayWorker
    from sharpmod.maps.locator_presentation import set_overlay_status
    from sharpmod.maps.map_overlays import attach_locator_overlay

    valid = None
    try:
        valid = prof_col.getCurrentDate()
    except (AttributeError, KeyError, TypeError, IndexError):
        pass
    if isinstance(valid, datetime):
        valid = (
            valid.replace(tzinfo=timezone.utc)
            if valid.tzinfo is None
            else valid.astimezone(timezone.utc)
        )
    # After ``valid`` is normalized: whether a hazard can be honoured depends on
    # which outlook day this sounding falls in.
    selection = _api._risk_selection_for_product(selection, product, valid)
    lat, lon = _api._locator_overlay_point(prof_col)

    # An explicit selection replaces prior context. This also makes "none"
    # deterministic when a collection is reused in an existing viewer.
    for key in locator_overlay.OVERLAY_KEYS.values():
        attach_locator_overlay(prof_col, None, key=key)
        set_overlay_status(prof_col, key, "not-selected")

    try:
        requested = locator_overlay.resolve(
            selection.values(), lat=lat, lon=lon, valid_time=valid)
    except Exception:  # noqa: BLE001 - the worker remains authoritative
        requested = tuple(selection.values())
    for item in requested:
        set_overlay_status(
            prof_col,
            item.key,
            "loading",
            family=item.family,
            product=item.product or "",
            detail="Loading locator context",
        )

    pending = list(selection.values())
    field = selection.get(locator_overlay.FAMILY_HRRR)
    raster = _api._controller_model_field(controller) if field is not None else None
    if raster is not None:
        raster_product = str(getattr(raster, "short_name", "") or "")
        raster_valid = getattr(raster, "valid_time", None)
        product_matches = field.product is None or raster_product == field.product
        time_matches = valid is None or raster_valid is None or raster_valid == valid
        if product_matches and time_matches:
            attach_locator_overlay(prof_col, raster, key=field.key)
            set_overlay_status(
                prof_col,
                field.key,
                "available",
                family=field.family,
                product=field.product or raster_product,
                detail="Field already loaded on the main map",
            )
            pending = [item for item in pending if item.family != field.family]
            _api._repaint_locator_insets(win)

    if not pending:
        return
    run = _api._collection_meta(prof_col, "run")
    if isinstance(run, datetime):
        run = (
            run.replace(tzinfo=timezone.utc)
            if run.tzinfo is None
            else run.astimezone(timezone.utc)
        )
    fxx = _api._collection_meta(prof_col, "fxx")
    if fxx is None and isinstance(run, datetime) and isinstance(valid, datetime):
        fxx = int(round((valid - run).total_seconds() / 3600.0))
    try:
        fxx = None if fxx is None else int(fxx)
    except (TypeError, ValueError):
        fxx = None

    collection = weakref.ref(prof_col)
    window = weakref.ref(win)

    def _on_loaded(key, layer) -> None:
        target = collection()
        host = window()
        if target is None or host is None:
            return
        try:
            attach_locator_overlay(target, layer, key=str(key))
        except Exception:  # noqa: BLE001 - optional visual context
            return
        try:
            set_overlay_status(
                target,
                str(key),
                "available" if layer is not None else "unavailable",
                detail=("Loaded from provider" if layer is not None
                        else "Provider returned no usable locator layer"),
            )
        except Exception:  # noqa: BLE001 - status is explanatory only
            pass
        if layer is not None:
            _api._repaint_locator_insets(host)
        else:
            dialog = getattr(host, "_sharpmod_locator_dialog", None)
            if dialog is not None:
                try:
                    dialog.refresh()
                except (AttributeError, RuntimeError):
                    pass

    worker = LocatorOverlayWorker(
        pending,
        lat=lat,
        lon=lon,
        valid_time=valid,
        run=run,
        fxx=fxx,
        parent=QApplication.instance(),
    )
    worker.loaded.connect(_on_loaded)
    _api._track_locator_worker(win, worker)


def start_locator_overlay_fetch(
    win, prof_col, *, product=None, controller=None, spec=None
) -> None:
    """Fetch this sounding's map overlay and attach it for the locator inset.

    Runs on a worker thread and attaches the result to the profile collection,
    because the inset is painted from inside a vendored render pass that must
    never touch the network -- an unreachable service would otherwise stall a
    hodograph repaint.

    ``spec`` is a :mod:`sharpmod.maps.locator_overlay` selection string. Omitting it
    keeps the previous behaviour of carrying whatever the picker was drawing,
    which is what a caller with no control of its own wants.

    Silent by design. The overlay is context on a locator thumbnail, so a
    missing outlook, a sounding outside the forecast area, or a failed request
    all simply leave the inset as it was rather than reporting anything.
    """
    if product is None:
        # Resolved here rather than trusted from the caller so every entry point
        # -- viewer, reopened session, CLI -- lands on the same hazard.
        product = _api._controller_overlay_product(controller)

    selection = _api._locator_selection(spec)
    if spec is not None:
        try:
            from sharpmod.maps.locator_overlay import SELECTION_META_KEY

            # Deliberately the spec as selected, before the live hazard is folded
            # in: persisting the resolved hazard would pin a reopened collection
            # to whatever was on the map the first time it was opened.
            normalized = ",".join(item.spec() for item in selection.values())
            prof_col.setMeta(SELECTION_META_KEY, normalized or "none")
        except Exception:  # noqa: BLE001 - optional provenance only
            pass

    if selection is not None:
        try:
            # ``product`` has to travel with the selection: this is the branch the
            # GUI always takes, because a picker with a locator control returns a
            # spec string ("risk", "none", ...) rather than None.
            _api._start_explicit_locator_fetch(
                win, prof_col, selection, controller, product
            )
        except Exception:  # noqa: BLE001 - overlays cannot block a sounding
            _LOGGER.debug("locator_overlay.selection_failed", exc_info=True)
        return

    # The gridded field first: it needs no network, and it must reach the inset
    # even for a sounding the convective outlook does not cover -- the outlook is
    # CONUS-and-issued, a model field is neither.
    if selection is None or "hrrr" in selection:
        if _api.attach_locator_model_field(win, prof_col, controller):
            _api._repaint_locator_insets(win)

    try:
        from sharpmod.providers import spc_outlook
        from sharpmod.ui.features.gui_workers import _SpcOutlookWorker
        from sharpmod.maps.locator_presentation import set_overlay_status
        from sharpmod.maps.map_overlays import attach_locator_overlay
    except Exception:  # noqa: BLE001 - the overlay is optional
        return

    # No spec-vs-product merge here: this branch only runs when ``spec`` was
    # None, so ``selection`` is None too and there is nothing to merge. The
    # explicit branch above owns that reconciliation.
    valid = None
    try:
        valid = prof_col.getCurrentDate()
    except (AttributeError, KeyError, TypeError, IndexError):
        valid = None
    if not isinstance(valid, datetime):
        try:
            set_overlay_status(
                prof_col, spc_outlook.OVERLAY_KEY, "unavailable",
                family="risk", product=product or spc_outlook.DEFAULT_PRODUCT,
                detail="Sounding valid time is unavailable",
            )
        except Exception:  # noqa: BLE001
            pass
        return
    if valid.tzinfo is None:
        valid = valid.replace(tzinfo=timezone.utc)
    else:
        valid = valid.astimezone(timezone.utc)

    lat, lon = _api._locator_overlay_point(prof_col)
    if not spc_outlook.covers_location(lat, lon):
        try:
            set_overlay_status(
                prof_col, spc_outlook.OVERLAY_KEY, "unavailable",
                family="risk", product=product or spc_outlook.DEFAULT_PRODUCT,
                detail="Outside SPC outlook coverage",
            )
        except Exception:  # noqa: BLE001
            pass
        return

    try:
        set_overlay_status(
            prof_col, spc_outlook.OVERLAY_KEY, "loading",
            family="risk", product=product or spc_outlook.DEFAULT_PRODUCT,
            detail="Loading hazard context",
        )
    except Exception:  # noqa: BLE001
        pass

    collection = weakref.ref(prof_col)
    window = weakref.ref(win)

    def _on_loaded(_token, _valid_time, layer) -> None:
        target = collection()
        host = window()
        if target is None or host is None:
            return
        try:
            attach_locator_overlay(target, layer, key=spc_outlook.OVERLAY_KEY)
        except Exception:  # noqa: BLE001
            return
        try:
            set_overlay_status(
                target,
                spc_outlook.OVERLAY_KEY,
                "available" if layer is not None else "unavailable",
                family="risk",
                product=product or spc_outlook.DEFAULT_PRODUCT,
                detail=("Loaded from NOAA/NWS SPC" if layer is not None
                        else "SPC returned no usable outlook for this point/time"),
            )
        except Exception:  # noqa: BLE001
            pass
        if layer:
            _api._repaint_locator_insets(host)
        else:
            dialog = getattr(host, "_sharpmod_locator_dialog", None)
            if dialog is not None:
                try:
                    dialog.refresh()
                except (AttributeError, RuntimeError):
                    pass

    # Parented to the application rather than the window. Viewers are closed
    # with WA_DeleteOnClose and their `destroyed` signal fires after the native
    # tree is already gone, which is too late to interrupt a child thread -- Qt
    # would warn that a running thread was destroyed. The application outlives
    # the fetch, and the weak references above mean a result arriving after the
    # viewer closed is simply dropped.
    app_parent = QApplication.instance()
    worker = _SpcOutlookWorker(
        valid, 0, parent=app_parent, product=product or spc_outlook.DEFAULT_PRODUCT
    )
    worker.loaded.connect(_on_loaded)

    _api._track_locator_worker(win, worker)
