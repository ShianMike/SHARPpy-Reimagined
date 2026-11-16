use super::*;
use pyo3::ffi::c_str;

#[test]
fn cache_listing_tolerates_optional_container_values_and_null_source_fields() {
    let _backend = crate::lock_python_backend_test();
    Python::initialize();
    Python::attach(|py| {
        let globals = PyDict::new(py);
        globals
            .set_item("native", wrap_pyfunction!(model_cache_entries, py).unwrap())
            .unwrap();
        py.run(c_str!(r#"
from pathlib import Path
from unittest.mock import patch
from sharpmod.models.model_disk_cache import ModelDiskCache
import sharpmod_rs
cache = ModelDiskCache('/unused-test-cache')
for field, value in [('member', ['p01']), ('spatial', {'region': 'conus'}),
                     ('source_url', ['url']), ('source_transport', {'type': 'http'}),
                     ('source_fields', None)]:
    payload = {'model': 'hrrr', 'run': '2026-11-16T00:00:00Z', 'fxx': 2, 'contract_version': 6, field: value}
    raw = [{'path': Path('/unused-test-cache/hour'), 'accessed': 100., 'size': 500,
        'protected': True, 'pinned': True, 'valid_grib': True, 'valid_sounding': True, 'file_count': 2, 'payload': payload}]
    cache._entries = lambda: raw
    with patch.object(sharpmod_rs, 'model_cache_entries', None, create=True):
        fallback = cache.entries()
    result = native(raw)
    assert result == fallback
    assert len(result) == 1
    assert result[0].pinned and result[0].protected
    assert result[0].path == Path('/unused-test-cache/hour')
    assert payload[field] is value
"#), Some(&globals), None).unwrap();
    });
}

#[test]
fn cache_entries_preserve_public_types_coercions_and_stable_order() {
    let _backend = crate::lock_python_backend_test();
    Python::initialize();
    Python::attach(|py| {
        let globals = PyDict::new(py);
        globals
            .set_item("native", wrap_pyfunction!(model_cache_entries, py).unwrap())
            .unwrap();
        py.run(c_str!(r#"
from pathlib import Path
from unittest.mock import patch
from sharpmod.models.model_disk_cache import CacheEntry, ModelDiskCache
import copy
import sharpmod_rs
payloads = [{}, {'fxx': '003', 'contract_version': '6', 'model': None, 'source_fields': ['TMP', 7]},
            {'fxx': -2.9, 'contract_version': None, 'member': 0, 'spatial': '', 'source_fields': ()},
            {'fxx': 10**100, 'contract_version': 10**100, 'member': 'p01', 'source_fields': 'UV'},
            {'fxx': [], 'contract_version': {}, 'source_fields': None},
            {'fxx': float('inf'), 'contract_version': 'bad', 'source_fields': []}]
raw = [dict(path=Path(f'/unused-test-cache/{i}'), accessed=t, size=100+i, protected=bool(i%2),
            pinned=bool(i%3), valid_grib=bool(i%2), valid_sounding=not bool(i%2), file_count=i, payload=p)
       for i,(p,t) in enumerate(zip(payloads, [5., 10., 5., 3., -0., 0.]))]
before = copy.deepcopy(raw)
cache = ModelDiskCache('/unused-test-cache')
cache._entries = lambda: raw
with patch.object(sharpmod_rs, 'model_cache_entries', None, create=True):
    expected = cache.entries()
actual = native(raw)
assert actual == expected
assert all(type(entry) is CacheEntry for entry in actual)
assert [entry.path.name for entry in actual] == ['1', '0', '2', '3', '4', '5']
assert actual[3].fxx == 10**100
assert actual[0].source_fields == ('TMP', '7')
assert raw == before
assert native([]) == []
"#), Some(&globals), None).unwrap();
    });
}

#[test]
fn cache_entries_normalize_nonfinite_presentation_times_without_mutating_payloads() {
    Python::initialize();
    Python::attach(|py| {
        let globals = PyDict::new(py);
        globals
            .set_item("native", wrap_pyfunction!(model_cache_entries, py).unwrap())
            .unwrap();
        py.run(
            c_str!(
                r#"
from pathlib import Path
import math
raw = [dict(path=Path(f'/unused-test-cache/{i}'), accessed=t, size=0, protected=False,
            pinned=False, valid_grib=False, valid_sounding=False, file_count=0,
            payload={'source_fields': 42})
       for i,t in enumerate([float('nan'), 8., float('inf'), float('-inf'), -1.])]
result = native(raw)
assert [x.path.name for x in result] == ['1', '0', '2', '3', '4']
assert [x.accessed for x in result] == [8., 0., 0., 0., -1.]
assert all(x.source_fields == () for x in result)
assert math.isnan(raw[0]['accessed']) and raw[2]['accessed'] == float('inf')
"#
            ),
            Some(&globals),
            None,
        )
        .unwrap();
    });
}

#[test]
fn malformed_metadata_keeps_real_storage_usage_and_pinned_entries_available() {
    let _backend = crate::lock_python_backend_test();
    Python::initialize();
    Python::attach(|py| {
        let globals = PyDict::new(py);
        globals
            .set_item("native", wrap_pyfunction!(model_cache_entries, py).unwrap())
            .unwrap();
        py.run(c_str!(r#"
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json
import os
import sharpmod_rs
from sharpmod.bridge import sounding_cache, storage
from sharpmod.models.model_disk_cache import ModelDiskCache
from sharpmod.models.model_hour_cache import ModelHourKey
with TemporaryDirectory(prefix='rust-cache-qc-') as temp:
    root = Path(temp)
    environment = {'SHARPMOD_MODEL_CACHE': str(root/'models'),
                   'SHARPMOD_HRRR_FIELD_CACHE': str(root/'maps'),
                   'SHARPMOD_OUTLOOK_CACHE': str(root/'outlooks')}
    with patch.dict(os.environ, environment):
        disk = ModelDiskCache()
        directory = disk.directory_for(ModelHourKey.create('hrrr', datetime(2026,11,16,tzinfo=timezone.utc), 2))
        metadata = directory/'.cache.json'
        payload = json.loads(metadata.read_text())
        payload.update(member=['p01'], spatial={'region':'conus'}, source_fields=None, pinned=True)
        metadata.write_text(json.dumps(payload))
        source = directory/'subset.grib2'
        source.write_bytes(b'GRIB7777')
        session = sounding_cache.SoundingCache(root/'session')
        with patch.object(sounding_cache, 'cache', return_value=session):
            for implementation in [None, native]:
                with patch.object(sharpmod_rs, 'model_cache_entries', implementation, create=True):
                    row = storage.usage({})['caches'][0]
                    assert row == {'key':'models', 'bytes':8, 'files':1, 'clearable_bytes':0}, row
                    assert storage.clear({'cache':'models'})['freed_bytes'] == 0
                    assert source.read_bytes() == b'GRIB7777'
                    assert json.loads(metadata.read_text()) == payload
"#), Some(&globals), None).unwrap();
    });
}

#[test]
fn cache_inspection_and_portable_validation_do_not_require_a_scientific_engine() {
    let _backend = crate::lock_python_backend_test();
    Python::initialize();
    Python::attach(|py| {
        let globals = PyDict::new(py);
        py.run(
            c_str!(
                r#"
from unittest.mock import patch
import os
from sharpmod.backends import selector
from sharpmod.io.portable_sounding import _physical_profile_issues
from sharpmod.models.model_disk_cache import ModelDiskCache
columns = dict(pres=[1000,900], hght=[0,1000], tmpc=[10,0], dwpc=[5,-5],
               wdir=[0,360], wspd=[0,20])
cache = ModelDiskCache('/unused-test-cache')
try:
    for mode in ['auto', 'rust', 'python', 'invalid-backend']:
        with patch.dict(os.environ, {'SHARPMOD_BACKEND': mode}), \
             patch.object(selector, '_rust_is_installed', return_value=False):
            selector.reset_backend_cache()
            assert cache.entries() == []
            assert _physical_profile_issues(columns) == ()
finally:
    selector.reset_backend_cache()
"#
            ),
            Some(&globals),
            None,
        )
        .unwrap();
    });
}
