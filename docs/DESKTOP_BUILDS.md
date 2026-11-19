# Linux and macOS desktop builds

| Platform | Package | System |
| --- | --- | --- |
| Linux x64 | `*-linux-x64.deb` | Ubuntu 24.04 x64 |
| Mac with Apple Silicon | `*-macos-arm64.dmg` | macOS 15 Sequoia or later |
| Intel Mac | `*-macos-x64.dmg` | macOS 15 Sequoia or later |

Each installer includes Python, the Rust scientific extension, Qt rendering,
and the model and WRF libraries. Match the download to your computer. On
Ubuntu, open the package with your package manager or run
`sudo apt install ./SHARPpy-Reimagined-<version>-linux-x64.deb`, using the
downloaded filename. On macOS, open the disk image and drag **SHARPpy
Reimagined** into Applications. Use **Settings → Updates → GitHub Releases**
to download a newer build and install it. Native GIF clipboard copying is
available on Windows. The Save GIF action works on Linux and macOS.

The latest published v3.8.5 uses the Windows release workflow from its tag.
Linux and macOS installers are built from source or from GitHub Actions until
they appear on the [releases page](https://github.com/ShianMike/SHARPpy-Reimagined/releases).
For Mac downloads through GitHub Actions, the candidate has an ad-hoc signature.
macOS may prompt for approval when opening it. Verify the origin before using
**Privacy & Security → Open Anyway**. Public Mac releases use Apple notarization.
[Apple's guidance on opening apps](https://support.apple.com/en-us/102445)

## Develop on Linux or Mac

Use the project's Python 3.11–3.13 range, Node.js 24.12 or newer, stable Rust,
and the [Tauri system prerequisites](https://v2.tauri.app/start/prerequisites/).
On Ubuntu, Tauri builds need `libwebkit2gtk-4.1-dev`, `libxdo-dev`, and
`libssl-dev` in addition to the standard build tools. On macOS, install the
Xcode command-line tools and use a Python interpreter built for your Mac's
architecture.

From the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python scripts/install_sharppy_compat.py --extras era5,wrf,rust-build
sharpmod-rust-sync
npm --prefix frontend ci
npm --prefix frontend run tauri -- dev
```

## Validate and package a release

Run **Linux and macOS desktop packages** in GitHub Actions. For a branch that
adds the workflow, run **Frontend and desktop bridge** with
`unix_packages=true`. The job builds on Ubuntu 24.04 x64 and on both macOS 15
architectures. It installs the `.deb` or mounts the `.dmg` and runs the
desktop executable against the backend from that package. The report checks
the exact runtime versions, ecCodes/WRF imports, and Severe and Winter PNG
dimensions. Download `unix-package-*` for the builds and `unix-runtime-*`
for the reports.

To build the equivalent artifacts locally, use CPython 3.13.16 and start with
a clean virtual environment. Use Rust 1.99.1 and your native target. Build the
wheel and freeze the backend on the target OS. On macOS, export
`MACOSX_DEPLOYMENT_TARGET=15.0` before building.

```bash
export RUSTUP_TOOLCHAIN=1.99.1
export PIP_CONSTRAINT="$PWD/constraints/release.txt"
python -m pip install ".[render,era5,wrf]" pyinstaller maturin
python scripts/install_sharppy_compat.py --sharppy-only
wheel_dir=$(mktemp -d)
python -m maturin build --release --locked --manifest-path rust/sharpmod-rs/Cargo.toml \
  --interpreter python --out "$wheel_dir"
python -m pip install --no-deps "$wheel_dir"/sharpmod_rs-*.whl
SHARPMOD_REQUIRE_RUST=1 python -m PyInstaller packaging/sharpmod_backend.spec --noconfirm
npm --prefix frontend ci
```

On Ubuntu, build the Linux installer with
`npm --prefix frontend run tauri -- build --config src-tauri/tauri.linux.release.conf.json -- --locked`.
On macOS, set `target` to `aarch64-apple-darwin` or `x86_64-apple-darwin`
on the matching architecture, then run:

```bash
export APPLE_SIGNING_IDENTITY="${APPLE_SIGNING_IDENTITY:--}"
bash packaging/prepare_macos_backend.sh "$target"
npm --prefix frontend run tauri -- build --target "$target" \
  --config src-tauri/tauri.macos.release.conf.json -- --locked
```

The Release workflow includes Linux in the new release's checksums and
GitHub build attestations. To include both Mac packages, enable
`notarize_macos` when dispatching Release. The repository needs
`APPLE_CERTIFICATE` (a base64 Developer ID Application `.p12`),
`APPLE_CERTIFICATE_PASSWORD`, `APPLE_ID`, `APPLE_PASSWORD` (an app-specific
password), and `APPLE_TEAM_ID`. The workflow signs, notarizes, and verifies
both packages before publication.
[Tauri's macOS signing documentation](https://v2.tauri.app/distribute/sign/macos/)
