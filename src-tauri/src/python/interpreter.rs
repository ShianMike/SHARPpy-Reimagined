//! Interpreter discovery (DESIGN-bridge B2): a pure function of the
//! `SHARPPY_PYTHON` value, the build profile, and a file-existence check.
//!
//! Never `.griblenv` (stale) and never a `PATH` search, so a stray interpreter
//! without sharpmod, or a planted `python.exe`, cannot be picked up.

use std::ffi::OsStr;
use std::path::{Path, PathBuf};

use crate::error::BridgeFailure;

pub const SHARPPY_PYTHON: &str = "SHARPPY_PYTHON";

pub fn bundled_backend(resources: &Path) -> PathBuf {
    resources.join("backend").join(if cfg!(windows) {
        "sharppy-backend.exe"
    } else {
        "sharppy-backend"
    })
}

const NOT_CONFIGURED: &str =
    "The Python backend is not configured. Set SHARPPY_PYTHON to a Python 3.11+ \
interpreter that has sharpmod installed, then restart the app.";

/// The repository root of this checkout (`src-tauri/..`), used only for the
/// debug-build defaults.
pub fn repo_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("..")
}

fn venv_python(venv: &Path) -> PathBuf {
    if cfg!(windows) {
        venv.join("Scripts").join("python.exe")
    } else {
        venv.join("bin").join("python")
    }
}

/// The interpreters a debug build tries, in order: `.gribenv`, then `.venv`.
pub fn debug_candidates(repo: &Path) -> [PathBuf; 2] {
    [
        venv_python(&repo.join(".gribenv")),
        venv_python(&repo.join(".venv")),
    ]
}

/// Chooses the interpreter, or fails with `PYTHON_NOT_FOUND`.
///
/// 1. `SHARPPY_PYTHON`, when set and non-empty: it must be the absolute path of
///    an existing file.
/// 2. The bundled backend for release builds.
/// 3. Debug builds only: the first existing of [`debug_candidates`].
pub fn discover(
    sharppy_python: Option<&OsStr>,
    is_debug: bool,
    repo: &Path,
    exists: &dyn Fn(&Path) -> bool,
    bundled: Option<&Path>,
) -> Result<PathBuf, BridgeFailure> {
    if let Some(value) = sharppy_python.filter(|v| !v.is_empty()) {
        let path = PathBuf::from(value);
        if path.is_absolute() && exists(&path) {
            return Ok(path);
        }
        return Err(BridgeFailure::NotConfigured {
            message: format!(
                "SHARPPY_PYTHON must be the absolute path of a Python interpreter; '{}' was not found.",
                path.display()
            ),
        });
    }
    if !is_debug {
        if let Some(path) = bundled.filter(|path| path.is_absolute() && exists(path)) {
            return Ok(path.to_owned());
        }
    }
    if is_debug {
        if let Some(found) = debug_candidates(repo).into_iter().find(|p| exists(p)) {
            return Ok(found);
        }
    }
    Err(BridgeFailure::NotConfigured {
        message: NOT_CONFIGURED.to_owned(),
    })
}

/// [`discover`] against the real process environment and file system.
pub fn discover_from_environment(resources: &Path) -> Result<PathBuf, BridgeFailure> {
    let value = std::env::var_os(SHARPPY_PYTHON);
    discover(
        value.as_deref(),
        cfg!(debug_assertions),
        &repo_root(),
        &|p: &Path| p.is_file(),
        Some(&bundled_backend(resources)),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::error::{ErrorCode, ErrorContext};

    fn root() -> PathBuf {
        if cfg!(windows) {
            PathBuf::from(r"C:\repo")
        } else {
            PathBuf::from("/repo")
        }
    }

    fn code(result: Result<PathBuf, BridgeFailure>) -> ErrorCode {
        result
            .unwrap_err()
            .into_backend_error(&ErrorContext::start_backend())
            .code
    }

    #[test]
    fn sharppy_python_wins_when_it_is_an_existing_absolute_file() {
        let custom = root().join("custom").join("python.exe");
        let found = discover(
            Some(custom.as_os_str()),
            true,
            &root(),
            &|p| p == custom,
            None,
        )
        .unwrap();
        assert_eq!(found, custom);
    }

    #[test]
    fn sharppy_python_must_exist_and_be_absolute() {
        let missing = root().join("missing.exe");
        let err = discover(Some(missing.as_os_str()), true, &root(), &|_| false, None).unwrap_err();
        let error = err.into_backend_error(&ErrorContext::start_backend());
        assert_eq!(error.code, ErrorCode::PythonNotFound);
        assert!(
            error
                .message
                .contains("SHARPPY_PYTHON must be the absolute path"),
            "{}",
            error.message
        );
        // A relative value is rejected even if it exists relative to the cwd,
        // and the debug defaults are not consulted instead.
        assert_eq!(
            code(discover(
                Some(OsStr::new("python.exe")),
                true,
                &root(),
                &|_| true,
                None
            )),
            ErrorCode::PythonNotFound
        );
    }

    #[test]
    fn empty_value_falls_through_to_the_debug_defaults_in_order() {
        let [gribenv, venv] = debug_candidates(&root());
        assert!(gribenv.to_string_lossy().contains(".gribenv"));
        assert_eq!(
            discover(Some(OsStr::new("")), true, &root(), &|_| true, None).unwrap(),
            gribenv
        );
        assert_eq!(
            discover(None, true, &root(), &|p| p == venv, None).unwrap(),
            venv
        );
        assert_eq!(
            code(discover(None, true, &root(), &|_| false, None)),
            ErrorCode::PythonNotFound
        );
    }

    #[test]
    fn release_builds_have_no_default() {
        assert_eq!(
            code(discover(None, false, &root(), &|_| true, None)),
            ErrorCode::PythonNotFound
        );
    }

    #[test]
    fn release_uses_the_packaged_backend_and_keeps_explicit_overrides() {
        let bundled = bundled_backend(&root());
        assert_eq!(
            discover(None, false, &root(), &|p| p == bundled, Some(&bundled)).unwrap(),
            bundled
        );
        assert!(discover(None, false, &root(), &|_| false, Some(&bundled)).is_err());
        let custom = root().join("python.exe");
        assert_eq!(
            discover(
                Some(custom.as_os_str()),
                false,
                &root(),
                &|_| true,
                Some(&bundled)
            )
            .unwrap(),
            custom
        );
    }

    #[test]
    fn griblenv_is_never_a_candidate() {
        for candidate in debug_candidates(&root()) {
            assert!(!candidate.to_string_lossy().contains(".griblenv"));
        }
        let griblenv = venv_python(&root().join(".griblenv"));
        assert_eq!(
            code(discover(None, true, &root(), &|p| p == griblenv, None)),
            ErrorCode::PythonNotFound
        );
    }
}
