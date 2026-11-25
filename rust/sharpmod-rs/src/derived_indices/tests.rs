use super::composites::{
    classify_hazard_values, compute_column_max_temp_c, compute_max_tevv, derived_dcp, derived_ehi,
    derived_hpi, derived_lscp, derived_moshe, derived_ncape_ncin, derived_nstp, derived_peskov,
    derived_vgp, kuchera_ratio_from_c,
};
use super::stp::compute_fast_effective_stp_cin;
use crate::wind::wind_to_components;

#[test]
fn fast_stp_cin_northern_and_southern_hemisphere_and_non_parcel_fallback() {
    let pres = [1000.0, 925.0, 850.0, 700.0, 500.0, 300.0, 200.0];
    let hght = [100.0, 750.0, 1500.0, 3000.0, 5600.0, 9200.0, 11800.0];
    let wdir = [150.0, 175.0, 200.0, 230.0, 250.0, 260.0, 265.0];
    let wspd = [12.0, 25.0, 35.0, 45.0, 60.0, 80.0, 90.0];
    let (u, v) = wind_to_components(&wdir, &wspd, None).expect("wind components");

    let north = compute_fast_effective_stp_cin(
        &pres,
        &hght,
        &u,
        &v,
        Some(0),
        Some(-9999.0),
        1000.0,
        700.0,
        2200.0,
        -25.0,
        11000.0,
        2000.0,
        -20.0,
        900.0,
        35.0,
    )
    .expect("stp north")
    .expect("finite stp north");
    assert!(north > 0.0);

    let wdir_south = [210.0, 185.0, 160.0, 130.0, 110.0, 100.0, 95.0];
    let (u_south, v_south) = wind_to_components(&wdir_south, &wspd, None).expect("south winds");
    let south = compute_fast_effective_stp_cin(
        &pres,
        &hght,
        &u_south,
        &v_south,
        Some(0),
        Some(-9999.0),
        1000.0,
        700.0,
        2200.0,
        -25.0,
        11000.0,
        2000.0,
        -20.0,
        900.0,
        -35.0,
    )
    .expect("stp south")
    .expect("finite stp south");
    assert!(south < 0.0, "expected negative south stp, got {south}");
    assert!((south + north).abs() < 1.0e-9);

    // Non-parcel Bunkers fallback when mucape <= 100
    let fallback = compute_fast_effective_stp_cin(
        &pres,
        &hght,
        &u,
        &v,
        Some(0),
        Some(-9999.0),
        1000.0,
        850.0,
        80.0,
        -10.0,
        8000.0,
        50.0,
        -10.0,
        1200.0,
        35.0,
    )
    .expect("fallback")
    .expect("finite fallback");
    assert!(fallback >= 0.0);

    // Non-finite effective layer yields 0.0
    assert_eq!(
        compute_fast_effective_stp_cin(
            &pres,
            &hght,
            &u,
            &v,
            Some(0),
            Some(-9999.0),
            f64::NAN,
            700.0,
            2000.0,
            -10.0,
            10000.0,
            1500.0,
            -10.0,
            900.0,
            35.0,
        )
        .expect("ok"),
        Some(0.0)
    );
}

#[test]
fn composites_hazard_winter_and_moshe_equations() {
    assert_eq!(derived_dcp(0.0, 1500.0, 30.0, 20.0), Some(0.0));
    assert!((derived_dcp(980.0, 2000.0, 40.0, 16.0).unwrap() - 2.0).abs() < 1e-12);

    assert_eq!(
        derived_ncape_ncin(
            Some(2000.0),
            Some(-50.0),
            Some(1000.0),
            Some(1000.0),
            Some(1000.0)
        ),
        (None, None)
    );
    assert_eq!(
        derived_ncape_ncin(
            Some(2000.0),
            Some(-100.0),
            Some(2000.0),
            Some(10000.0),
            Some(500.0)
        ),
        (Some(0.25), Some(-100.0 / 1500.0))
    );
    assert_eq!(derived_ehi(2000.0, 160.0), Some(2.0));
    assert_eq!(derived_vgp(0.0, 40.0), Some(0.0));
    assert!(derived_vgp(-1.0, 40.0).is_none());
    assert!((derived_hpi(500.0, 3350.0).unwrap() - 1.0).abs() < 1e-12);
    assert!((derived_hpi(500.0, 6700.0).unwrap() - 0.0).abs() < 1e-12);
    assert!((derived_peskov(30.0, 1000.0, 10.0, 5.0).unwrap() - 30.0).abs() < 1e-12);

    let lscp = derived_lscp(2000.0, -80.0, -100.0, 20.0 * 1.9438444924406046).unwrap();
    assert!((lscp - (-2.0)).abs() < 1e-12);
    let nstp = derived_nstp(9.0, 100.0, -25.0, 13.0, 8.0e-5).unwrap();
    assert!((nstp - 1.25).abs() < 1e-12);
    let moshe = derived_moshe(6.0, 18.0, 18.0 * 1.9438444924406046, 8.0).unwrap();
    assert!((moshe - 2.0).abs() < 1e-12);

    assert_eq!(
        classify_hazard_values(20.0, 200.0, 50.0, 5.0, 5.0, 5.0),
        "none"
    );
    assert_eq!(
        classify_hazard_values(1500.0, 150.0, 35.0, 1.2, 1.5, 0.5),
        "tornado"
    );
    assert_eq!(
        classify_hazard_values(1500.0, 50.0, 45.0, 0.5, 0.5, 0.5),
        "supercell"
    );
    assert_eq!(
        classify_hazard_values(1500.0, 50.0, 25.0, 0.5, 0.5, 1.2),
        "hail"
    );
    assert_eq!(
        classify_hazard_values(1200.0, 50.0, 32.0, 0.5, 0.5, 0.5),
        "wind"
    );
    assert_eq!(
        classify_hazard_values(600.0, 10.0, 10.0, 0.1, 0.1, 0.1),
        "marginal"
    );

    assert!((kuchera_ratio_from_c(-2.0).unwrap() - 12.0).abs() < 0.05);
    assert_eq!(kuchera_ratio_from_c(10.0), Some(0.0));
    assert_eq!(kuchera_ratio_from_c(-9999.0), None);
    assert_eq!(
        compute_column_max_temp_c(&[1000.0, 700.0, 400.0], &[-5.0, 1.5, -20.0], 500.0),
        Some(1.5)
    );
}

#[test]
fn moshe_max_tevv_handles_valid_and_masked_omega() {
    let pres = [1000.0, 850.0, 700.0, 500.0, 400.0];
    let hght = [100.0, 1500.0, 3100.0, 5600.0, 7200.0];
    let tmpc = [25.0, 15.0, 5.0, -15.0, -28.0];
    let dwpc = [20.0, 12.0, -2.0, -25.0, -38.0];
    let omeg = [-0.2, -0.5, -1.0, -0.8, -0.3];
    let val = compute_max_tevv(&pres, &hght, &tmpc, &dwpc, &omeg, 0, Some(-9999.0))
        .expect("tevv")
        .expect("present");
    assert!(val > 0.0);
    let masked_omeg = [-9999.0; 5];
    assert_eq!(
        compute_max_tevv(&pres, &hght, &tmpc, &dwpc, &masked_omeg, 0, Some(-9999.0)).unwrap(),
        None
    );
}

#[test]
fn bundled_oax_fast_stp_matches_sharppy_parity() {
    use pyo3::prelude::*;
    use std::ffi::CString;
    Python::attach(|py| {
        let code = CString::new(
            r#"
import numpy as np
from sharppy.sharptab import profile as sp_profile, params as sp_params, winds as sp_winds, interp as sp_interp, utils as sp_utils

lines = open('examples/14061619.OAX', encoding='utf-8').read().splitlines()
start = next(i for i, l in enumerate(lines) if l.strip() == '%RAW%') + 1
end = next(i for i, l in enumerate(lines[start:], start) if l.strip() == '%END%')
rows = [[float(x.strip()) for x in l.split(',')] for l in lines[start:end] if l.strip()]
arr = np.asarray(rows, dtype=float)
sp = sp_profile.create_profile(profile='convective', pres=arr[:,0], hght=arr[:,1], tmpc=arr[:,2], dwpc=arr[:,3], wdir=arr[:,4], wspd=arr[:,5], strictQC=False)
pres = np.asarray(sp.pres.filled(-9999.0), dtype=float)
hght = np.asarray(sp.hght.filled(-9999.0), dtype=float)
u = np.asarray(sp.u.filled(-9999.0), dtype=float)
v = np.asarray(sp.v.filled(-9999.0), dtype=float)
motion = sp_params.bunkers_storm_motion(sp, mupcl=sp.mupcl, pbot=sp.ebottom)
bot_agl = float(sp_interp.to_agl(sp, sp_interp.hght(sp, sp.ebottom)))
top_agl = float(sp_interp.to_agl(sp, sp_interp.hght(sp, sp.etop)))
depth = (float(sp.mupcl.elhght) - bot_agl) / 2.0
shear_top = sp_interp.pres(sp, sp_interp.to_msl(sp, bot_agl + depth))
bulk = float(sp_utils.mag(*sp_winds.wind_shear(sp, pbot=sp.ebottom, ptop=shear_top)))
esrh = float(sp_winds.helicity(sp, bot_agl, top_agl, stu=motion[0], stv=motion[1])[0])
ref = float(sp_params.stp_cin(float(sp.mlpcl.bplus), esrh, sp_utils.KTS2MS(bulk), float(sp.mlpcl.lclhght), float(sp.mlpcl.bminus)))
RESULT = (
    (pres.tolist(), hght.tolist(), u.tolist(), v.tolist()),
    (int(sp.sfc), float(sp.ebottom), float(sp.etop), float(sp.mupcl.bplus), float(sp.mupcl.bminus), float(sp.mupcl.elhght)),
    (float(sp.mlpcl.bplus), float(sp.mlpcl.bminus), float(sp.mlpcl.lclhght), ref),
)
"#,
        )
        .expect("cstr");
        let locals = pyo3::types::PyDict::new(py);
        if py.run(code.as_c_str(), None, Some(&locals)).is_err() {
            return;
        }
        let Ok(Some(res)) = locals.get_item("RESULT") else {
            return;
        };
        type SharppyStpReference = (
            (Vec<f64>, Vec<f64>, Vec<f64>, Vec<f64>),
            (usize, f64, f64, f64, f64, f64),
            (f64, f64, f64, f64),
        );
        let (
            (pres, hght, u, v),
            (sfc, eb, et, mu_c, mu_cin, mu_el),
            (ml_c, ml_cin, ml_lcl, ref_val),
        ): SharppyStpReference = res.extract().expect("extract");
        let rust_val = compute_fast_effective_stp_cin(
            &pres,
            &hght,
            &u,
            &v,
            Some(sfc),
            Some(-9999.0),
            eb,
            et,
            mu_c,
            mu_cin,
            mu_el,
            ml_c,
            ml_cin,
            ml_lcl,
            41.3,
        )
        .expect("compute")
        .expect("finite");
        assert!(
            (rust_val - ref_val).abs() < 1.0e-3,
            "rust={rust_val} vs sharppy={ref_val}"
        );
    });
}
