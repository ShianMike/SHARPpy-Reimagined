# Linux and macOS desktop builds

| Platform | Package | System |
| --- | --- | --- |
| Linux x64 | `*-linux-x64.deb` | Ubuntu 24.04 x64 |
| Mac with Apple Silicon | `*-macos-arm64.dmg` or `*-macos-arm64-preview.dmg` | macOS 15 Sequoia or later |
| Intel Mac | `*-macos-x64.dmg` or `*-macos-x64-preview.dmg` | macOS 15 Sequoia or later |

Each installer includes Python, the Rust scientific extension, Qt rendering,
and the model and WRF libraries. Match the download to your computer. On
Ubuntu, open the package with your package manager or run
`sudo apt install ./SHARPpy-Reimagined-<version>-linux-x64.deb`, using the
downloaded filename. On macOS, open the disk image and drag **SHARPpy
Reimagined** into Applications. Use **Settings → Updates → GitHub Releases**
to download a newer build and install it. Native GIF clipboard copying is
available on Windows. The Save GIF action works on Linux and macOS.

Find published installers on the
[releases page](https://github.com/ShianMike/SHARPpy-Reimagined/releases).
Ubuntu and Mac candidates are also available from GitHub Actions. Mac packages
labeled `preview` use ad-hoc signatures. macOS may prompt for approval when
opening a preview build. Verify the origin before using
**Privacy & Security → Open Anyway**.
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

Actions caches Ubuntu package downloads by the current APT install plan and
macOS Cargo dependencies by architecture, Rust compiler, Xcode SDK, Python
version and the lockfiles. Every run still resolves the APT packages,
rebuilds the first-party code, freezes the backend and checks the installed
app. The preview Mac packages use ad-hoc signatures. Enabling notarization
signs the bundled code with Developer ID and notarizes the finished package.

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

On Ubuntu, use the Tauri package to assemble the installer with the complete
backend and its library links:

```bash
target=x86_64-unknown-linux-gnu
npm --prefix frontend run tauri -- build --target "$target" \
  --config src-tauri/tauri.linux.release.conf.json -- --locked
version=$(node -p 'require("./frontend/package.json").version')
bash packaging/package_linux_desktop.sh \
  "src-tauri/target/$target/release/bundle/deb/SHARPpy Reimagined_${version}_amd64.deb" \
  "SHARPpy-Reimagined-v$version-linux-x64.deb"
```

On macOS, set `target` to `aarch64-apple-darwin` or `x86_64-apple-darwin`
on the matching architecture, then run:

```bash
export APPLE_SIGNING_IDENTITY="${APPLE_SIGNING_IDENTITY:--}"
bash packaging/prepare_macos_backend.sh "$target"
npm --prefix frontend run tauri -- build --target "$target" \
  --config src-tauri/tauri.macos.release.conf.json -- --locked
```

The Release workflow includes the tested Linux package and both Mac packages
in the release, checksums, and GitHub build attestations. Enable
`notarize_macos` when dispatching Release to publish Developer ID packages.
The repository needs
`APPLE_CERTIFICATE` (a base64 Developer ID Application `.p12`),
`APPLE_CERTIFICATE_PASSWORD`, `APPLE_ID`, `APPLE_PASSWORD` (an app-specific
password), and `APPLE_TEAM_ID`. The workflow signs, notarizes, and verifies
both packages before publication.
[Tauri's macOS signing documentation](https://v2.tauri.app/distribute/sign/macos/)
