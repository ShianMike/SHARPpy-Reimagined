# Code signing policy

Official Windows downloads are built from the tagged source by
`.github/workflows/release.yml`. The workflow runs the release test suite,
builds on a GitHub-hosted Windows runner, validates the Python and Rust version
contract, and verifies every frozen executable before it can be published.

## Signing provider and release status

After the project's open-source application is approved, signed releases will
use **Free code signing provided by
[SignPath.io](https://signpath.io/), certificate by
[SignPath Foundation](https://signpath.org/)**. Every SignPath signing request
requires manual approval by the project approver.

The release manifest and `Authenticode-Signed` or `Authenticode-Unsigned`
marker are authoritative for a particular release. Until the SignPath
application is approved, the release workflow continues to label artifacts as
unsigned instead of implying that they have a trusted signature. A maintainer
may alternatively configure the workflow's PFX-based Authenticode provider.

Only these project-built launchers are signed with the project's certificate:

- the recommended one-folder `SHARPpy-Reimagined.exe` launcher;
- the portable single-file `SHARPpy-Reimagined.exe` launcher.

Bundled third-party open-source libraries are not represented as SHARPpy
Reimagined code and are not signed with the project's certificate. The
SignPath artifact configuration restricts the product name, product version,
file version, company name, copyright, and original filename before signing.

## Team roles

- Authors and committers: [ShianMike](https://github.com/ShianMike), the
  repository owner and direct maintainer.
- Reviewer: [ShianMike](https://github.com/ShianMike). Changes proposed by
  contributors without push access require maintainer review before merge.
- Signing approver: [ShianMike](https://github.com/ShianMike).

Maintainers must use multi-factor authentication for GitHub and the signing
provider. Signing credentials are stored as GitHub Actions secrets or in the
provider's hardware security module; they are never committed to this
repository or included in release artifacts.

## Privacy and network behavior

SHARPpy Reimagined contains no advertising, analytics, or telemetry and does
not automatically upload user files. The program transfers information to
networked systems only when the user explicitly requests remote weather data,
place search, or another documented online operation. Requests contain the
location, time, model, station, or provider credentials needed for that
operation. Downloaded data, preferences, saved locations, and caches remain on
the user's computer unless the user explicitly exports or shares them.

## Verification

On Windows, verify an extracted executable with:

```powershell
Get-AuthenticodeSignature .\SHARPpy-Reimagined.exe | Format-List Status,SignerCertificate,TimeStamperCertificate
```

A signed official release must report `Status: Valid`. Release assets also
include SHA-256 checksums, and GitHub build provenance can be verified with:

```powershell
gh attestation verify PATH-TO-DOWNLOADED-ASSET --repo ShianMike/SHARPpy-Reimagined
```

Report an unexpected or invalid signature through the repository's
[issue tracker](https://github.com/ShianMike/SHARPpy-Reimagined/issues).
