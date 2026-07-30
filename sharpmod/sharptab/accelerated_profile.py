"""Backend-accelerated SHARPpy convective-profile compatibility layer.

Only the expensive parcel and DCAPE integrations cross the backend boundary.
The surrounding SHARPpy ``ConvectiveProfile`` orchestration, public parcel
objects, widgets, and severe-weather formulas retain their existing API.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import numpy as np
import numpy.ma as ma
from sharppy.sharptab import interp as sp_interp
from sharppy.sharptab import params as sp_params
from sharppy.sharptab import profile as sp_profile
from sharppy.sharptab import thermo as sp_thermo
from sharppy.sharptab import utils as sp_utils

from sharpmod import backends

logger = logging.getLogger(__name__)

__all__ = [
    "AcceleratedConvectiveProfile",
    "accelerate_profile_collection",
    "install_user_parcel_acceleration",
    "parcel_from_ascent",
]


_PARCEL_DESCRIPTIONS = {
    "surface": (1, "Surface Parcel"),
    "forecast": (2, "Forecast Surface Parcel"),
    "most_unstable": (3, "Most Unstable Parcel in Lowest 300.00 hPa"),
    "mixed_layer": (4, "100.00 hPa Mixed Layer Parcel"),
    "effective": (5, "Mean Effective Layer Parcel"),
    "user": (5, "User Defined Parcel"),
}


def _value(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ma.masked
    return number if np.isfinite(number) else ma.masked


def _trace_temperature(trace, pressure):
    pressures = np.asarray(trace.pressure, dtype=np.float64)
    temperatures = np.asarray(trace.temperature, dtype=np.float64)
    valid = (
        np.isfinite(pressures)
        & (pressures > 0.0)
        & np.isfinite(temperatures)
    )
    pressures = pressures[valid]
    temperatures = temperatures[valid]
    if pressures.size < 2:
        return ma.masked
    order = np.argsort(np.log10(pressures), kind="stable")
    coordinate = np.log10(pressures[order])
    target = np.log10(float(pressure))
    if target < coordinate[0] or target > coordinate[-1]:
        return ma.masked
    return float(np.interp(target, coordinate, temperatures[order]))


def _maximum_parcel_level(prof, pcl):
    if (
        ma.is_masked(pcl.lfcpres)
        or ma.is_masked(pcl.elpres)
        or pcl.ptrace is ma.masked
    ):
        return ma.masked, ma.masked
    start_pressure = float(pcl.pres)
    lcl_pressure, lcl_temperature = sp_thermo.drylift(
        start_pressure,
        float(pcl.tmpc),
        float(pcl.dwpc),
    )
    bottom = min(
        float(prof.pres[prof.sfc]),
        start_pressure,
        float(lcl_pressure),
    )
    previous_pressure = bottom
    previous_height = float(sp_interp.hght(prof, bottom))
    previous_environment = float(sp_interp.vtmp(prof, bottom))
    previous_temperature = float(
        sp_thermo.wetlift(lcl_pressure, lcl_temperature, bottom),
    )
    previous_energy = 0.0
    cumulative = 0.0
    el_found = False

    indices = np.flatnonzero(np.asarray(prof.pres, dtype=float) <= bottom)
    for index in indices:
        if not sp_utils.QC(prof.tmpc[index]):
            continue
        pressure = float(prof.pres[index])
        height = float(prof.hght[index])
        environment = float(prof.vtmp[index])
        temperature = float(
            sp_thermo.wetlift(
                previous_pressure,
                previous_temperature,
                pressure,
            ),
        )
        previous_virtual = float(
            sp_thermo.virtemp(
                previous_pressure,
                previous_temperature,
                previous_temperature,
            ),
        )
        virtual = float(sp_thermo.virtemp(pressure, temperature, temperature))
        def1 = (
            (previous_virtual - previous_environment)
            / (previous_environment + 273.15)
        )
        def2 = (virtual - environment) / (environment + 273.15)
        layer_energy = (
            9.80665 * (def1 + def2) / 2.0 * (height - previous_height)
        )
        cumulative += layer_energy

        if layer_energy >= 0.0 and previous_energy <= 0.0:
            if previous_virtual <= previous_environment:
                cumulative = 0.0
        if (
            layer_energy <= 0.0
            and previous_energy >= 0.0
            and pressure <= float(pcl.elpres)
        ):
            el_found = True
        if cumulative < 0.0 and el_found:
            running = cumulative - layer_energy
            refine_pressure = previous_pressure
            refine_temperature = previous_temperature
            refine_environment = previous_environment
            refine_height = previous_height
            while running > 0.0 and refine_pressure > float(prof.pres[-1]):
                target_pressure = refine_pressure - 1.0
                target_environment = float(
                    sp_interp.vtmp(prof, target_pressure),
                )
                target_temperature = float(
                    sp_thermo.wetlift(
                        refine_pressure,
                        refine_temperature,
                        target_pressure,
                    ),
                )
                target_height = float(
                    sp_interp.hght(prof, target_pressure),
                )
                refine_virtual = float(
                    sp_thermo.virtemp(
                        refine_pressure,
                        refine_temperature,
                        refine_temperature,
                    ),
                )
                target_virtual = float(
                    sp_thermo.virtemp(
                        target_pressure,
                        target_temperature,
                        target_temperature,
                    ),
                )
                refine_def = (
                    (refine_virtual - refine_environment)
                    / (refine_environment + 273.15)
                )
                target_def = (
                    (target_virtual - target_environment)
                    / (target_environment + 273.15)
                )
                running += (
                    9.80665
                    * (refine_def + target_def)
                    / 2.0
                    * (target_height - refine_height)
                )
                refine_pressure = target_pressure
                refine_temperature = target_temperature
                refine_environment = target_environment
                refine_height = target_height
            height_agl = sp_interp.to_agl(
                prof,
                sp_interp.hght(prof, refine_pressure),
            )
            return _value(refine_pressure), _value(height_agl)

        previous_pressure = pressure
        previous_height = height
        previous_environment = environment
        previous_temperature = temperature
        previous_energy = layer_energy
    return ma.masked, ma.masked


def parcel_from_ascent(prof, ascent, *, kind="user"):
    """Convert one typed backend ascent to SHARPpy's public ``Parcel``."""
    diagnostics = ascent.diagnostics
    flag, description = _PARCEL_DESCRIPTIONS[kind]
    pcl = sp_params.Parcel()
    pcl.pres = _value(diagnostics.start_pressure)
    pcl.tmpc = _value(diagnostics.start_temperature)
    pcl.dwpc = _value(diagnostics.start_dewpoint)
    pcl.lplvals = SimpleNamespace(
        flag=flag,
        pres=pcl.pres,
        tmpc=pcl.tmpc,
        dwpc=pcl.dwpc,
        desc=description,
    )
    pcl.blayer = pcl.pres
    pcl.pbot = pcl.pres
    pcl.tlayer = _value(prof.pres[-1])
    pcl.ptop = pcl.tlayer
    pcl.lclpres = _value(diagnostics.lcl_pressure)
    pcl.lclhght = _value(diagnostics.lcl_height)
    pcl.lfcpres = _value(diagnostics.lfc_pressure)
    pcl.lfchght = _value(diagnostics.lfc_height)
    pcl.elpres = _value(diagnostics.el_pressure)
    pcl.elhght = _value(diagnostics.el_height)
    pcl.bplus = _value(diagnostics.cape)
    pcl.bminus = _value(diagnostics.cin)
    pcl.b3km = _value(diagnostics.cape_3km)
    pcl.b6km = _value(diagnostics.cape_6km)
    pcl.ptrace = ma.masked_invalid(
        np.asarray(ascent.trace.pressure, dtype=np.float64),
    )
    pcl.ttrace = ma.masked_invalid(
        np.asarray(ascent.trace.temperature, dtype=np.float64),
    )

    for temperature, pressure_name, height_name in (
        (0.0, "p0c", "hght0c"),
        (-10.0, "pm10c", "hghtm10c"),
        (-20.0, "pm20c", "hghtm20c"),
        (-30.0, "pm30c", "hghtm30c"),
    ):
        pressure = _value(sp_params.temp_lvl(prof, temperature))
        setattr(pcl, pressure_name, pressure)
        setattr(
            pcl,
            height_name,
            (
                _value(sp_interp.hght(prof, pressure))
                if not ma.is_masked(pressure)
                else ma.masked
            ),
        )

    for pressure, name in ((500.0, "li5"), (300.0, "li3")):
        parcel_temperature = _trace_temperature(ascent.trace, pressure)
        environment = _value(sp_interp.vtmp(prof, pressure))
        setattr(
            pcl,
            name,
            (
                _value(float(environment) - float(parcel_temperature))
                if not ma.is_masked(environment)
                and not ma.is_masked(parcel_temperature)
                else ma.masked
            ),
        )

    if pcl.ptrace.size:
        valid = np.asarray(pcl.ptrace >= 500.0) & ~ma.getmaskarray(pcl.ttrace)
        if np.any(valid):
            buoyancy = (
                np.asarray(pcl.ttrace[valid], dtype=np.float64)
                - np.asarray(
                    sp_interp.vtmp(prof, pcl.ptrace[valid]),
                    dtype=np.float64,
                )
            )
            index = int(np.nanargmin(buoyancy))
            pcl.bmin = _value(buoyancy[index])
            pcl.bminpres = _value(pcl.ptrace[valid][index])

    pcl.mplpres, pcl.mplhght = _maximum_parcel_level(prof, pcl)
    try:
        sp_params.bulk_rich(prof, pcl)
    except Exception:
        pass
    return pcl


def _microburst(prof):
    sbpcl = prof.sfcpcl
    sfc_thetae = sp_thermo.thetae(
        sbpcl.lplvals.pres,
        sbpcl.lplvals.tmpc,
        sbpcl.lplvals.dwpc,
    )
    te = 1 if sp_thermo.ctok(sfc_thetae) >= 355 else 0

    if not sp_utils.QC(sbpcl.bplus):
        sbcape_term = np.nan
    elif sbpcl.bplus >= 4300:
        sbcape_term = 4
    elif sbpcl.bplus >= 3700:
        sbcape_term = 2
    elif sbpcl.bplus >= 3300:
        sbcape_term = 1
    elif sbpcl.bplus >= 2000:
        sbcape_term = 0
    else:
        sbcape_term = -5

    if not sp_utils.QC(sbpcl.li5):
        sbli_term = np.nan
    elif sbpcl.li5 <= -10.0:
        sbli_term = 3
    elif sbpcl.li5 <= -9.0:
        sbli_term = 2
    elif sbpcl.li5 <= -7.5:
        sbli_term = 1
    else:
        sbli_term = 0

    pwat_term = np.nan if not sp_utils.QC(prof.pwat) else (
        -3 if prof.pwat < 1.5 else 0
    )
    dcape_term = np.nan if not sp_utils.QC(prof.dcape) else (
        1 if prof.pwat > 1.70 and prof.dcape > 900 else 0
    )
    lr03_term = np.nan if not sp_utils.QC(prof.lapserate_3km) else (
        1 if prof.lapserate_3km > 8.4 else 0
    )
    vertical_totals = getattr(
        prof,
        "vertical_totals",
        sp_params.v_totals(prof),
    )
    if not sp_utils.QC(vertical_totals):
        vt_term = np.nan
    elif vertical_totals < 27:
        vt_term = 0
    elif vertical_totals < 28:
        vt_term = 1
    elif vertical_totals < 29:
        vt_term = 2
    else:
        vt_term = 3
    thetae_difference = sp_params.thetae_diff(prof)
    ted = np.nan if not sp_utils.QC(thetae_difference) else (
        1 if thetae_difference >= 35 else 0
    )
    result = (
        te
        + sbcape_term
        + sbli_term
        + pwat_term
        + dcape_term
        + lr03_term
        + vt_term
        + ted
    )
    if np.isnan(result):
        return ma.masked
    return max(0, result)


class AcceleratedConvectiveProfile(sp_profile.ConvectiveProfile):
    """Convective profile using backend parcel and DCAPE workspaces."""

    def get_parcels(self):
        try:
            workspace = backends.profile_convective_parcels(
                self.pres,
                self.hght,
                self.tmpc,
                self.dwpc,
                sfc=self.sfc,
                missing=self.missing,
            )
            most_unstable = parcel_from_ascent(
                self,
                workspace.most_unstable,
                kind="most_unstable",
            )
            if (
                not sp_utils.QC(most_unstable.bplus)
                or most_unstable.ptrace.size < 2
            ):
                raise ValueError("backend most-unstable parcel is incomplete")
            self.mupcl = most_unstable
            if (
                workspace.surface.diagnostics.start_pressure
                == workspace.most_unstable.diagnostics.start_pressure
            ):
                self.sfcpcl = self.mupcl
            else:
                self.sfcpcl = parcel_from_ascent(
                    self,
                    workspace.surface,
                    kind="surface",
                )
            self.fcstpcl = parcel_from_ascent(
                self,
                workspace.forecast,
                kind="forecast",
            )
            self.mlpcl = parcel_from_ascent(
                self,
                workspace.mixed_layer,
                kind="mixed_layer",
            )
            self.usrpcl = sp_params.Parcel()
            self.ebottom = _value(workspace.effective_bottom_pressure)
            self.etop = _value(workspace.effective_top_pressure)
            if ma.is_masked(self.ebottom) or ma.is_masked(self.etop):
                self.ebottom = ma.masked
                self.etop = ma.masked
                self.ebotm = ma.masked
                self.etopm = ma.masked
                self.effpcl = self.sfcpcl
            else:
                self.ebotm = sp_interp.to_agl(
                    self,
                    sp_interp.hght(self, self.ebottom),
                )
                self.etopm = sp_interp.to_agl(
                    self,
                    sp_interp.hght(self, self.etop),
                )
                self.effpcl = parcel_from_ascent(
                    self,
                    workspace.effective,
                    kind="effective",
                )
        except Exception:
            logger.debug(
                "Falling back to SHARPpy parcel construction",
                exc_info=True,
            )
            super().get_parcels()

    def get_indices(self):
        self.tei = sp_params.tei(self)
        self.esp = sp_params.esp(self)
        self.mmp = sp_params.mmp(self)
        self.wndg = sp_params.wndg(self)
        self.sig_severe = sp_params.sig_severe(self)
        try:
            downdraft = backends.profile_dcape(
                self.pres,
                self.hght,
                self.tmpc,
                self.dwpc,
                sfc=self.sfc,
                missing=self.missing,
            )
            if (
                not np.isfinite(downdraft.cape)
                or len(downdraft.trace.pressure) < 2
            ):
                raise ValueError("backend DCAPE result is incomplete")
            self.dcape = downdraft.cape
            self.dpcl_ptrace = ma.masked_invalid(
                np.asarray(downdraft.trace.pressure, dtype=np.float64),
            )
            self.dpcl_ttrace = ma.masked_invalid(
                np.asarray(downdraft.trace.temperature, dtype=np.float64),
            )
            self.drush = sp_thermo.ctof(downdraft.downrush_temperature)
            self.mburst = _microburst(self)
        except Exception:
            logger.debug(
                "Falling back to SHARPpy DCAPE construction",
                exc_info=True,
            )
            (
                self.dcape,
                self.dpcl_ttrace,
                self.dpcl_ptrace,
            ) = sp_params.dcape(self)
            self.drush = sp_thermo.ctof(self.dpcl_ttrace[-1])
            self.mburst = sp_params.mburst(self)


def accelerate_profile_collection(prof_collection):
    """Select the accelerated target type for subsequent profile upgrades."""
    prof_collection._target_type = AcceleratedConvectiveProfile
    return prof_collection


def install_user_parcel_acceleration():
    """Install the backend explicit-ascent path on SHARPpy's Skew-T widget."""
    import sharppy.viz.skew as skew_module

    widget_type = skew_module.plotSkewT
    if getattr(widget_type, "_sharpmod_user_parcel_accelerated", False):
        return

    def liftparcellevel(self, depth):
        pressure = self.pix_to_pres(self.cursor_loc.y())
        temperature = sp_interp.temp(self.prof, pressure)
        dewpoint = sp_interp.dwpt(self.prof, pressure)
        if depth != 0:
            if depth == -9999:
                text, accepted = skew_module.QInputDialog.getText(
                    None,
                    f"Parcel Depth ({int(pressure)}to __)",
                    "Mean Layer Depth (mb):",
                )
                if not accepted:
                    return
                try:
                    depth = int(text)
                except (TypeError, ValueError):
                    return
            initial = sp_params.DefineParcel(
                self.prof,
                flag=4,
                pbot=pressure,
                pres=depth,
            )
            pressure = initial.pres
            temperature = initial.tmpc
            dewpoint = initial.dwpc
        try:
            ascent = backends.lift_parcel(
                self.prof.pres,
                self.prof.hght,
                self.prof.tmpc,
                self.prof.dwpc,
                pressure,
                temperature,
                dewpoint,
                sfc=self.prof.sfc,
                missing=self.prof.missing,
            )
            parcel = parcel_from_ascent(self.prof, ascent, kind="user")
            if not sp_utils.QC(parcel.bplus):
                raise ValueError("backend user parcel is incomplete")
        except Exception:
            logger.debug(
                "Falling back to SHARPpy user-parcel ascent",
                exc_info=True,
            )
            parcel = sp_params.parcelx(
                self.prof,
                flag=5,
                pres=pressure,
                tmpc=temperature,
                dwpc=dewpoint,
            )
        self.parcel.emit(parcel)

    widget_type.liftparcellevel = liftparcellevel
    widget_type._sharpmod_user_parcel_accelerated = True
