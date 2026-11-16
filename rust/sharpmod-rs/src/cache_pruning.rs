//! Cache retention decisions, with filesystem mutation kept by its cache owner.

use pyo3::{
    exceptions::PyKeyError,
    prelude::*,
    types::{PyDict, PyList},
};

#[derive(Clone, Copy, Debug)]
struct RetentionEntry {
    accessed: f64,
    size: u64,
    protected: bool,
    pinned: bool,
}

fn prune<E>(
    entries: &[RetentionEntry],
    cutoff: f64,
    max_bytes: u128,
    mut remove: impl FnMut(usize) -> Result<bool, E>,
) -> Result<Vec<usize>, E> {
    let mut order: Vec<_> = (0..entries.len()).collect();
    order.sort_by(|&a, &b| {
        let (a, b) = (entries[a].accessed, entries[b].accessed);
        if a == b {
            std::cmp::Ordering::Equal
        } else {
            a.total_cmp(&b)
        }
    });
    let mut kept = Vec::with_capacity(entries.len());
    let mut removed = Vec::new();
    for index in order {
        let entry = entries[index];
        if !entry.protected && !entry.pinned && entry.accessed < cutoff && remove(index)? {
            removed.push(index);
        } else {
            kept.push(index);
        }
    }
    // Each filesystem size is u64 and the vector length is bounded by usize;
    // u128 keeps byte accounting exact, including budgets above f64 precision.
    let mut total: u128 = kept
        .iter()
        .map(|&index| u128::from(entries[index].size))
        .sum();
    for index in kept {
        if total <= max_bytes {
            break;
        }
        let entry = entries[index];
        if !entry.protected && !entry.pinned && remove(index)? {
            removed.push(index);
            total -= u128::from(entry.size);
        }
    }
    Ok(removed)
}

#[pyfunction]
fn prune_model_cache<'py>(
    py: Python<'py>,
    cache: &Bound<'py, PyAny>,
    cutoff: f64,
) -> PyResult<Bound<'py, PyList>> {
    let raw = cache.call_method0("_entries")?.cast_into::<PyList>()?;
    let mut entries = Vec::with_capacity(raw.len());
    let mut paths = Vec::with_capacity(raw.len());
    for item in raw.iter() {
        let item = item.cast_into::<PyDict>()?;
        let field = |name| {
            item.get_item(name)?
                .ok_or_else(|| PyKeyError::new_err(name))
        };
        entries.push(RetentionEntry {
            accessed: field("accessed")?.extract()?,
            size: field("size")?.extract()?,
            protected: field("protected")?.extract()?,
            pinned: field("pinned")?.extract()?,
        });
        paths.push(field("path")?);
    }
    let budget = cache.getattr("max_bytes")?;
    // The public cache permits arbitrary Python integers. Any budget beyond
    // u128 already exceeds all bytes representable by this filesystem snapshot.
    let budget = if budget.ge(u128::MAX)? {
        u128::MAX
    } else {
        budget.extract()?
    };
    let remove = cache.getattr("_remove_tree")?;
    let removed = prune(&entries, cutoff, budget, |index| {
        remove.call1((&paths[index],))?.is_truthy()
    })?;
    PyList::new(py, removed.into_iter().map(|index| paths[index].clone()))
}

pub(crate) fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(prune_model_cache, module)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use pyo3::ffi::c_str;

    #[test]
    fn invalid_access_time_does_not_discard_pin_metadata() {
        Python::initialize();
        Python::attach(|py| {
            py.run(c_str!(r#"
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import json
from sharpmod.models.model_disk_cache import ModelDiskCache
from sharpmod.models.model_hour_cache import ModelHourKey
with TemporaryDirectory(prefix='cache-pin-regression-') as temp:
    cache = ModelDiskCache(temp, max_bytes=0, max_age_hours=0)
    directory = cache.directory_for(ModelHourKey.create('hrrr', datetime(2026,11,16,tzinfo=timezone.utc), 0))
    metadata = directory/'.cache.json'
    payload = json.loads(metadata.read_text())
    for accessed in ['invalid-time', None, [], {}, float('nan'), float('inf'), float('-inf'), 10**1000]:
        payload.update(pinned=True, accessed=accessed)
        metadata.write_text(json.dumps(payload))
        entry = cache.entries()[0]
        assert entry.pinned, 'invalid access timestamp discarded the pin'
        assert entry.accessed == metadata.stat().st_mtime
        assert cache.prune(now=10_000_000_000.) == []
        assert cache.clear() == []
        assert directory.is_dir()
        assert json.loads(metadata.read_text())['pinned'] is True
"#), None, None).unwrap();
        });
    }

    #[test]
    fn expired_entries_size_retries_and_large_integer_budgets_match_existing_pruning() {
        Python::initialize();
        Python::attach(|py| {
            let globals = PyDict::new(py);
            globals
                .set_item("native", wrap_pyfunction!(prune_model_cache, py).unwrap())
                .unwrap();
            py.run(
                c_str!(
                    r#"
from collections import defaultdict
from pathlib import Path
from random import Random
from unittest.mock import patch
import sharpmod_rs
from sharpmod.models.model_disk_cache import ModelDiskCache
rng = Random(318)
for case in range(120):
    count = rng.randrange(0, 40)
    rows = [dict(path=Path(f'/unused/{i}'), accessed=rng.choice([-0.,0.,1.,2.,3.,8.]),
                 size=rng.choice([0,1,7,2**53+1,2**63-1]),
                 protected=rng.randrange(7)==0, pinned=rng.randrange(5)==0)
            for i in range(count)]
    budget = rng.choice([0,1,2**53,2**53+1,2**64,10**200])
    cutoff = rng.choice([-1.,0.,1.,4.,float('-inf'),float('nan')])
    first_attempt_fails = {i for i in range(count) if rng.randrange(4)==0}
    results = []
    for use_native in [False, True]:
        cache = ModelDiskCache('/unused', max_bytes=budget, max_age_hours=0)
        cache._entries = lambda: rows
        calls = []
        attempts = defaultdict(int)
        def remove(path):
            i = int(path.name)
            calls.append(i)
            attempts[i] += 1
            return not (i in first_attempt_fails and attempts[i] == 1)
        cache._remove_tree = remove
        if use_native:
            removed = native(cache, cutoff)
        else:
            with patch.object(sharpmod_rs, 'prune_model_cache', None, create=True):
                removed = cache.prune(now=cutoff)
        results.append((removed, calls))
    assert results[0] == results[1], (case, rows, budget, cutoff, results)
"#
                ),
                Some(&globals),
                None,
            )
            .unwrap();
        });
    }

    #[test]
    fn a_lease_acquired_after_scan_blocks_prune_and_clear_at_the_removal_boundary() {
        Python::initialize();
        Python::attach(|py| {
            let globals = PyDict::new(py);
            globals
                .set_item("native", wrap_pyfunction!(prune_model_cache, py).unwrap())
                .unwrap();
            py.run(c_str!(r#"
from datetime import datetime, timezone
from tempfile import TemporaryDirectory
from unittest.mock import patch
import os
import sharpmod_rs
from sharpmod.models.model_disk_cache import ModelDiskCache
from sharpmod.models.model_hour_cache import ModelHourKey
for operation in ['prune', 'clear']:
    for use_native in [False, True]:
        with TemporaryDirectory(prefix='late-cache-lease-') as temp:
            cache = ModelDiskCache(temp, max_bytes=0, max_age_hours=0)
            directory = cache.directory_for(ModelHourKey.create('hrrr', datetime(2026,11,16,tzinfo=timezone.utc), 0))
            source = directory/'subset.grib2'
            source.write_bytes(b'GRIB7777')
            scan = cache._entries
            def stale_scan():
                rows = scan()
                (directory/f'.lease-{os.getpid()}-late').write_text('{}')
                return rows
            cache._entries = stale_scan
            with patch.object(sharpmod_rs, 'prune_model_cache', native if use_native else None, create=True):
                removed = cache.prune(now=10_000_000_000.) if operation == 'prune' else cache.clear(include_pinned=True)
            assert removed == []
            assert source.read_bytes() == b'GRIB7777'
"#), Some(&globals), None).unwrap();
        });
    }

    #[test]
    fn byte_accounting_is_exact_and_failed_expiry_remains_a_size_candidate() {
        let entries = [
            RetentionEntry {
                accessed: -0.,
                size: (1 << 53) + 1,
                protected: false,
                pinned: false,
            },
            RetentionEntry {
                accessed: 0.,
                size: 1,
                protected: false,
                pinned: false,
            },
        ];
        let mut calls = Vec::new();
        let removed = prune(&entries, -1., 1 << 53, |i| -> Result<bool, ()> {
            calls.push(i);
            Ok(true)
        })
        .unwrap();
        assert_eq!(removed, [0]);
        assert_eq!(calls, [0]);
        let mut attempts = [0; 2];
        let removed = prune(&entries, 1., 0, |i| -> Result<bool, ()> {
            attempts[i] += 1;
            Ok(attempts[i] > 1)
        })
        .unwrap();
        assert_eq!(removed, [0, 1]);
        assert_eq!(attempts, [2, 2]);
    }

    #[test]
    fn cache_pruning_still_works_with_no_engine_or_an_invalid_selection() {
        Python::initialize();
        Python::attach(|py| {
            py.run(
                c_str!(
                    r#"
from unittest.mock import patch
import os
from sharpmod.backends import selector
from sharpmod.models.model_disk_cache import ModelDiskCache
cache = ModelDiskCache('/unused-cache-prune-test')
try:
    for mode in ['auto', 'rust', 'python', 'invalid-backend']:
        with patch.dict(os.environ, {'SHARPMOD_BACKEND': mode}), \
             patch.object(selector, '_rust_is_installed', return_value=False):
            selector.reset_backend_cache()
            assert cache.prune() == []
finally:
    selector.reset_backend_cache()
"#
                ),
                None,
                None,
            )
            .unwrap();
        });
    }
}
