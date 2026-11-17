use super::*;
use std::ffi::CString;
#[test]
fn native_display_matches_pinned_sharptab_sampling_and_thermodynamics() {
    let _guard = crate::lock_python_backend_test();
    Python::initialize();
    Python::attach(|py| -> PyResult<()> {
        let locals=PyDict::new(py);
        py.run(&CString::new(r#"
import numpy as np
from sharppy.sharptab import profile, params
p = np.array([1000.,950.,900.,850.,800.,700.,600.,500.,400.,300.,200.,100.])
h = np.array([100.,500.,950.,1450.,2000.,3100.,4300.,5700.,7300.,9200.,12000.,16000.])
t = np.array([25.,22.,19.,16.,13.,6.,-2.,-12.,-25.,-38.,-52.,-65.])
d = t - np.array([4.,4.,5.,7.,8.,10.,10.,12.,15.,15.,15.,15.])
prof=profile.create_profile(profile='default', pres=p,hght=h,tmpc=t,dwpc=d,wdir=np.linspace(150,280,len(p)),wspd=np.linspace(10,95,len(p)),missing=-9999.,latitude=35.)
expected={n:getattr(params,f)(prof) for n,f in [('k_idx','k_index'),('pwat','precip_water'),('low_rh','mean_relh'),('totals_totals','t_totals')]}
expected['mid_rh']=params.mean_relh(prof,850.,650.)
expected['maxT']=params.max_temp(prof)*1.8+32.
for n,b,t,pressure in [('lapserate_3km',0.,3000.,False),('lapserate_3_6km',3000.,6000.,False),('lapserate_850_500',850.,500.,True),('lapserate_700_500',700.,500.,True)]: expected[n]=params.lapse_rate(prof,b,t,pressure)
expected['max_lapse_rate_2_6']=params.max_lapse_rate(prof)
expected['inf_temp_adv']=params.inferred_temp_adv(prof,lat=35.)
"#).unwrap(),Some(&locals),Some(&locals))?;
        let actual=thermo(py,&locals.get_item("prof")?.unwrap())?;
        locals.set_item("actual",actual)?;
        py.run(&CString::new("for key, value in expected.items():\n    if key == 'inf_temp_adv':\n        for a,b in zip(actual[key],value): np.testing.assert_allclose(a,b,rtol=2e-11,atol=2e-11)\n    else: np.testing.assert_allclose(actual[key],value,rtol=2e-11,atol=2e-11,err_msg=key)").unwrap(),Some(&locals),Some(&locals))?;
        py.run(&CString::new(r#"
from types import SimpleNamespace
prof.thetae=np.ma.array(np.linspace(340.,300.,len(p)))
prof.mlpcl=SimpleNamespace(bplus=2000.,bminus=-25.,b3km=125.)
prof.mupcl=SimpleNamespace(bplus=2500.,pres=950.,dwpc=18.)
prof.sfc_6km_shear=(30.,40.)
prof.lapserate_3km=params.lapse_rate(prof,0.,3000.,False)
severe={name:getattr(params,name)(prof) for name in ('tei','esp','wndg','sig_severe')}
expected_ship=params.ship(prof)
"#).unwrap(),Some(&locals),Some(&locals))?;
        let prof=locals.get_item("prof")?.unwrap();populate_severe_indices(py,&prof)?;
        locals.set_item("actual_ship",ship(py,&prof)?)?;
        py.run(&CString::new("for key,value in severe.items(): np.testing.assert_allclose(getattr(prof,key),value,rtol=2e-11,atol=2e-11,err_msg=key)\nnp.testing.assert_allclose(actual_ship,expected_ship,rtol=2e-11,atol=2e-11)").unwrap(),Some(&locals),Some(&locals))?;
        Ok(())
    }).unwrap();
}

#[test]
fn native_mmp_preserves_independent_wind_masks_and_explicit_adapter_overrides() {
    let _guard = crate::lock_python_backend_test();
    Python::initialize();
    Python::attach(|py|->PyResult<()> {
        let scope=PyDict::new(py);scope.set_item("native",wrap_pyfunction!(mmp_linear_predictor,py)?)?;
        py.run(&CString::new(r#"
import numpy as np
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from sharppy.sharptab import profile,params,interp,winds,utils
from sharpmod.io import decoder
raw=next(iter(decoder.load_npz(Path(decoder.__file__).parents[2] / 'examples/soundings/hrrr_point_36.68N_95.66W_f018.npz')[0]._profs.values()))[0]
for gap in ('none','wind','height','all_wind'):
    prof=profile.BasicProfile.copy(raw)
    prof.mupcl=SimpleNamespace(bplus=2000.)
    if gap=='wind': prof.u[2:4]=np.ma.masked;prof.v[14:17]=np.ma.masked
    if gap=='height': prof.hght[2]=np.ma.masked
    if gap=='all_wind': prof.wdir[:]=np.ma.masked
    agl=np.asarray(interp.to_agl(prof,prof.hght))
    bottoms=np.atleast_1d(interp.pres(prof,prof.hght[agl<=1000.]))
    tops=np.atleast_1d(interp.pres(prof,prof.hght[(agl>=6000.)&(agl<10000.)]))
    magnitudes=[utils.mag(*winds.wind_shear(prof,b,t)) for b in bottoms for t in tops]
    usable=[float(v) for v in magnitudes if not np.ma.is_masked(v) and np.isfinite(v)]
    expected=None if not usable else 13.-.0459*float(utils.KTS2MS(max(usable)))-1.16*7.-.000617*2000.-.17*float(utils.KTS2MS(50.))
    with patch.object(params,'lapse_rate',lambda *a,**kw:7.),patch.object(winds,'mean_wind',lambda *a,**kw:(30.,40.)):
        actual=native(prof)
    if expected is None: assert actual is None
    else: np.testing.assert_allclose(actual,expected,rtol=1e-12,atol=1e-12,err_msg=gap)
"#).unwrap(),Some(&scope),Some(&scope))?;Ok(())
    }).unwrap();
}
