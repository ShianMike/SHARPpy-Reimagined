# Windows release builds

The release workflow resolves one immutable source commit, builds Windows
downloads alongside the scientific and desktop test lanes, and waits for all
required checks before attestation or publication. Caches are build inputs;
they do not replace source validation, runtime checks or signed-artifact tests.

## Cache ownership and invalidation

| Cache owner | Stored inputs | Identity and restore behavior |
|---|---|---|
| setup-python | pip download cache | Windows architecture, exact Python version, root `pyproject.toml` and `constraints/release.txt`. Native package version changes do not create another pip cache. Every run installs the constrained package, compatibility-patched SHARPpy and native wheel, then runs `pip check`. |
| Cargo downloads | Registry index, compressed `.crate` archives and Git database | OS, architecture and both Windows build lockfiles. Partial restores may supply downloaded packages, which Cargo validates against the current lockfile. Unpacked registry sources are regenerated rather than archived alongside their compressed copies. |
| Scientific release dependencies | `rust/sharpmod-rs/target/release/{.fingerprint,build,deps}` | OS, architecture, actual pinned rustc identity (currently 1.88.0), CPython 3.13.16/cp313, release/default features, thin LTO, one codegen unit, lockfile and first-party manifests. Partial restores stay inside this compiler/ABI/profile namespace; Cargo checks dependency fingerprints against current inputs. |
| Desktop release dependencies | `src-tauri/target/release/{.fingerprint,build,deps}` | OS, architecture, exact pinned rustc version, release/default features, lockfile and first-party manifests. Partial restores stay inside this compiler/profile namespace and are rebuilt through Cargo. |
| setup-node | npm download cache | OS, architecture, package manager and `frontend/package-lock.json`. Node remains pinned and `npm ci` still runs; `node_modules` is not restored as an installed environment. |

Before successful post-job cache saves, the workflow removes artifacts owned
by `sharpmod-rs`, `sharpmod-weather` and `sharppy-reimagined` from the relevant
targets that will be saved. Exact hits are immutable and are not uploaded
again, so their final cleanup is skipped. Every runner compiles both scientific
first-party crates and the desktop application from the checked-out source.
Cached wheels and final
executables cannot substitute for these builds. A missing cache follows the
ordinary full build path. A broken dependency or artifact must still fail the
build or its existing verification gate.

Cache paths exclude installed Python environments, frontend installations,
release staging, signed outputs and secrets. Download archives have one owner
instead of being duplicated in both compilation caches. Compiled entries are
limited to dependency targets within a toolchain/profile namespace; GitHub's
repository cache quota and eviction policy bound retained history. A lockfile
change can add dependency versions to a partial restore, so monitor compressed
entry size when reviewing dependency updates. Bump the cache namespace when
changing toolchain policy, ABI, release features, LTO, codegen units, target
architecture or cache contents.

Keep the populated desktop v2 namespace. Only when it has no exact or partial
match, a restore-only action tries v1 with its original five paths, including
the Cargo registry and Git directories. Cache paths contribute to GitHub's
cache version, so adding a v1 restore-key to the v2 action would not migrate
the old archive. The fallback remains inside the same OS, architecture and
compiler namespace. It cleans the legacy first-party weather crate; the
application clean still runs immediately before the production build. Only
the dependency-only v2 archive is saved after successful verification.

If both generations miss, the ordinary full build remains available. Do not
hide migration costs in warm-build comparisons or broaden compatibility
prefixes to accept a previously built application.

## Frozen scientific backend

Keep the default one-folder layout and the CPython 3.13.16 Windows x64 ABI.
The packaging specification recursively collects the app, SHARPpy decoders
and dynamically loaded provider modules. MetPy, Pint, numcodecs, pyproj,
netCDF4 and cftime use their maintained PyInstaller hooks and ordinary import
analysis, with installed distribution metadata retained for licensing and
runtime discovery. Dependency test trees and bytecode cache directories are
excluded from recursive collection. netCDF4's HDF5 compression-plugin DLLs are
collected explicitly in addition to the hook's external delvewheel libraries.

The spec explicitly includes `__main__` in Analysis exclusions. PyInstaller
already excludes that module while building its graph; declaring it before
Analysis prevents an in-place exclusion-list change from invalidating the next
warm analysis. This does not exclude the launcher script or alter its runtime
`__main__` behavior.

Do not assume a local warm analysis cache is portable to a fresh runner.
Analysis TOCs contain absolute paths and track input modification times;
fresh dependency installations can invalidate them even with identical package
versions. The workflow deliberately does not cache entire freezer work trees
or installed environments.

Official release mode still requires external, matching wheel metadata,
the native Rust extension, PSF/OpenSSL license files and the hermetic Windows
DLL search path. The workflow obtains the OpenSSL license from the pinned
CPython dependency source. A missing license remains a build error.

## Verification and scheduling

The desktop package clean remains before the production Tauri build. Removing
it requires controlled executable and installer checks for source, backend,
version, configuration, icons/resources, features and lockfile changes.
Successful unchanged-source builds alone do not establish that safety.

`npm ci` runs before the step that receives the updater signing key. Preserve
the key's existing scope for the Tauri installer/signature operation. Never
cache it or provide it to dependency installation for convenience.

The final updater test runs after the optimized desktop and signed installer
have been copied into release staging. Only that test invocation overrides
`profile.release.package.sharppy-reimagined.opt-level=0`, avoiding a second
optimization pass over the unused application library and test harness. Its
Tauri/updater dependencies retain their release profile; the shipped desktop,
scientific libraries and installer build do not receive the override. The test
still verifies actual installer bytes with the installed updater plugin and
rejects corruption and signed-version substitution. Do not move artifact
staging after this command or package its test-only executable as the desktop.

Preserve the frozen model-fetch check, PE versions, native numerical checks,
packaged desktop import/render check, installed upgrade, installed preferences
and restart acceptance, updater corruption/version-substitution checks, SBOM,
checksums, provenance and published-release immutability. Do not serialize
the Windows build behind the already parallel scientific and desktop lanes.

For performance comparisons, record fresh and warm target/analysis caches
separately, include archive restore/save time, and distinguish hosted job time
from total workflow time. Keep raw diagnostics outside the repository. A local
unsigned executable or a prior release's signed installer does not establish
that a new signed NSIS release or installed upgrade has passed.

## Release #72 / #73 regression

The GitHub jobs API and timestamped logs identify #72 as run `42190371418`
(v3.5.4, `84056a2634ae53028b931c8728a8a7e8b97bc07c`) and #73 as run
`42338901605` (v3.5.5, `bbdcb8414feb9bde89bcdefdf8cb05a3f3ac2a9e`).
The end-to-end increase is 301 seconds; 298 are in the Windows job.

| Measured interval | #72 | #73 | Delta |
|---|---:|---:|---:|
| Complete workflow | 1,093 s | 1,394 s | +301 s |
| Windows job, including post-job hooks | 889 s | 1,187 s | +298 s |
| Desktop cache restore | 36 s, partial v1 hit | 1 s, v2 miss | -35 s |
| Frontend install plus desktop/installer build | 199 s | 457 s | +258 s |
| Scientific wheel build and install | 132 s | 166 s | +34 s |
| Node setup | 11 s | 29 s | +18 s |
| Final updater verification | 107 s | 98 s | -9 s |
| All Windows post-job hooks | 47 s | 60 s | +13 s |

The desktop Cargo log reports 1m40s versus 5m39s: #72 compiled the two
first-party crates using restored dependencies, while #73 compiled the Tauri
dependency graph after the path/namespace migration missed. NSIS itself took
62.64 versus 70.87 seconds. The three Rust cache saves in #73 took 19 seconds
combined; first-party post-build cleanup took one second. Neither accounts
for five minutes of regression. `npm ci` moved out of the desktop build step,
so compare its combined interval rather than adding its new step twice.

#73 also missed both new scientific/download caches. It is a cold-cache
measurement, not evidence of steady-state cache performance. Scientific source
and dependency inputs differ from #72, so its extra compilation time cannot
be assigned solely to caching. Cold PyInstaller collection/startup decreased
about five seconds, while graph analysis increased about ten; whole freezing
rose from 132 to 137 seconds. This does not establish a hosted freezer speedup.

For a hosted comparison of this correction, use the existing non-publishing
candidate path, twice without clearing caches:

```powershell
gh workflow run release.yml --repo ShianMike/SHARPpy-Reimagined `
  --ref '<branch-containing-the-corrected-workflow>' `
  -f tag=v3.5.5 -F publish_release=false
```

Run and inspect each candidate to completion before starting the next. The
existing tag pins the same v3.5.5 application source; the selected branch
supplies the workflow. Publication stays disabled, and the existing installed
upgrade baseline remains v3.5.4. Record run IDs, matched cache keys, all job
and step times, post-job compression/upload, and the full critical path.
Compare like cache states and retain the first candidate's startup cost.
Passing local checks or a warmed candidate alone does not prove a hosted
end-to-end improvement over the 1,093-second baseline.
