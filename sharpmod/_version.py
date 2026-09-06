"""Single source of truth for SHARPpy Reimagined's package version."""

#: One literal string has to satisfy two grammars, because the Python
#: distribution and the Rust crate are versioned together. Cargo reads it as
#: semver and PEP 440 reads it as a distribution version, so a plain
#: ``MAJOR.MINOR.PATCH`` needs no translation in either direction. Keeping it
#: identical to the crate's version is also what lets the backend-equivalence
#: check stay a plain string comparison -- ``sharpmod_rs.__version__`` is
#: ``CARGO_PKG_VERSION`` verbatim, and a test asserts the two are equal.
#:
#: A pre-release has to be spelled with a hyphen (``1.2.0-beta1``) to stay legal
#: in both, since Cargo rejects PEP 440's ``1.2.0b1`` while the built wheel
#: normalizes the hyphenated form back to it -- which is why the metadata check
#: compares parsed versions rather than strings.
#:
#: Bumping this requires bumping ``rust/sharpmod-rs/Cargo.toml``,
#: ``rust/sharpmod-rs/Cargo.lock``, ``rust/sharpmod-rs/pyproject.toml``, and the
#: default tag in ``.github/workflows/release.yml`` in lockstep; the release
#: workflow refuses to publish when the four disagree or when the tag is not
#: ``v`` + this value.
__version__ = "1.1.0"
