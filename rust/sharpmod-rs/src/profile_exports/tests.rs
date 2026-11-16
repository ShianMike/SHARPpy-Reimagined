use super::*;
use pyo3::ffi::c_str;
use std::{fs, path::PathBuf};

fn directory() -> PathBuf {
    let mut random = [0; 8];
    use ring::rand::{SecureRandom, SystemRandom};
    SystemRandom::new().fill(&mut random).unwrap();
    let path =
        std::env::temp_dir().join(format!("sharpmod-csv-{:016x}", u64::from_ne_bytes(random)));
    fs::create_dir(&path).unwrap();
    path
}

#[test]
fn native_writer_quotes_utf8_and_preserves_crlf_records() {
    let directory = directory();
    let path = directory.join("weather.csv");
    let table = Table {
        fields: vec!["label".into(), "value".into()],
        rows: vec![vec!["Snow, \"heavy\"\r\n雪".into(), "".into()]],
    };
    storage::write(&path, &table, false).unwrap();
    assert_eq!(
        fs::read(&path).unwrap(),
        "label,value\r\n\"Snow, \"\"heavy\"\"\r\n雪\",\r\n".as_bytes()
    );
    fs::remove_dir_all(directory).unwrap();
}

#[test]
fn failed_records_preserve_saved_output_and_leave_no_temporary_file() {
    let directory = directory();
    let path = directory.join("saved.csv");
    fs::write(&path, b"keep this export").unwrap();
    let malformed = Table {
        fields: vec!["one".into()],
        rows: vec![vec!["two".into(), "fields".into()]],
    };
    assert!(storage::write(&path, &malformed, false).is_err());
    assert_eq!(fs::read(&path).unwrap(), b"keep this export");
    assert_eq!(fs::read_dir(&directory).unwrap().count(), 1);
    fs::remove_dir_all(directory).unwrap();
}

#[cfg(unix)]
#[test]
fn overwriting_an_export_preserves_private_file_permissions() {
    use std::os::unix::fs::PermissionsExt;
    let directory = directory();
    let path = directory.join("private.csv");
    fs::write(&path, b"private").unwrap();
    fs::set_permissions(&path, fs::Permissions::from_mode(0o600)).unwrap();
    storage::write(
        &path,
        &Table {
            fields: vec!["weather".into()],
            rows: vec![vec!["snow".into()]],
        },
        false,
    )
    .unwrap();
    assert_eq!(
        fs::metadata(&path).unwrap().permissions().mode() & 0o777,
        0o600
    );
    assert_eq!(fs::read(&path).unwrap(), b"weather\r\nsnow\r\n");
    fs::remove_dir_all(directory).unwrap();
}

#[test]
fn replace_failures_are_clean_and_existing_directories_survive() {
    let directory = directory();
    let target = directory.join("directory.csv");
    fs::create_dir(&target).unwrap();
    let table = Table {
        fields: vec!["one".into()],
        rows: Vec::new(),
    };
    assert!(storage::write(&target, &table, false).is_err());
    assert!(target.is_dir());
    assert_eq!(fs::read_dir(&directory).unwrap().count(), 1);
    assert!(storage::write(&directory.join("absent").join("file.csv"), &table, false).is_err());
    storage::write(&directory.join("created").join("file.csv"), &table, true).unwrap();
    fs::remove_dir_all(directory).unwrap();
}

#[test]
fn temporary_names_do_not_extend_a_long_destination_filename() {
    let directory = directory();
    let target = directory.join(format!("{}.csv", "x".repeat(220)));
    storage::write(
        &target,
        &Table {
            fields: vec!["ok".into()],
            rows: Vec::new(),
        },
        false,
    )
    .unwrap();
    assert_eq!(fs::read(&target).unwrap(), b"ok\r\n");
    assert_eq!(fs::read_dir(&directory).unwrap().count(), 1);
    fs::remove_dir_all(directory).unwrap();
}

#[test]
fn ensemble_csv_matches_python_bytes_for_missing_data_and_unicode() {
    Python::initialize();
    Python::attach(|py| {
        let native = PyModule::new(py, "exports_test").unwrap();
        register(&native).unwrap();
        let scope = PyDict::new(py);
        scope.set_item("native", native).unwrap();
        py.run(c_str!(r#"
from pathlib import Path
from datetime import datetime, timezone, timedelta
from types import MappingProxyType
from unittest.mock import patch
from tempfile import TemporaryDirectory
from sharpmod.analysis import profile_metrics as api
from sharpmod.analysis.ensemble_members import EnsembleAcquisition, MemberFailure

def compare(summary, acquisition, root):
    reference, actual = root / 'reference.csv', root / 'native.csv'
    with patch.object(api, '_native_metric_operation', return_value=None):
        assert api.export_ensemble_csv(reference, summary, acquisition) == reference
    result = native.export_profile_ensemble_csv(actual, summary, acquisition)
    assert isinstance(result, Path) and result == actual
    assert actual.read_bytes() == reference.read_bytes()


run = datetime(2026, 11, 16, 0, 1, 2, 345, timezone(timedelta(hours=8)))
with TemporaryDirectory() as directory:
    root = Path(directory)
    scalar = MappingProxyType({'mlcape': api.MetricDistribution(2, 10.0, 30.0, 50.0, 0.0, 100.0),
        'mlcin': api.MetricDistribution(0, None, None, None, None, None)})
    summary = api.EnsembleSummary(run, 3, 1, ('ok','bad','cancel'), ('download',), scalar, (), True,
        requested_members=('ok','bad','cancel','download'), loaded_members=('ok','bad','cancel'), unusable_members=('bad','cancel'),
        distribution_available=False,
        member_samples=(api.EnsembleMemberSample('ok','completed'), api.EnsembleMemberSample('bad','failed',reason='error, "雪"'),
            api.EnsembleMemberSample('cancel','cancelled',reason='cancelled\nby user')))
    ledger = EnsembleAcquisition(('ok','bad','cancel','download'), ('ok','bad','cancel'), (MemberFailure('download','failed','HTTP unavailable'),))
    for acquisition in [None, ledger]:
        compare(summary, acquisition, root)
    empty = api.EnsembleSummary(None,0,0,(),(),{},(),False)
    compare(empty, None, root)
"#), Some(&scope), Some(&scope)).unwrap();
    });
}
