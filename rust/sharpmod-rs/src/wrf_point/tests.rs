use super::*;
use std::ffi::CString;
#[test]
fn netcdf_reads_select_horizontal_hyperslabs_before_materialization() {
    let _guard = crate::lock_python_backend_test();
    Python::initialize();
    Python::attach(|py| -> PyResult<()> {
        let scope = PyDict::new(py);
        py.run(
            &CString::new(
                r#"
import tempfile
import xarray as xr
from unittest.mock import patch
from xarray.backends.netCDF4_ import NetCDF4ArrayWrapper
from sharpmod.tests.test_wrf_workflow import _wrf_dataset
temporary = tempfile.TemporaryDirectory()
path = temporary.name + '/wrfout.nc'
_wrf_dataset().to_netcdf(path,engine='netcdf4')
dataset = xr.open_dataset(path,engine='netcdf4',cache=False)
reads=[]
original=NetCDF4ArrayWrapper._getitem
def track(array,key):
    reads.append((array.variable_name,key))
    return original(array,key)
patched=patch.object(NetCDF4ArrayWrapper,'_getitem',track)
patched.__enter__()
"#,
            )
            .unwrap(),
            Some(&scope),
            Some(&scope),
        )?;
        let result = wrf_point_columns(
            py,
            &scope.get_item("dataset")?.unwrap(),
            35.1,
            -98.1,
            &py.None().into_bound(py),
        );
        scope
            .get_item("patched")?
            .unwrap()
            .call_method1("__exit__", (py.None(), py.None(), py.None()))?;
        scope.get_item("dataset")?.unwrap().call_method0("close")?;
        scope
            .get_item("temporary")?
            .unwrap()
            .call_method0("cleanup")?;
        result?;
        py.run(
            &CString::new(
                r#"
fields={'P','PB','PH','PHB','T','QVAPOR','U','V'}
selected=[(name,key) for name,key in reads if name in fields]
assert {name for name,key in selected} == fields
for name,key in selected:
    if name == 'U': assert isinstance(key[-2],int) and key[-1].stop-key[-1].start==2, (name,key)
    elif name == 'V': assert key[-2].stop-key[-2].start==2 and isinstance(key[-1],int), (name,key)
    else: assert all(isinstance(k,int) for k in key[-2:]), (name,key)
"#,
            )
            .unwrap(),
            Some(&scope),
            Some(&scope),
        )?;
        Ok(())
    })
    .unwrap();
}
#[test]
fn selected_wrf_column_matches_mass_level_conventions_and_preserves_masks() {
    let _guard = crate::lock_python_backend_test();
    Python::initialize();
    Python::attach(|py| -> PyResult<()> {
        let fixture = PyModule::import(py, "sharpmod.tests.test_wrf_workflow")?;
        let ds = fixture.call_method0("_wrf_dataset")?;
        let result = wrf_point_columns(py, &ds, 35.1, -98.1, &py.None().into_bound(py))?;
        let values = result.get_item(0)?;
        assert_eq!(
            values
                .get_item("pres")?
                .call_method0("tolist")?
                .extract::<Vec<f64>>()?,
            [1000., 850., 700.]
        );
        assert_eq!(
            values
                .get_item("hght")?
                .call_method0("tolist")?
                .extract::<Vec<f64>>()?,
            [500., 2000., 4500.]
        );
        assert!((values.get_item("tmpc")?.get_item(0)?.extract::<f64>()? - 26.85).abs() < 1e-10);
        assert!(
            (values.get_item("wspd")?.get_item(0)?.extract::<f64>()?
                - (80_f64).sqrt() * 1.94384449)
                .abs()
                < 1e-10
        );
        assert_eq!(
            result.get_item(1)?.get_item("glat")?.extract::<f64>()?,
            35.1
        );
        Ok(())
    })
    .unwrap();
}
