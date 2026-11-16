//! Exact content snapshots invalidate metric caches after in-place profile edits.

use numpy::{PyReadonlyArrayDyn, PyUntypedArrayMethods};
use pyo3::{
    exceptions::{PyAttributeError, PyTypeError, PyValueError},
    prelude::*,
    types::{PyBytes, PyString, PyTuple},
};
use ring::digest::{digest, SHA256};

const COLUMNS: [&str; 8] = ["pres", "hght", "tmpc", "dwpc", "wdir", "wspd", "u", "v"];

enum Buffer<'py> {
    Python(Bound<'py, PyBytes>),
    Owned(Vec<u8>),
}

impl Buffer<'_> {
    fn as_bytes(&self) -> &[u8] {
        match self {
            Self::Python(bytes) => bytes.as_bytes(),
            Self::Owned(bytes) => bytes,
        }
    }
}

struct Arrays<'py> {
    numpy: Bound<'py, PyModule>,
    masked: Bound<'py, PyAny>,
    ndarray_type: Bound<'py, PyAny>,
    masked_type: Bound<'py, PyAny>,
    nomask: Bound<'py, PyAny>,
}

enum Column<'py> {
    Absent,
    Opaque(Bound<'py, PyAny>, Bound<'py, PyAny>),
    Numeric {
        shape: Bound<'py, PyAny>,
        dtype: Bound<'py, PyAny>,
        data: Buffer<'py>,
        mask: Buffer<'py>,
    },
}

fn attribute<'py>(profile: &Bound<'py, PyAny>, name: &str) -> PyResult<Bound<'py, PyAny>> {
    match profile.getattr(name) {
        Ok(value) => Ok(value),
        Err(error) if error.is_instance_of::<PyAttributeError>(profile.py()) => {
            Ok(profile.py().None().into_bound(profile.py()))
        }
        Err(error) => Err(error),
    }
}

fn snapshot<'py>(arrays: &Arrays<'py>, raw: &Bound<'py, PyAny>) -> PyResult<Column<'py>> {
    let numpy = &arrays.numpy;
    if raw.is_none() {
        return Ok(Column::Absent);
    }
    let converted = || -> PyResult<Column<'py>> {
        let kind = raw.get_type();
        let plain = kind.is(&arrays.ndarray_type);
        if plain || kind.is(&arrays.masked_type) {
            let parsed: PyResult<PyReadonlyArrayDyn<'_, f64>> = raw.extract().map_err(Into::into);
            match parsed {
                Ok(values) => {
                    let data = values
                        .as_array()
                        .iter()
                        .flat_map(|value| value.to_ne_bytes())
                        .collect();
                    let mask = if plain {
                        Buffer::Owned(vec![0; values.len()])
                    } else {
                        let mask = raw.getattr("_mask")?;
                        if mask.is(&arrays.nomask) {
                            Buffer::Owned(vec![0; values.len()])
                        } else {
                            Buffer::Python(
                                numpy
                                    .call_method1("ascontiguousarray", (mask,))?
                                    .call_method0("tobytes")?
                                    .cast_into::<PyBytes>()?,
                            )
                        }
                    };
                    let mut shape = values.shape().to_vec();
                    if shape.is_empty() {
                        shape.push(1);
                    }
                    let dtype = if cfg!(target_endian = "little") {
                        "<f8"
                    } else {
                        ">f8"
                    };
                    return Ok(Column::Numeric {
                        shape: PyTuple::new(raw.py(), shape)?.into_any(),
                        dtype: PyString::new(raw.py(), dtype).into_any(),
                        data: Buffer::Owned(data),
                        mask,
                    });
                }
                Err(error) if error.is_instance_of::<PyTypeError>(raw.py()) => (),
                Err(error) => return Err(error),
            }
        }
        let masked = &arrays.masked;
        let column = masked.call_method1("asarray", (raw,))?;
        let data = numpy.call_method1(
            "ascontiguousarray",
            (masked.call_method1("getdata", (&column,))?,),
        )?;
        let mask = numpy.call_method1(
            "ascontiguousarray",
            (masked.call_method1("getmaskarray", (&column,))?,),
        )?;
        Ok(Column::Numeric {
            shape: data.getattr("shape")?,
            dtype: data.getattr("dtype")?.getattr("str")?,
            data: Buffer::Python(data.call_method0("tobytes")?.cast_into::<PyBytes>()?),
            mask: Buffer::Python(mask.call_method0("tobytes")?.cast_into::<PyBytes>()?),
        })
    };
    match converted() {
        Ok(column) => Ok(column),
        Err(error)
            if error.is_instance_of::<PyTypeError>(numpy.py())
                || error.is_instance_of::<PyValueError>(numpy.py()) =>
        {
            Ok(Column::Opaque(
                PyModule::import(numpy.py(), "builtins")?.call_method1("id", (raw,))?,
                raw.repr()?.into_any(),
            ))
        }
        Err(error) => Err(error),
    }
}

#[pyfunction]
fn profile_metric_signature<'py>(
    py: Python<'py>,
    profile: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyTuple>> {
    let numpy = PyModule::import(py, "numpy")?;
    let masked = numpy.getattr("ma")?;
    let arrays = Arrays {
        ndarray_type: numpy.getattr("ndarray")?,
        masked_type: masked.getattr("MaskedArray")?,
        nomask: masked.getattr("nomask")?,
        numpy,
        masked,
    };
    let columns = COLUMNS
        .iter()
        .map(|name| snapshot(&arrays, &attribute(profile, name)?))
        .collect::<PyResult<Vec<_>>>()?;
    let bytes: Vec<_> = columns
        .iter()
        .filter_map(|column| match column {
            Column::Numeric { data, mask, .. } => Some((data.as_bytes(), mask.as_bytes())),
            _ => None,
        })
        .collect();
    // These slices borrow immutable snapshots held alive by `columns`.
    // Raw NumPy arrays are never borrowed while the interpreter is detached.
    let digests: Vec<_> = py.detach(|| {
        bytes
            .iter()
            .map(|(data, mask)| (digest(&SHA256, data), digest(&SHA256, mask)))
            .collect()
    });
    let mut digests = digests.into_iter();
    let mut signatures = vec![attribute(profile, "_sharpmod_metrics_revision")?];
    for (name, column) in COLUMNS.iter().zip(columns) {
        let mut fields = vec![name.into_pyobject(py)?.into_any()];
        match column {
            Column::Absent => fields.push(py.None().into_bound(py)),
            Column::Opaque(identity, representation) => fields.extend([identity, representation]),
            Column::Numeric { shape, dtype, .. } => {
                let (data, mask) = digests
                    .next()
                    .expect("one digest for each numeric snapshot");
                fields.extend([
                    shape,
                    dtype,
                    PyBytes::new(py, data.as_ref()).into_any(),
                    PyBytes::new(py, mask.as_ref()).into_any(),
                ]);
            }
        }
        signatures.push(PyTuple::new(py, fields)?.into_any());
    }
    PyTuple::new(py, signatures)
}

pub(crate) fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(profile_metric_signature, module)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use pyo3::{ffi::c_str, types::PyDict};

    #[test]
    fn direct_numeric_snapshots_equal_general_numpy_snapshots() {
        Python::initialize();
        Python::attach(|py| {
            let native = PyModule::new(py, "fingerprint_direct_test").unwrap();
            register(&native).unwrap();
            let scope = PyDict::new(py);
            scope.set_item("native", native).unwrap();
            py.run(c_str!(r#"
import numpy as np
from types import SimpleNamespace
class General(np.ndarray):
    pass
class GeneralMasked(np.ma.MaskedArray):
    pass
for raw in [np.array(3.), np.empty((0, 2)), np.arange(30.).reshape(5,6).T[:,::-1], np.array([-0., np.nan, np.inf]),
            np.ma.array([1.,2.,3.]), np.ma.array([1.,2.,3.],mask=[False,True,False]), np.ma.array(3.,mask=True)]:
    general = raw.view(GeneralMasked if isinstance(raw,np.ma.MaskedArray) else General)
    assert native.profile_metric_signature(SimpleNamespace(tmpc=raw)) == native.profile_metric_signature(SimpleNamespace(tmpc=general))
"#), Some(&scope), Some(&scope)).unwrap();
        });
    }

    #[test]
    fn fingerprint_invalidation_matches_python_for_data_masks_shapes_and_revisions() {
        Python::initialize();
        Python::attach(|py| {
            let native = PyModule::new(py, "fingerprint_test").unwrap();
            register(&native).unwrap();
            let scope = PyDict::new(py);
            scope.set_item("native", native).unwrap();
            py.run(c_str!(r#"
from types import SimpleNamespace
from unittest.mock import patch
from sharpmod.analysis import profile_metrics as api
import numpy as np

def reference(profile):
    with patch.object(api, '_native_metric_operation', return_value=None):
        return api._profile_signature(profile)

for dtype in ['f8', 'f4', '>f8', 'i4', 'U4', 'complex128']:
    values = np.arange(40).astype(dtype).reshape(5,8).T[:, ::-1]
    values.setflags(write=False)
    profile = SimpleNamespace(tmpc=values, dwpc=np.ma.array([1.,2.,3.], mask=[False,True,False]))
    before, expected = native.profile_metric_signature(profile), reference(profile)
    assert before == native.profile_metric_signature(profile)
    profile.dwpc.mask[0] = True
    assert (before != native.profile_metric_signature(profile)) == (expected != reference(profile))
    before, expected = native.profile_metric_signature(profile), reference(profile)
    profile.tmpc = values.copy().reshape(-1)
    assert (before != native.profile_metric_signature(profile)) == (expected != reference(profile))
    before, expected = native.profile_metric_signature(profile), reference(profile)
    profile._sharpmod_metrics_revision = 2
    assert (before != native.profile_metric_signature(profile)) == (expected != reference(profile))
    np.testing.assert_array_equal(values, np.arange(40).astype(dtype).reshape(5,8).T[:, ::-1])

profile = SimpleNamespace(tmpc=np.array([0., np.nan, np.inf, -9999.]))
for new_value in [-0., 1., float('inf'), -9998.]:
    before, expected = native.profile_metric_signature(profile), reference(profile)
    profile.tmpc[0] = new_value
    assert (before != native.profile_metric_signature(profile)) == (expected != reference(profile))
class Opaque:
    def __array__(self, *args, **kwargs):
        raise ValueError('not a numeric column')
profile.u = Opaque()
assert native.profile_metric_signature(profile) == native.profile_metric_signature(profile)
assert native.profile_metric_signature(SimpleNamespace()) == (None,) + tuple((field, None) for field in ['pres','hght','tmpc','dwpc','wdir','wspd','u','v'])
"#), Some(&scope), Some(&scope)).unwrap();
        });
    }
}
