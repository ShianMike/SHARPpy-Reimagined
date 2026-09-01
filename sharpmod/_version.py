"""Single source of truth for SHARPpy Reimagined's package version."""

#: "1.0.0 Beta", spelled so one literal string is legal in both ecosystems this
#: project ships into. Semver needs the hyphen (Cargo rejects ``1.0.0b1``);
#: PEP 440 accepts this form and normalizes the *distribution* version to
#: ``1.0.0b1``, which sorts before ``1.0.0`` as a pre-release must. Keeping the
#: literal identical to the crate's version is what lets the backend-equivalence
#: check stay a plain string comparison -- ``sharpmod_rs.__version__`` is
#: ``CARGO_PKG_VERSION`` verbatim, so any spelling that differed between the two
#: would break it.
__version__ = "1.0.0-beta1"
