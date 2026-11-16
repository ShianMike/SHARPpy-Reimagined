use super::*;
use pyo3::ffi::c_str;

#[test]
fn portable_qc_matches_existing_checks_on_valid_missing_and_malformed_profiles() {
    let _backend = crate::lock_python_backend_test();
    Python::initialize();
    Python::attach(|py| {
        let globals = PyDict::new(py);
        globals
            .set_item(
                "native",
                wrap_pyfunction!(portable_profile_issues, py).unwrap(),
            )
            .unwrap();
        py.run(
            c_str!(
                r#"
from unittest.mock import patch
import numpy as np
import sharpmod_rs
from sharpmod.io.portable_sounding import _physical_profile_issues, fatal_issues
rng = np.random.default_rng(512)
for n in [0, 1, 2, 3, 37, 512]:
    base = dict(pres=np.linspace(1000, 100, n), hght=np.linspace(100, 16000, n),
                tmpc=np.linspace(20, -70, n), dwpc=np.linspace(15, -80, n),
                wdir=np.linspace(0, 360, n), wspd=np.linspace(0, 150, n))
    cases = [base]
    for name in base:
        for value in [-9999., np.nan, np.inf, -np.inf, -300., 0., 360.01]:
            changed = {k:v.copy() for k,v in base.items()}
            if n:
                changed[name][n//2] = value
            cases.append(changed)
    for _ in range(12):
        cases.append({k:rng.choice([-9999., np.nan, -300., -1., 0., 20., 360., 1000.], n)
                      for k in base})
    for columns in cases:
        before = {k:v.copy() for k,v in columns.items()}
        with patch.object(sharpmod_rs, 'portable_profile_issues', None, create=True):
            expected = _physical_profile_issues(columns)
        assert native(columns) == expected, (columns, native(columns), expected)
        for k in columns:
            np.testing.assert_array_equal(columns[k], before[k])

columns = dict(pres=[1000,900], hght=[0,1000], tmpc=[-300,0], dwpc=[-301,1],
               wdir=[0,360], wspd=[0,20])
assert native(columns) == ('dewpoint_above_temperature',)
assert fatal_issues(native(columns)) == ()
"#
            ),
            Some(&globals),
            None,
        )
        .unwrap();
    });
}

#[test]
fn portable_qc_preserves_array_conversion_strides_readonly_and_mask_data() {
    let _backend = crate::lock_python_backend_test();
    Python::initialize();
    Python::attach(|py| {
        let globals = PyDict::new(py);
        globals
            .set_item(
                "native",
                wrap_pyfunction!(portable_profile_issues, py).unwrap(),
            )
            .unwrap();
        py.run(
            c_str!(
                r#"
from unittest.mock import patch
import numpy as np
import sharpmod_rs
from sharpmod.io.portable_sounding import _physical_profile_issues
base = dict(pres=[1000,900,800], hght=[0,1000,2000], tmpc=[10,5,0],
            dwpc=[5,0,-5], wdir=[0,180,360], wspd=[0,20,40])
for dtype in [np.int32, np.float32, np.float64, object]:
    columns = {k:np.repeat(np.asarray(v,dtype=dtype), 2)[::2] for k,v in base.items()}
    for v in columns.values():
        v.flags.writeable = False
    assert native(columns) == ()
    reversed_columns = {k:v[::-1] for k,v in columns.items()}
    with patch.object(sharpmod_rs, 'portable_profile_issues', None, create=True):
        expected = _physical_profile_issues(reversed_columns)
    assert native(reversed_columns) == expected
columns = {k:np.ma.array(v, mask=[False,True,False]) for k,v in base.items()}
columns['wspd'].data[1] = -5
assert native(columns) == ('negative_wind_speed',)
assert columns['wspd'].mask[1]
try:
    native(dict(base, hght=[0]))
except ValueError as error:
    assert 'equal lengths' in str(error)
else:
    raise AssertionError('mismatched lengths were accepted')
try:
    native(dict(base, pres=[[1000,900,800]]))
except TypeError:
    pass
else:
    raise AssertionError('two-dimensional pressure accepted')
"#
            ),
            Some(&globals),
            None,
        )
        .unwrap();
    });
}
