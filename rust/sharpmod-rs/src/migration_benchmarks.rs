//! Manual phase measurements against an explicitly supplied previous build.
use pyo3::{prelude::*, types::PyDict};
use std::ffi::CString;
#[test]
#[ignore = "manual measurement with a previous native extension and source archive"]
fn measure_migration_phases() {
    let _guard = crate::lock_python_backend_test();
    Python::initialize();
    Python::attach(|py|->PyResult<()> {
        let scope=PyDict::new(py);
        scope.set_item("baseline_extension",std::env::var("SHARPMOD_MIGRATION_BASELINE_EXTENSION").expect("baseline native extension"))?;
        scope.set_item("baseline_archive",std::env::var("SHARPMOD_MIGRATION_BASELINE_ARCHIVE").expect("baseline source archive"))?;
        py.run(&CString::new(r#"
import importlib.util, json, statistics, tarfile, tempfile, time, types
from pathlib import Path
from unittest.mock import patch
import numpy as np
import xarray as xr
from xarray.backends.netCDF4_ import NetCDF4ArrayWrapper
from qtpy.QtCore import QPoint
from sharpmod.io import decoder
from sharppy.sharptab.profile import BasicProfile
from sharpmod.tests.test_wrf_workflow import _wrf_dataset
from sharpmod.rendering import interactive
import sharpmod_rs as native

spec=importlib.util.spec_from_file_location('previous_build.sharpmod_rs',baseline_extension)
previous=importlib.util.module_from_spec(spec)
spec.loader.exec_module(previous)
with tarfile.open(baseline_archive) as archive:
    old_wrf=types.ModuleType('previous_wrf')
    exec(compile(archive.extractfile('sharpmod/tools/wrf_extract.py').read(),'previous_wrf','exec'),old_wrf.__dict__)
    old_interactive=types.ModuleType('previous_interactive')
    exec(compile(archive.extractfile('sharpmod/rendering/interactive.py').read(),'previous_interactive','exec'),old_interactive.__dict__)

reports=[]
def measure(label,reference,current,iterations):
    samples=[[],[]]
    for repeat in range(5):
        for mode in ((0,1) if repeat%2==0 else (1,0)):
            operation=(reference,current)[mode]
            operation()
            start=time.perf_counter()
            for i in range(iterations): operation()
            samples[mode].append((time.perf_counter()-start)*1e6/iterations)
    a,b=map(statistics.median,samples)
    reports.append(dict(phase=label,reference_us=samples[0],native_us=samples[1],reference_median_us=a,native_median_us=b,speedup=a/b))

sample=Path(decoder.__file__).parents[2]/'examples/soundings/hrrr_point_36.68N_95.66W_f018.npz'
profile=BasicProfile.copy(next(iter(decoder.load_npz(sample)[0]._profs.values()))[0])
a,b=previous.profile_thermo(profile),native.profile_thermo(profile)
for key in a:
    if key=='inf_temp_adv':
        for x,y in zip(a[key],b[key]): np.testing.assert_allclose(x,y,rtol=2e-10,atol=2e-10)
    else: np.testing.assert_allclose(a[key],b[key],rtol=2e-10,atol=2e-10,err_msg=key)
measure('full_profile_thermo',lambda:previous.profile_thermo(profile),lambda:native.profile_thermo(profile),30)

n=128
p=types.SimpleNamespace(pres=np.linspace(1000.,100.,n),hght=np.linspace(0.,16000.,n),tmpc=np.linspace(25.,-70.,n),dwpc=np.linspace(20.,-75.,n),u=np.linspace(5.,100.,n),v=np.linspace(10.,40.,n),wdir=np.linspace(180.,260.,n),wspd=np.linspace(10.,110.,n),sfc=0)
sound=types.SimpleNamespace(width=lambda:1200,height=lambda:700,lpad=10.,tpad=20.,brx=1150.,bry=650.,pmin=100.,pmax=1050.,scale=1.,originx=0.,originy=0.,pres_to_pix=lambda pressure:(1050.-pressure)/2.,tmpc_to_pix=lambda t,p:500.+t*5.,mapTo=lambda *args:QPoint(0,0))
hodo=types.SimpleNamespace(width=lambda:800,height=lambda:700,uv_to_pix=lambda u,v:(u*3.,v*3.),srwind=(10.,20.,-10.,20.),mapTo=lambda *args:QPoint(1200,0))
widget=types.SimpleNamespace(default_prof=p,sound=sound,hodo=hodo,width=lambda:2000,height=lambda:1000)
for name in ('targets','inspection'):
    before=getattr(old_interactive,name);after=getattr(interactive,name)
    assert before(widget)==after(widget),name
    measure('interactive_'+name,lambda:before(widget),lambda:after(widget),100)

temporary=tempfile.TemporaryDirectory()
try:
    seed=_wrf_dataset();variables={};ny,nx=180,240
    for name,array in seed.data_vars.items():
        dimensions=array.dims
        shape=tuple(ny+(1 if dim.endswith('_stag') else 0) if dim.startswith('south_north') else nx+(1 if dim.endswith('_stag') else 0) if dim.startswith('west_east') else size for dim,size in zip(dimensions,array.shape))
        key=tuple(0 if dim.startswith(('south_north','west_east')) else slice(None) for dim in dimensions)
        column=array.values[key]
        base=column.reshape(tuple(1 if dim.startswith(('south_north','west_east')) else size for dim,size in zip(dimensions,array.shape)))
        variables[name]=(dimensions,np.broadcast_to(base,shape).copy())
    lat,lon=np.meshgrid(np.linspace(34.,36.,ny),np.linspace(-99.2,-97.2,nx),indexing='ij')
    variables['XLAT']=(seed['XLAT'].dims,lat[None]);variables['XLONG']=(seed['XLONG'].dims,lon[None])
    path=temporary.name+'/wrfout.nc';xr.Dataset(variables).to_netcdf(path,engine='netcdf4')
    reads=[];original=NetCDF4ArrayWrapper._getitem
    def counted(array,key):
        count=1
        for size,index in zip(array.shape,key): count*=len(range(*index.indices(size))) if isinstance(index,slice) else 1
        reads.append((array.variable_name,count))
        return original(array,key)
    def extract(operation):
        with xr.open_dataset(path,engine='netcdf4',cache=False) as dataset: return operation(dataset,35.1,-98.1,None)
    counts=[];outputs=[]
    for operation in (old_wrf._build_columns,native.wrf_point_columns):
        reads.clear()
        with patch.object(NetCDF4ArrayWrapper,'_getitem',counted): outputs.append(extract(operation))
        counts.append(sum(count for name,count in reads if name in {'P','PB','PH','PHB','T','QVAPOR','U','V','COSALPHA','SINALPHA'}))
    for key in outputs[0][0]: np.testing.assert_allclose(outputs[0][0][key],outputs[1][0][key],rtol=2e-11,atol=2e-11)
    measure('wrf_point_column_warm_os_cache',lambda:extract(old_wrf._build_columns),lambda:extract(native.wrf_point_columns),3)
    reports[-1]['scientific_values_read_reference']=counts[0];reports[-1]['scientific_values_read_native']=counts[1]
    reports[-1]['grid_shape']=[ny,nx];reports[-1]['file_bytes']=Path(path).stat().st_size
finally: temporary.cleanup()
report=json.dumps(dict(native_extension=native.__file__,baseline_extension=baseline_extension,samples=reports),indent=2)
"#).unwrap(),Some(&scope),Some(&scope))?;
        let output=std::env::var("SHARPMOD_MIGRATION_BENCHMARK_OUTPUT").expect("benchmark output path");
        std::fs::write(output,scope.get_item("report")?.unwrap().extract::<String>()?)?;Ok(())
    }).unwrap();
}
