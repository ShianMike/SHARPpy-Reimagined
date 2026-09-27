"""Fast-tier calculations for fields sampled across a geographic box.

Backend-supported values are batched where possible, while the ``box_analysis`` facade
keeps parameter metadata and coordinates fast, summary, and composite results."""

from __future__ import annotations

from sharpmod.analysis.box_sounding import BoxSamplePlan
from types import SimpleNamespace
from typing import Callable
from typing import Mapping
from typing import Sequence
import math
from sharpmod.analysis import box_analysis as _api


def _fast_values(prof, *, include_effective_stp=False) -> dict[str, float]:
    """Return the native-backend tier for one profile.

    Every quantity here comes from the shared :mod:`sharpmod.backends`
    contract, so it agrees with the Skew-T and hodograph drawn for the same
    point and costs well under a millisecond.
    """
    from sharpmod import backends

    pres, hght, tmpc, dwpc, wdir, wspd = _api._profile_columns(prof)
    surface_index = _api._profile_surface_index(prof, tmpc)
    u, v = backends.wind_to_components(wdir, wspd, missing=-9999.0)
    values: dict[str, float] = {}

    try:
        thermodynamics = backends._profile_thermodynamics_buffers(
            pres,
            hght,
            tmpc,
            dwpc,
            sfc=surface_index,
        )
        parcels = thermodynamics.parcels
        convective = thermodynamics.convective
        downdraft = thermodynamics.downdraft
    except Exception:
        # Preserve the prior partial-result behavior for an older or failing
        # backend while keeping the supported path to one profile preparation.
        parcels = backends.profile_parcels(
            pres, hght, tmpc, dwpc, sfc=surface_index,
        )
        try:
            convective = backends.profile_convective_parcels(
                pres, hght, tmpc, dwpc, sfc=surface_index,
            )
        except Exception:
            convective = None
        try:
            downdraft = backends.profile_dcape(
                pres, hght, tmpc, dwpc, sfc=surface_index,
            )
        except Exception:
            downdraft = None
    surface = parcels.surface
    mixed = parcels.mixed_layer
    unstable = parcels.most_unstable
    for key, raw in (
        ("sbcape", surface.cape),
        ("sbcin", surface.cin),
        ("sb_lcl", surface.lcl_height),
        ("mlcape", mixed.cape),
        ("mlcin", mixed.cin),
        ("ml_lcl", mixed.lcl_height),
        ("ml_lfc", mixed.lfc_height),
        ("mucape", unstable.cape),
        ("mucin", unstable.cin),
        ("mucape_3km", unstable.cape_3km),
        ("mucape_6km", unstable.cape_6km),
        ("mu_el", unstable.el_height),
    ):
        number = _api._finite(raw)
        if number is not None:
            values[key] = number

    # The effective inflow layer needs the convective workspace rather than the
    # three-parcel one, and it is what makes an effective-layer field possible
    # without the pure-Python oracle.
    if convective is not None:
        effective = convective.effective.diagnostics
        for key, raw in (
            ("eff_cape", effective.cape),
            ("eff_cin", effective.cin),
            ("eff_inflow_base", convective.effective_bottom_pressure),
            ("eff_inflow_top", convective.effective_top_pressure),
        ):
            number = _api._finite(raw)
            if number is not None:
                values[key] = number

    kinematics = backends.profile_kinematics(
        pres,
        hght,
        u,
        v,
        _api.KINEMATIC_LAYER_TOPS,
        sfc=surface_index,
    )
    for top, shear_key, srh_key in (
        (1000.0, "shear_1km", "srh_1km"),
        (3000.0, "shear_3km", "srh_3km"),
        (6000.0, "shear_6km", None),
        (8000.0, "shear_8km", None),
    ):
        layer = kinematics.layer(top)
        if layer is None:
            continue
        shear = _api._magnitude(layer.height_shear_u, layer.height_shear_v)
        if shear is not None:
            values[shear_key] = shear
        if srh_key is not None:
            srh = _api._finite(layer.srh_total)
            if srh is not None:
                values[srh_key] = srh
        if top == 6000.0:
            mean = _api._magnitude(layer.mean_u, layer.mean_v)
            if mean is not None:
                values["mean_wind_6km"] = mean
    motion = kinematics.storm_motion
    if motion is not None and len(motion) >= 2:
        right = _api._magnitude(motion[0], motion[1])
        if right is not None:
            values["storm_motion_r"] = right

    if downdraft is not None:
        for key, raw in (
            ("dcape", downdraft.cape),
            ("downrush_t", downdraft.downrush_temperature),
        ):
            number = _api._finite(raw)
            if number is not None:
                values[key] = number
    if include_effective_stp and convective is not None:
        stp = _api._fast_effective_stp_cin(prof, convective)
        if stp is not None:
            values["stp_cin"] = stp
    return values


def _fast_values_from_batch(
    batch,
    index: int,
    prof,
    *,
    include_effective_stp: bool = False,
) -> dict[str, float]:
    """Map one fixed-width backend batch row to the ordinary fast-tier keys."""
    parcels = batch.parcels[index]
    convective = batch.convective_parcels[index]
    bounds = batch.effective_bounds[index]
    downdraft = batch.downdraft[index]
    storm_motion = batch.storm_motion[index]
    layers = batch.kinematic_layers[index]
    values: dict[str, float] = {}

    def put(key, raw):
        number = _api._finite(raw)
        if number is not None:
            values[key] = number

    surface = parcels[0]
    unstable = parcels[1]
    mixed = parcels[2]
    for key, raw in (
        ("sbcape", surface[10]),
        ("sbcin", surface[11]),
        ("sb_lcl", surface[5]),
        ("mlcape", mixed[10]),
        ("mlcin", mixed[11]),
        ("ml_lcl", mixed[5]),
        ("ml_lfc", mixed[7]),
        ("mucape", unstable[10]),
        ("mucin", unstable[11]),
        ("mucape_3km", unstable[12]),
        ("mucape_6km", unstable[13]),
        ("mu_el", unstable[9]),
        ("eff_cape", convective[4][10]),
        ("eff_cin", convective[4][11]),
        ("eff_inflow_base", bounds[0]),
        ("eff_inflow_top", bounds[1]),
        ("dcape", downdraft[0]),
        ("downrush_t", downdraft[2]),
    ):
        put(key, raw)

    for layer_index, (top, shear_key, srh_key) in enumerate(
        (
            (1000.0, "shear_1km", "srh_1km"),
            (3000.0, "shear_3km", "srh_3km"),
            (6000.0, "shear_6km", None),
            (8000.0, "shear_8km", None),
        )
    ):
        layer = layers[layer_index]
        shear = _api._magnitude(layer[4], layer[5])
        if shear is not None:
            values[shear_key] = shear
        if srh_key is not None:
            put(srh_key, layer[10])
        if top == 6000.0:
            mean = _api._magnitude(layer[6], layer[7])
            if mean is not None:
                values["mean_wind_6km"] = mean
    right = _api._magnitude(storm_motion[0], storm_motion[1])
    if right is not None:
        values["storm_motion_r"] = right

    if include_effective_stp:
        most_unstable = convective[2]
        mixed_layer = convective[3]
        convective_view = SimpleNamespace(
            effective_bottom_pressure=bounds[0],
            effective_top_pressure=bounds[1],
            most_unstable=SimpleNamespace(
                diagnostics=SimpleNamespace(
                    cape=most_unstable[10],
                    cin=most_unstable[11],
                    el_height=most_unstable[9],
                )
            ),
            mixed_layer=SimpleNamespace(
                diagnostics=SimpleNamespace(
                    cape=mixed_layer[10],
                    cin=mixed_layer[11],
                    lcl_height=mixed_layer[5],
                )
            ),
        )
        stp = _api._fast_effective_stp_cin(prof, convective_view)
        if stp is not None:
            values["stp_cin"] = stp
    return values


def _fast_effective_stp_cin(prof, convective) -> float | None:
    """Compute the table's one composite without building the full oracle.

    The complete ``ConvectiveProfile`` eagerly evaluates every severe, winter,
    fire, and analogue product. STP needs only the parcel workspace already
    produced by the fast tier plus a few effective-layer wind integrations.
    This follows SHARPpy's own ``get_kinematics``/``get_severe`` equations and
    therefore retains numerical parity while avoiding unrelated work.
    """
    existing = _api._finite(getattr(prof, "stp_cin", None))
    if existing is not None:
        return existing
    try:
        from sharppy.sharptab import interp as sp_interp
        from sharppy.sharptab import params as sp_params
        from sharppy.sharptab import profile as sp_profile
        from sharppy.sharptab import utils as sp_utils
        from sharppy.sharptab import winds as sp_winds

        source = prof
        if not hasattr(source, "sfc"):
            source = sp_profile.BasicProfile.copy(source)
        effective_bottom = float(convective.effective_bottom_pressure)
        effective_top = float(convective.effective_top_pressure)
        if not math.isfinite(effective_bottom) or not math.isfinite(effective_top):
            return 0.0

        mu = convective.most_unstable.diagnostics
        mixed = convective.mixed_layer.diagnostics
        required = (
            mu.cape,
            mu.cin,
            mu.el_height,
            mixed.cape,
            mixed.cin,
            mixed.lcl_height,
        )
        if not all(math.isfinite(float(value)) for value in required):
            return None
        mu_parcel = SimpleNamespace(
            bplus=float(mu.cape),
            bminus=float(mu.cin),
            elhght=float(mu.el_height),
        )
        motion = sp_params.bunkers_storm_motion(
            source, mupcl=mu_parcel, pbot=effective_bottom
        )
        bottom_agl = sp_interp.to_agl(source, sp_interp.hght(source, effective_bottom))
        top_agl = sp_interp.to_agl(source, sp_interp.hght(source, effective_top))
        depth = (float(mu.el_height) - float(bottom_agl)) / 2.0
        shear_top = sp_interp.pres(
            source, sp_interp.to_msl(source, float(bottom_agl) + depth)
        )
        shear = sp_winds.wind_shear(source, pbot=effective_bottom, ptop=shear_top)
        bulk_shear = sp_utils.mag(*shear)

        latitude = _api._finite(getattr(source, "latitude", None)) or 0.0
        motion_offset = 2 if latitude < 0.0 else 0
        helicity = sp_winds.helicity(
            source,
            float(bottom_agl),
            float(top_agl),
            stu=motion[motion_offset],
            stv=motion[motion_offset + 1],
        )[0]
        if latitude < 0.0:
            helicity = -helicity
        value = sp_params.stp_cin(
            float(mixed.cape),
            helicity,
            sp_utils.KTS2MS(bulk_shear),
            float(mixed.lcl_height),
            float(mixed.cin),
        )
        if latitude < 0.0:
            value = -value
        return _api._finite(value)
    except Exception:  # noqa: BLE001 - a missing summary value is non-fatal
        return None


def composite_values(prof) -> dict[str, float]:
    """Return the SPC/AMS composite tier for one profile.

    Reads the cached upstream ``ConvectiveProfile`` that the rest of the
    application already uses, so a box field and the corresponding Skew-T
    report the same numbers.
    """
    from sharpmod.sharptab import derived

    values: dict[str, float] = {}
    oracle = derived._convective_oracle_profile(prof)
    if oracle is not None:
        for key, attribute in (
            ("stp_cin", "stp_cin"),
            ("stp_fixed", "stp_fixed"),
            ("scp", "scp"),
            ("ship", "ship"),
            ("sig_severe", "sig_severe"),
            ("mmp", "mmp"),
            ("wndg", "wndg"),
            ("esp", "esp"),
            ("sherbe", "sherbe"),
            ("ebwd", "ebwspd"),
            ("critical_angle", "right_critical_angle"),
            ("left_scp", "left_scp"),
            ("pwat", "pwat"),
            ("mean_mixr", "mean_mixr"),
            ("low_rh", "low_rh"),
            ("mid_rh", "mid_rh"),
            ("lapse_3km", "lapserate_3km"),
            ("lapse_700_500", "lapserate_700_500"),
            ("k_index", "k_idx"),
            ("totals_totals", "totals_totals"),
            ("conv_t", "convT"),
            ("max_t", "maxT"),
        ):
            number = _api._finite(getattr(oracle, attribute, None))
            if number is not None:
                values[key] = number
        # Effective SRH arrives as (total, positive, negative).
        esrh = getattr(oracle, "right_esrh", None)
        if isinstance(esrh, Sequence) and len(esrh) >= 1:
            number = _api._finite(esrh[0])
            if number is not None:
                values["esrh"] = number

    for key, function in (
        ("lhp", derived.large_hail_parameter),
        ("hpi", derived.hail_possibility_index),
        ("peskov", derived.peskov_index),
        ("mcs_index", derived.mcs_index),
        ("wbz", derived.wet_bulb_zero_height),
        ("dcp", derived.dcp),
        ("vgp", derived.vorticity_generation_parameter),
        ("nst", derived.non_supercell_tornado_parameter),
    ):
        try:
            number = _api._finite(function(prof))
        except Exception:
            number = None
        if number is not None:
            values[key] = number
    for key, layer in (("ehi_1km", 1000.0), ("ehi_3km", 3000.0)):
        try:
            number = _api._finite(derived.ehi(prof, layer))
        except Exception:
            number = None
        if number is not None:
            values[key] = number
    return values


def _analyze_fast_box_points(
    plan: BoxSamplePlan,
    paths: Mapping[str, str],
    *,
    progress: Callable[[int, int, _api.BoxPointAnalysis], None] | None,
    cancelled: Callable[[], bool] | None,
) -> tuple[_api.BoxPointAnalysis, ...]:
    """Analyze a complete fast-tier box through one ordered backend batch."""
    prepared: list[_api.BoxPointAnalysis | None] = [None] * len(plan.points)
    pending = []

    # Loading and transect interpolation stay per node. They can fail or be
    # cancelled independently and do not belong in the native batch contract.
    for position, node in enumerate(plan.points):
        path = paths.get(node.request_id)
        if path is None:
            prepared[position] = _api._empty_point(node)
            continue
        if cancelled is not None and cancelled():
            prepared[position] = _api._empty_point(node, error="cancelled")
            continue
        try:
            prof, _collection = _api.load_profile(path)
            columns = _api.transect_columns(prof)
        except Exception as exc:  # noqa: BLE001 - one bad node must not end
            prepared[position] = _api.BoxPointAnalysis(
                row=node.row,
                col=node.col,
                lat=node.lat,
                lon=node.lon,
                request_id=node.request_id,
                npz_path=path,
                values={},
                error=f"{type(exc).__name__}: {exc}",
            )
            continue
        pending.append((position, node, path, prof, columns))

    if pending:
        try:
            rows = _api.fast_values_many(item[3] for item in pending)
            if len(rows) != len(pending):
                raise _api.BoxAnalysisError(
                    "backend batch returned the wrong profile count"
                )
            outcomes = tuple(rows)
        except Exception:
            # Preserve the old one-bad-node isolation if packing, native work,
            # or dense-result mapping rejects the complete workload.
            isolated = []
            for _position, _node, _path, prof, _columns in pending:
                try:
                    isolated.append(_api.fast_values(prof))
                except Exception as exc:  # noqa: BLE001 - per-node fallback
                    isolated.append(exc)
            outcomes = tuple(isolated)

        for item, outcome in zip(pending, outcomes):
            position, node, path, _prof, columns = item
            if isinstance(outcome, Exception):
                prepared[position] = _api.BoxPointAnalysis(
                    row=node.row,
                    col=node.col,
                    lat=node.lat,
                    lon=node.lon,
                    request_id=node.request_id,
                    npz_path=path,
                    values={},
                    error=f"{type(outcome).__name__}: {outcome}",
                )
            else:
                prepared[position] = _api.BoxPointAnalysis(
                    row=node.row,
                    col=node.col,
                    lat=node.lat,
                    lon=node.lon,
                    request_id=node.request_id,
                    npz_path=path,
                    values=outcome,
                    columns=columns,
                )

    total = len(paths)
    done = 0
    stop_after_progress = False
    results = []
    for position, node in enumerate(plan.points):
        path = paths.get(node.request_id)
        result = prepared[position]
        if result is None:
            # Defensive only: every slot is assigned by loading or batch work.
            result = _api._empty_point(node, error="analysis produced no result")
        if path is not None:
            if stop_after_progress:
                result = _api._empty_point(node, error="cancelled")
            done += 1
            if progress is not None:
                progress(done, total, result)
            if cancelled is not None and cancelled():
                stop_after_progress = True
        results.append(result)
    return tuple(results)


def analyze_box(
    plan: BoxSamplePlan,
    outputs: Mapping[str, str],
    *,
    tiers=(_api.FAST_TIER,),
    run_time=None,
    valid_time=None,
    fxx=0,
    progress: Callable[[int, int, _api.BoxPointAnalysis], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> _api.BoxAnalysis:
    """Analyze every extracted node of a plan into scalar fields.

    ``outputs`` maps a node's ``request_id`` to the ``.npz`` path produced for
    it. Nodes with no entry -- outside the domain, failed, or not yet extracted
    -- are carried as empty results so the lattice stays rectangular.

    ``progress`` is called as ``(done, total, point)`` after each analyzed node,
    which is what lets a field map fill in while the composite tier runs.
    """
    if not isinstance(plan, BoxSamplePlan):
        raise _api.BoxAnalysisError("plan must be a BoxSamplePlan")
    wanted = tuple(str(tier) for tier in tiers)
    unknown = set(wanted) - set(_api.TIERS)
    if unknown:
        raise _api.BoxAnalysisError(
            f"unknown analysis tier(s): {', '.join(sorted(unknown))}"
        )
    paths = {str(key): str(value) for key, value in dict(outputs).items()}
    if set(wanted) == {_api.FAST_TIER}:
        results = _api._analyze_fast_box_points(
            plan,
            paths,
            progress=progress,
            cancelled=cancelled,
        )
    else:
        total = len(paths)
        done = 0
        serial_results: list[_api.BoxPointAnalysis] = []
        for node in plan.points:
            path = paths.get(node.request_id)
            if path is None:
                serial_results.append(_api._empty_point(node))
                continue
            if cancelled is not None and cancelled():
                serial_results.append(_api._empty_point(node, error="cancelled"))
                continue
            try:
                prof, _collection = _api.load_profile(path)
                values = _api.analyze_profile(prof, tiers=wanted)
                # The profile is open here, on a worker thread, having already
                # been decoded for the tier values. Taking the transect columns
                # now saves a later GUI-thread re-read for each slice/envelope.
                result = _api.BoxPointAnalysis(
                    row=node.row,
                    col=node.col,
                    lat=node.lat,
                    lon=node.lon,
                    request_id=node.request_id,
                    npz_path=path,
                    values=values,
                    columns=_api.transect_columns(prof),
                )
            except Exception as exc:  # noqa: BLE001 - one bad node must not end
                result = _api.BoxPointAnalysis(
                    row=node.row,
                    col=node.col,
                    lat=node.lat,
                    lon=node.lon,
                    request_id=node.request_id,
                    npz_path=path,
                    values={},
                    error=f"{type(exc).__name__}: {exc}",
                )
            serial_results.append(result)
            done += 1
            if progress is not None:
                progress(done, total, result)
        results = tuple(serial_results)
    return _api.BoxAnalysis(
        plan=plan,
        points=tuple(results),
        tiers=wanted,
        run_time=run_time,
        valid_time=valid_time,
        fxx=int(fxx),
    )
