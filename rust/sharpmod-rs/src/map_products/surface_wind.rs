//! Surface shading with 10 m wind barbs and sea-level pressure isobars.

use pyo3::{prelude::*, types::PyDict};

use super::ptype::sea_level_contour;

const CHIP_TEMPERATURE_WIND: &str = "2m T+Wind";
const CHIP_DEWPOINT_WIND: &str = "2m Td+Wind";
const ISOBAR_DESCRIPTION: &str = "Mean sea-level pressure is contoured every 2 hPa.";

#[derive(Clone, Copy)]
struct SurfaceWindSpec {
    key: &'static str,
    template_key: &'static str,
    field_attr: &'static str,
    label: &'static str,
    chip: &'static str,
    description: &'static str,
}

const TEMPERATURE_WIND: SurfaceWindSpec = SurfaceWindSpec {
    key: "tmp-2m-wind",
    template_key: "tmp-2m",
    field_attr: "F_TMP2M",
    label: "2 m AGL Temperature, 10 m AGL Wind",
    chip: CHIP_TEMPERATURE_WIND,
    description: "Temperature at 2 m above ground, with 10 m wind barbs in knots.",
};

const DEWPOINT_WIND: SurfaceWindSpec = SurfaceWindSpec {
    key: "dpt-2m-wind",
    template_key: "dpt-2m",
    field_attr: "F_DPT2M",
    label: "2 m AGL Dewpoint, 10 m AGL Wind",
    chip: CHIP_DEWPOINT_WIND,
    description: "Dewpoint at 2 m above ground, with 10 m wind barbs in knots.",
};

fn pressure_variable(model: &str) -> Option<&'static str> {
    match model {
        "hrrr" | "rap" => Some("MSLMA"),
        "rrfs-a" | "refs" | "refs-m1" | "refs-m2" | "refs-m3" | "refs-m4" | "refs-m5" => {
            Some("MSLET")
        }
        "rdps"
        | "hrdps"
        | "nam"
        | "nam-3km-conus"
        | "openmeteo-ecmwf-ifs025"
        | "ecmwf-ifs-9km"
        | "aigfs"
        | "gfs"
        | "gdps"
        | "openmeteo-icon-global"
        | "hrw-wrf-arw"
        | "hrw-wrf-nssl"
        | "hrw-fv3" => Some("PRMSL"),
        _ => None,
    }
}

pub(super) fn extend_catalogue(
    py: Python<'_>,
    model: &str,
    products: &mut Vec<Py<PyAny>>,
) -> PyResult<()> {
    let Some(pressure_variable) = pressure_variable(model) else {
        return Ok(());
    };
    let catalogue = PyModule::import(py, "sharpmod.providers.hrrr_products")?;
    let shared = catalogue.getattr("PRODUCTS")?;
    let replace = PyModule::import(py, "dataclasses")?.getattr("replace")?;
    let u10 = catalogue.getattr("F_U10")?;
    let v10 = catalogue.getattr("F_V10")?;

    let pressure = catalogue.getattr("FieldSpec")?.call1((
        "mslp",
        "sfc",
        pressure_variable,
        "mean sea level",
    ))?;
    let overlay = PyDict::new(py);
    overlay.set_item("contour", sea_level_contour(&catalogue, 2.0)?)?;
    overlay.set_item("contour_fields", (pressure,))?;

    for product in products.iter_mut() {
        let entry = product.bind(py);
        if entry.getattr("key")?.extract::<&str>()? != "thetae-2m" {
            continue;
        }
        let description = entry.getattr("description")?;
        overlay.set_item(
            "description",
            format!("{} {ISOBAR_DESCRIPTION}", description.extract::<&str>()?),
        )?;
        // Theta-e still derives from surface pressure. The MSLP grid is only for the lines.
        *product = replace.call((entry,), Some(&overlay))?.unbind();
        break;
    }

    let specs: &[SurfaceWindSpec] = if model == "aigfs" {
        &[TEMPERATURE_WIND]
    } else {
        &[TEMPERATURE_WIND, DEWPOINT_WIND]
    };

    for spec in specs {
        let template = shared.get_item(spec.template_key)?;
        let field = catalogue.getattr(spec.field_attr)?;
        let updates = overlay.copy()?;
        updates.set_item("key", spec.key)?;
        updates.set_item("label", spec.label)?;
        updates.set_item("chip", spec.chip)?;
        updates.set_item("fields", (field, &u10, &v10))?;
        updates.set_item("wind", true)?;
        updates.set_item(
            "description",
            format!("{} {ISOBAR_DESCRIPTION}", spec.description),
        )?;
        products.push(replace.call((template,), Some(&updates))?.unbind());
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    const ALL_FIELD_MODELS: [&str; 22] = [
        "hrrr",
        "rap",
        "rdps",
        "hrdps",
        "rrfs-a",
        "nam",
        "nam-3km-conus",
        "refs",
        "refs-m1",
        "refs-m2",
        "refs-m3",
        "refs-m4",
        "refs-m5",
        "openmeteo-ecmwf-ifs025",
        "ecmwf-ifs-9km",
        "aigfs",
        "gfs",
        "gdps",
        "openmeteo-icon-global",
        "hrw-wrf-arw",
        "hrw-wrf-nssl",
        "hrw-fv3",
    ];

    #[test]
    fn all_wind_models_use_their_published_mean_sea_level_record() {
        for model in ALL_FIELD_MODELS {
            let expected = if matches!(model, "hrrr" | "rap") {
                "MSLMA"
            } else if model == "rrfs-a" || model.starts_with("refs") {
                "MSLET"
            } else {
                "PRMSL"
            };
            assert_eq!(pressure_variable(model), Some(expected), "{model}");
        }
        for unknown in ["", "unknown", "ecmwf"] {
            assert_eq!(pressure_variable(unknown), None);
        }
    }

    #[test]
    fn chips_fit_locator_inset_budget_and_are_distinct() {
        let specs = [TEMPERATURE_WIND, DEWPOINT_WIND];
        for spec in specs {
            assert!((1..=12).contains(&spec.chip.len()), "{}", spec.key);
            assert!(spec.chip.is_ascii(), "{}", spec.key);
        }
        assert_ne!(specs[0].chip, specs[1].chip);
    }
}
