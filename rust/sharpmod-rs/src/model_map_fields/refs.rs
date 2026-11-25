//! NOAA/NCEP operational REFS ensemble-mean maps.

use pyo3::{
    prelude::*,
    types::{PyDateAccess, PyDateTime, PyDict, PyTimeAccess, PyTuple},
};

use crate::map_products::{
    derive_refs_member_ehi_0_1km, derive_refs_member_ehi_0_3km, pressure_contour,
};

struct SpcCapeSpec<'a> {
    key: &'a str,
    label: &'a str,
    chip: &'a str,
    field_name: &'a str,
    level: &'a str,
    variants: Option<&'a [(&'a str, &'a str)]>,
    description: &'a str,
}

const SBCAPE_VARIANTS: &[(&str, &str)] = &[
    ("sbcape-min", "Ensemble Minimum"),
    ("sbcape", "Ensemble Mean"),
    ("sbcape-max", "Ensemble Maximum"),
];

fn spc_cape_product(
    py: Python<'_>,
    catalogue: &Bound<'_, PyModule>,
    spec: &SpcCapeSpec<'_>,
) -> PyResult<Py<PyAny>> {
    let field_spec = catalogue.getattr("FieldSpec")?;
    let fields = (
        field_spec.call1((spec.field_name, "sfc", "CAPE", spec.level))?,
        field_spec.call1(("ushr06", "sfc", "UGRD", "0-6000 m above ground"))?,
        field_spec.call1(("vshr06", "sfc", "VGRD", "0-6000 m above ground"))?,
    );
    let options = PyDict::new(py);
    options.set_item("key", spec.key)?;
    options.set_item("label", spec.label)?;
    options.set_item("chip", spec.chip)?;
    options.set_item("category", "Instability")?;
    options.set_item("fields", fields)?;
    options.set_item(
        "derive",
        catalogue.getattr("_single")?.call1((spec.field_name,))?,
    )?;
    options.set_item("palette", catalogue.getattr("PALETTE_CAPE")?)?;
    options.set_item("wind", true)?;
    if let Some(variants) = spec.variants {
        options.set_item("variants", PyTuple::new(py, variants.iter().copied())?)?;
    }
    options.set_item("description", spec.description)?;
    options.set_item(
        "caveat",
        "Uses SPC's 11-member REFS ensemble (HRRR, RRFS, and REFS members) when published; can publish after other REFS fields.",
    )?;
    Ok(catalogue
        .getattr("HrrrProduct")?
        .call((), Some(&options))?
        .unbind())
}

struct SpcWindowSpec<'a> {
    key: &'a str,
    label: &'a str,
    chip: &'a str,
    category: &'a str,
    field_name: &'a str,
    variable: &'a str,
    level: &'a str,
    palette_attr: &'a str,
    max_hours: u16,
    description: &'a str,
    caveat: &'a str,
}

fn spc_window_product(
    py: Python<'_>,
    catalogue: &Bound<'_, PyModule>,
    spec: &SpcWindowSpec<'_>,
) -> PyResult<Py<PyAny>> {
    let field = catalogue.getattr("FieldSpec")?.call1((
        spec.field_name,
        "sfc",
        spec.variable,
        spec.level,
    ))?;
    let options = PyDict::new(py);
    options.set_item("key", spec.key)?;
    options.set_item("label", spec.label)?;
    options.set_item("chip", spec.chip)?;
    options.set_item("category", spec.category)?;
    options.set_item("fields", (field,))?;
    options.set_item(
        "derive",
        catalogue.getattr("_single")?.call1((spec.field_name,))?,
    )?;
    options.set_item("palette", catalogue.getattr(spec.palette_attr)?)?;
    options.set_item("max_hours", spec.max_hours)?;
    options.set_item("description", spec.description)?;
    options.set_item("caveat", spec.caveat)?;
    Ok(catalogue
        .getattr("HrrrProduct")?
        .call((), Some(&options))?
        .unbind())
}

const SURFACE_WIND_WINDOW_SPECS: [SpcWindowSpec<'static>; 2] = [
    SpcWindowSpec {
        key: "wind-10m-4h",
        label: "10-m Wind Speed (4-h Max)",
        chip: "10 m Wind 4h",
        category: "Surface",
        field_name: "wspd10m4h",
        variable: "WIND",
        level: "10 m above ground",
        palette_attr: "PALETTE_SURFACE_WIND",
        max_hours: 4,
        description: "Ensemble-maximum 10 m AGL wind speed over the 4 hours ending at the selected forecast time.",
        caveat: "Computed where reflectivity exceeds 20 dBZ in SPC's 11-member REFS ensemble; available from F004 through F060.",
    },
    SpcWindowSpec {
        key: "wind-10m-24h",
        label: "10-m Wind Speed (24-h Max)",
        chip: "10 m Wind 24h",
        category: "Surface",
        field_name: "wspd10m24h",
        variable: "WIND",
        level: "10 m above ground",
        palette_attr: "PALETTE_SURFACE_WIND",
        max_hours: 24,
        description: "Ensemble-maximum 10 m AGL wind speed over the 24 hours ending at the selected forecast time.",
        caveat: "Computed where reflectivity exceeds 20 dBZ in SPC's 11-member REFS ensemble for 24-hour windows spaced every 12 hours.",
    },
];

const RADAR_WINDOW_SPECS: [SpcWindowSpec<'static>; 2] = [
    SpcWindowSpec {
        key: "refc-4h",
        label: "Reflectivity (4-h Max)",
        chip: "4-h Max Refl",
        category: "Radar",
        field_name: "refc4h",
        variable: "REFD",
        level: "1000 m above ground",
        palette_attr: "PALETTE_REFLECTIVITY",
        max_hours: 4,
        description: "Ensemble-maximum 1 km AGL simulated reflectivity over the 4 hours ending at the selected forecast time, in dBZ.",
        caveat: "Uses SPC's 11-member REFS ensemble (HRRR, RRFS, and REFS members) from F004 through F060; can publish after other REFS fields.",
    },
    SpcWindowSpec {
        key: "refc-24h",
        label: "Reflectivity (24-h Max)",
        chip: "24-h Max Refl",
        category: "Radar",
        field_name: "refc24h",
        variable: "REFD",
        level: "1000 m above ground",
        palette_attr: "PALETTE_REFLECTIVITY",
        max_hours: 24,
        description: "Ensemble-maximum 1 km AGL simulated reflectivity over the 24 hours ending at the selected forecast time, in dBZ.",
        caveat: "Uses SPC's 11-member REFS ensemble (HRRR, RRFS, and REFS members) for 24-hour windows spaced every 12 hours; can publish after other REFS fields.",
    },
];

const PRECIPITATION_WINDOW_SPECS: [SpcWindowSpec<'static>; 5] = [
    SpcWindowSpec {
        key: "qpf-1h-ptype",
        label: "1-h QPF and Dominant Precipitation Type",
        chip: "1-h QPF & P-Type",
        category: "Precipitation",
        field_name: "qpf1h_ptype",
        variable: "APCP",
        level: "surface",
        palette_attr: "PALETTE_QPF_PTYPE",
        max_hours: 1,
        description: "Ensemble-mean 1-hour quantitative precipitation forecast (QPF) in inches, shaded by consensus dominant precipitation type (rain, snow, freezing rain, or mix).",
        caveat: "Uses SPC's 11-member REFS ensemble (HRRR, RRFS, and REFS members) from F001 through F060; only cells with at least 0.01 in of 1-hour QPF are shaded.",
    },
    SpcWindowSpec {
        key: "qpf-3h",
        label: "3-h QPF Mean",
        chip: "3-h QPF",
        category: "Precipitation",
        field_name: "qpf3h",
        variable: "APCP",
        level: "surface",
        palette_attr: "PALETTE_PRECIPITATION",
        max_hours: 3,
        description: "Ensemble-mean 3-hour quantitative precipitation forecast (QPF) over the 3 hours ending at the selected forecast time, in inches.",
        caveat: "Uses SPC's 11-member REFS ensemble (HRRR, RRFS, and REFS members) at 3-hour steps from F003 through F060; can publish after other REFS fields.",
    },
    SpcWindowSpec {
        key: "qpf-6h",
        label: "6-h QPF Mean",
        chip: "6-h QPF",
        category: "Precipitation",
        field_name: "qpf6h",
        variable: "APCP",
        level: "surface",
        palette_attr: "PALETTE_PRECIPITATION",
        max_hours: 6,
        description: "Ensemble-mean 6-hour quantitative precipitation forecast (QPF) over the 6 hours ending at the selected forecast time, in inches.",
        caveat: "Uses SPC's 11-member REFS ensemble (HRRR, RRFS, and REFS members) at 6-hour steps from F006 through F060; can publish after other REFS fields.",
    },
    SpcWindowSpec {
        key: "qpf-24h",
        label: "24-h QPF Mean",
        chip: "24-h QPF",
        category: "Precipitation",
        field_name: "qpf24h",
        variable: "APCP",
        level: "surface",
        palette_attr: "PALETTE_PRECIPITATION",
        max_hours: 24,
        description: "Ensemble-mean 24-hour quantitative precipitation forecast (QPF) over the 24 hours ending at the selected forecast time, in inches.",
        caveat: "Uses SPC's 11-member REFS ensemble (HRRR, RRFS, and REFS members) for 24-hour windows spaced every 12 hours; can publish after other REFS fields.",
    },
    SpcWindowSpec {
        key: "qpf-48h",
        label: "48-h QPF Mean",
        chip: "48-h QPF",
        category: "Precipitation",
        field_name: "qpf48h",
        variable: "APCP",
        level: "surface",
        palette_attr: "PALETTE_PRECIPITATION",
        max_hours: 48,
        description: "Ensemble-mean 48-hour quantitative precipitation forecast (QPF) over the 48 hours ending at the selected forecast time, in inches.",
        caveat: "Uses SPC's 11-member REFS ensemble (HRRR, RRFS, and REFS members) for 48-hour windows spaced every 12 hours; can publish after other REFS fields.",
    },
];

const WINTER_WINDOW_SPECS: [SpcWindowSpec<'static>; 1] = [SpcWindowSpec {
    key: "snow-1h",
    label: "1-h Snowfall",
    chip: "1-h Snow",
    category: "Winter Weather",
    field_name: "snow1h",
    variable: "ASNOW",
    level: "surface",
    palette_attr: "PALETTE_SNOW_DEPTH",
    max_hours: 1,
    description: "Ensemble-mean 1-hour snowfall accumulation over the hour ending at the selected forecast time, in inches.",
    caveat: "Uses SPC's 11-member REFS ensemble (HRRR, RRFS, and REFS members) from F001 through F060; can publish after other REFS fields.",
}];

pub(crate) fn catalogue(py: Python<'_>) -> PyResult<Vec<Py<PyAny>>> {
    let catalogue = PyModule::import(py, "sharpmod.providers.hrrr_products")?;
    let shared = catalogue.getattr("PRODUCTS")?;
    let mut products = ["hgt-wind-500", "hgt-wind-700", "hgt-wind-850"]
        .into_iter()
        .map(|key| shared.get_item(key).map(Bound::unbind))
        .collect::<PyResult<Vec<_>>>()?;

    let replace = PyModule::import(py, "dataclasses")?.getattr("replace")?;
    let pressure =
        catalogue
            .getattr("FieldSpec")?
            .call1(("mslp", "sfc", "MSLET", "mean sea level"))?;
    let isobars = pressure_contour(&catalogue)?;
    let u = catalogue.getattr("F_U10")?;
    let v = catalogue.getattr("F_V10")?;
    for (key, field, label) in [
        ("tmp-2m", "F_TMP2M", "2-m Temperature, MSLP, Wind"),
        ("dpt-2m", "F_DPT2M", "2-m Dewpoint, MSLP, Wind"),
    ] {
        let template = shared.get_item(key)?;
        let description = template.getattr("description")?;
        let description = description.extract::<&str>()?;
        let updates = PyDict::new(py);
        updates.set_item("label", label)?;
        updates.set_item("fields", (catalogue.getattr(field)?, &u, &v))?;
        updates.set_item("contour_fields", (&pressure,))?;
        updates.set_item("contour", &isobars)?;
        updates.set_item("wind", true)?;
        updates.set_item(
            "description",
            format!("{description} Mean sea-level pressure is contoured every 4 hPa, with 10 m wind barbs in knots."),
        )?;
        products.push(replace.call((template,), Some(&updates))?.unbind());
    }
    for spec in &SURFACE_WIND_WINDOW_SPECS {
        products.push(spc_window_product(py, &catalogue, spec)?);
    }
    for spec in &PRECIPITATION_WINDOW_SPECS {
        products.push(spc_window_product(py, &catalogue, spec)?);
    }
    let winds = shared.get_item("hgt-wind-700")?.getattr("fields")?;
    let water = PyDict::new(py);
    water.set_item(
        "fields",
        (
            catalogue.getattr("F_PWAT")?,
            winds.get_item(0)?,
            winds.get_item(1)?,
        ),
    )?;
    water.set_item("wind", true)?;
    water.set_item(
        "description",
        "Total water vapour in the atmospheric column, expressed as inches of liquid water, with 700 mb wind barbs in knots.",
    )?;
    products.push(
        replace
            .call((shared.get_item("pwat")?,), Some(&water))?
            .unbind(),
    );
    for spec in &WINTER_WINDOW_SPECS {
        products.push(spc_window_product(py, &catalogue, spec)?);
    }
    for spec in &RADAR_WINDOW_SPECS {
        products.push(spc_window_product(py, &catalogue, spec)?);
    }
    for spec in [
        SpcCapeSpec {
            key: "sbcape",
            label: "Surface-Based CAPE Min/Mean/Max",
            chip: "SBCAPE",
            field_name: "sbcape",
            level: "surface",
            variants: Some(SBCAPE_VARIANTS),
            description: "Surface-based CAPE (ensemble minimum, mean, or maximum) in J/kg, with 0-6 km mean bulk shear wind barbs in knots.",
        },
        SpcCapeSpec {
            key: "mucape",
            label: "Most-Unstable CAPE Mean",
            chip: "MUCAPE",
            field_name: "mucape",
            level: "255-0 mb above ground",
            variants: None,
            description: "Most-unstable ensemble-mean CAPE in J/kg, with 0-6 km mean bulk shear wind barbs in knots.",
        },
    ] {
        products.push(spc_cape_product(py, &catalogue, &spec)?);
    }
    products.push(shared.get_item("srh-0-3km")?.unbind());
    let srh01_caveat = PyDict::new(py);
    srh01_caveat.set_item(
        "caveat",
        "Uses SPC's 11-member REFS ensemble (HRRR, RRFS, and REFS members) when published; can publish after other REFS fields.",
    )?;
    products.push(
        replace
            .call((shared.get_item("srh-0-1km")?,), Some(&srh01_caveat))?
            .unbind(),
    );
    for (key, srh_attr, layer, derive) in [
        (
            "ehi-0-1km",
            "F_SRH01",
            "0-1 km",
            wrap_pyfunction!(derive_refs_member_ehi_0_1km, py)?,
        ),
        (
            "ehi-0-3km",
            "F_SRH03",
            "0-3 km",
            wrap_pyfunction!(derive_refs_member_ehi_0_3km, py)?,
        ),
    ] {
        let ehi_updates = PyDict::new(py);
        ehi_updates.set_item(
            "fields",
            (catalogue.getattr("F_SBCAPE")?, catalogue.getattr(srh_attr)?),
        )?;
        ehi_updates.set_item("derive", derive)?;
        ehi_updates.set_item(
            "description",
            format!("Surface-based CAPE times {layer} SRH (ensemble means), scaled by 160000."),
        )?;
        ehi_updates.set_item(
            "caveat",
            format!("Derived from SPC's 11-member REFS ensemble-mean surface-based CAPE and {layer} SRH (not an average of member EHIs); can publish after other REFS fields."),
        )?;
        products.push(
            replace
                .call((shared.get_item(key)?,), Some(&ehi_updates))?
                .unbind(),
        );
    }
    let scp_updates = PyDict::new(py);
    scp_updates.set_item(
        "description",
        "Fixed-layer supercell composite computed from ensemble-mean most-unstable CAPE, 0-3 km SRH, and 0-6 km bulk shear.",
    )?;
    scp_updates.set_item(
        "caveat",
        "Fixed-layer form derived from SPC's 11-member REFS ensemble-mean MUCAPE, 0-3 km SRH, and 0-6 km shear (not an average of member SCPs); can publish after other REFS fields.",
    )?;
    products.push(
        replace
            .call((shared.get_item("scp")?,), Some(&scp_updates))?
            .unbind(),
    );
    let stp_updates = PyDict::new(py);
    stp_updates.set_item(
        "fields",
        (catalogue
            .getattr("FieldSpec")?
            .call1(("stp", "sfc", "STP", "surface"))?,),
    )?;
    stp_updates.set_item("derive", catalogue.getattr("_single")?.call1(("stp",))?)?;
    stp_updates.set_item(
        "description",
        "Fixed-layer significant tornado parameter ensemble mean combining surface-based CAPE and CIN, LCL height, 0-1 km SRH, and 0-6 km shear.",
    )?;
    stp_updates.set_item(
        "caveat",
        "Fixed-layer form from SPC's 11-member REFS ensemble (HRRR, RRFS, and REFS members) when published; can publish after other REFS fields.",
    )?;
    products.push(
        replace
            .call((shared.get_item("stp")?,), Some(&stp_updates))?
            .unbind(),
    );
    products.extend(super::grefs::catalogue(py)?);
    Ok(products)
}

#[pyfunction]
fn refs_grib_url(run: &Bound<'_, PyDateTime>, hour: u32) -> String {
    let (year, month, day, cycle) = (
        run.get_year(),
        run.get_month(),
        run.get_day(),
        run.get_hour(),
    );
    format!(
        "https://nomads.ncep.noaa.gov/pub/data/nccf/com/refs/prod/refs.{year:04}{month:02}{day:02}/{cycle:02}/ensprod/refs.t{cycle:02}z.mean.f{hour:02}.conus.grib2"
    )
}

#[pyfunction]
fn available_refs_map_products(
    py: Python<'_>,
    products: Vec<Py<PyAny>>,
    run: &Bound<'_, PyDateTime>,
    hour: u32,
    timeout: f64,
) -> PyResult<Vec<Py<PyAny>>> {
    let url = refs_grib_url(run, hour);
    let api = PyModule::import(py, "sharpmod.providers.hrrr_field")?;
    let unavailable = api.getattr("HrrrFieldUnavailable")?;
    let locate = api.getattr("locate")?;
    let rows = match super::inventory::fetch(py, &url, timeout, None) {
        Ok((rows, _)) => Some(rows),
        Err(error) if error.is_instance(py, &unavailable) => None,
        Err(error) => return Err(error),
    };
    let mut available = Vec::with_capacity(products.len());
    'products: for product in products {
        let entry = product.bind(py);
        if entry.getattr("category")?.extract::<&str>()? == "Guidance"
            || super::refs_zarr::is_zarr_product(entry.getattr("key")?.extract::<&str>()?)
        {
            available.push(product);
            continue;
        }
        let Some(rows) = rows.as_ref() else { continue };
        for field in entry.getattr("all_fields")?.try_iter()? {
            match locate.call1((rows.bind(py), field?)) {
                Ok(_) => {}
                Err(error) if error.is_instance(py, &unavailable) => continue 'products,
                Err(error) => return Err(error),
            }
        }
        available.push(product);
    }
    Ok(available)
}

pub(super) fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(refs_grib_url, module)?)?;
    module.add_function(wrap_pyfunction!(available_refs_map_products, module)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn urls_use_the_operational_mean_stream_and_two_digit_forecast_hours() {
        Python::initialize();
        Python::attach(|py| {
            let run = PyDateTime::new(py, 2026, 11, 22, 6, 0, 0, 0, None).unwrap();
            assert_eq!(
                refs_grib_url(&run, 1),
                "https://nomads.ncep.noaa.gov/pub/data/nccf/com/refs/prod/refs.20261122/06/ensprod/refs.t06z.mean.f01.conus.grib2"
            );
            assert!(refs_grib_url(&run, 60).ends_with("refs.t06z.mean.f60.conus.grib2"));
        });
    }
}
