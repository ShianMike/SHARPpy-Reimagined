//! Forecast diagnostics selected from the models' published GRIB records.

use super::palettes::{cape03_stops, lcl_stops, CAPE03_TICKS, LCL_TICKS};
use pyo3::{
    prelude::*,
    types::{PyDict, PyTuple},
};

#[derive(Clone, Copy)]
enum Palette {
    Shared(&'static str),
    Cape03,
    Lcl,
}

#[derive(Clone, Copy)]
struct Product {
    key: &'static str,
    label: &'static str,
    chip: &'static str,
    category: &'static str,
    field: &'static str,
    variable: &'static str,
    level: &'static str,
    description: &'static str,
    palette: Palette,
}

const REFLECTIVITY: Product = Product {
    key: "refd-1km",
    label: "1 km AGL Reflectivity",
    chip: "1 km Refl.",
    category: "Radar",
    field: "refd1km",
    variable: "REFD",
    level: "1000 m above ground",
    description: "Instantaneous simulated reflectivity 1 km above ground, using the Composite Reflectivity scale in dBZ.",
    palette: Palette::Shared("PALETTE_REFLECTIVITY"),
};

const CAPE03: Product = Product {
    key: "cape-0-3km",
    label: "0-3 km AGL CAPE",
    chip: "0-3 km CAPE",
    category: "Instability",
    field: "cape03",
    variable: "CAPE",
    level: "0-3000 m above ground",
    description: "Convective available potential energy in the lowest 3 km above ground, in J/kg.",
    palette: Palette::Cape03,
};

const SBCIN: Product = Product {
    key: "sbcin",
    label: "Surface-Based CIN",
    chip: "SBCIN",
    category: "Instability",
    field: "sbcin",
    variable: "CIN",
    level: "surface",
    description: "Convective inhibition for a parcel lifted from the surface, in J/kg.",
    palette: Palette::Shared("PALETTE_CIN"),
};

const LCL: Product = Product {
    key: "lcl",
    label: "Surface-Based LCL Height",
    chip: "SBLCL",
    category: "Instability",
    field: "lcl",
    variable: "HGT",
    level: "level of adiabatic condensation from sfc",
    description: "Height above ground where a surface parcel first reaches saturation, in metres.",
    palette: Palette::Lcl,
};

fn published_products(model: &str) -> &'static [Product] {
    match model {
        "hrrr" | "rrfs-a" => &[REFLECTIVITY, CAPE03, SBCIN, LCL],
        "rap" => &[REFLECTIVITY, CAPE03, SBCIN],
        "nam" | "nam-3km-conus" => &[REFLECTIVITY, SBCIN, LCL],
        "gfs" => &[REFLECTIVITY, SBCIN],
        "gdps" | "rdps" | "refs" => &[SBCIN],
        // REFS members already have CIN and an LCL derived from 2 m thermodynamics.
        _ => &[],
    }
}

pub(super) fn extend_catalogue(
    py: Python<'_>,
    model: &str,
    products: &mut Vec<Py<PyAny>>,
) -> PyResult<()> {
    let added = published_products(model);
    if added.is_empty() {
        return Ok(());
    }
    let catalogue = PyModule::import(py, "sharpmod.providers.hrrr_products")?;
    let field_spec = catalogue.getattr("FieldSpec")?;
    let product_type = catalogue.getattr("HrrrProduct")?;
    let single = catalogue.getattr("_single")?;
    for product in added {
        let field = field_spec.call1((product.field, "sfc", product.variable, product.level))?;
        let palette = match product.palette {
            Palette::Shared(name) => catalogue.getattr(name)?,
            Palette::Cape03 => {
                stepped_palette(&catalogue, cape03_stops(), &CAPE03_TICKS, "J/kg", Some(5.0))?
            }
            Palette::Lcl => stepped_palette(&catalogue, lcl_stops(), &LCL_TICKS, "m", None)?,
        };
        let options = PyDict::new(py);
        options.set_item("key", product.key)?;
        options.set_item("label", product.label)?;
        options.set_item("chip", product.chip)?;
        options.set_item("category", product.category)?;
        options.set_item("fields", (field,))?;
        options.set_item("derive", single.call1((product.field,))?)?;
        options.set_item("palette", palette)?;
        options.set_item("description", product.description)?;
        if model == "refs" {
            options.set_item(
                "caveat",
                "Uses NOAA's weighted ensemble mean of surface-based CIN.",
            )?;
        }
        products.push(product_type.call((), Some(&options))?.unbind());
    }
    Ok(())
}

fn stepped_palette<'py>(
    catalogue: &Bound<'py, PyModule>,
    stops: impl ExactSizeIterator<Item = (f64, &'static str)>,
    ticks: &[f64],
    units: &str,
    floor: Option<f64>,
) -> PyResult<Bound<'py, PyAny>> {
    let py = catalogue.py();
    let options = PyDict::new(py);
    options.set_item("stepped", true)?;
    options.set_item("ticks", PyTuple::new(py, ticks.iter().copied())?)?;
    options.set_item("floor", floor)?;
    catalogue
        .getattr("Palette")?
        .call((PyTuple::new(py, stops)?, units), Some(&options))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn diagnostics_use_their_published_record_addresses() {
        let fields: Vec<_> = published_products("hrrr")
            .iter()
            .map(|product| (product.key, product.variable, product.level))
            .collect();
        assert_eq!(
            fields,
            [
                ("refd-1km", "REFD", "1000 m above ground"),
                ("cape-0-3km", "CAPE", "0-3000 m above ground"),
                ("sbcin", "CIN", "surface"),
                ("lcl", "HGT", "level of adiabatic condensation from sfc"),
            ],
        );
        for (model, keys) in [
            ("rrfs-a", &["refd-1km", "cape-0-3km", "sbcin", "lcl"][..]),
            ("rap", &["refd-1km", "cape-0-3km", "sbcin"][..]),
            ("nam", &["refd-1km", "sbcin", "lcl"][..]),
            ("nam-3km-conus", &["refd-1km", "sbcin", "lcl"][..]),
            ("gfs", &["refd-1km", "sbcin"][..]),
            ("gdps", &["sbcin"][..]),
            ("rdps", &["sbcin"][..]),
            ("refs", &["sbcin"][..]),
            ("refs-m1", &[][..]),
            ("openmeteo-icon-global", &[][..]),
        ] {
            let actual: Vec<_> = published_products(model)
                .iter()
                .map(|product| product.key)
                .collect();
            assert_eq!(actual, keys, "{model}");
        }
    }
}
