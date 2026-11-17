[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string] $PreviousInstaller,
    [Parameter(Mandatory = $true)][string] $PreviousVersion,
    [Parameter(Mandatory = $true)][string] $Installer,
    [Parameter(Mandatory = $true)][string] $ExpectedVersion,
    [Parameter(Mandatory = $true)][string] $Sample,
    [Parameter(Mandatory = $true)][string] $ReportDirectory
)
$ErrorActionPreference = 'Stop'
# NSIS writes per-user uninstall registration; use only a disposable hosted account.
if ($env:GITHUB_ACTIONS -ne 'true' -or $env:RUNNER_ENVIRONMENT -ne 'github-hosted' -or [string]::IsNullOrWhiteSpace($env:RUNNER_TEMP)) {
    throw 'Installed-upgrade verification requires a disposable GitHub Actions runner account'
}
$reportRoot = [System.IO.Path]::GetFullPath($ReportDirectory)
$temporaryRoot = [System.IO.Path]::GetFullPath($env:RUNNER_TEMP).TrimEnd('\') + '\'
if (-not $reportRoot.StartsWith($temporaryRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'Upgrade report/install directory must be inside RUNNER_TEMP'
}
$installRoot = Join-Path $reportRoot 'installed'
if (Test-Path -LiteralPath $reportRoot) { throw 'Upgrade verification requires a new directory' }
New-Item -ItemType Directory -Path $reportRoot | Out-Null
$sampleFile = (Resolve-Path -LiteralPath $Sample).Path
$savedFile = Join-Path $reportRoot 'saved-sounding.npz'
Copy-Item -LiteralPath $sampleFile -Destination $savedFile
$sampleHash = (Get-FileHash -LiteralPath $savedFile -Algorithm SHA256).Hash

function Install-Candidate([string] $Path, [string] $Version, [string] $Phase) {
    $resolvedInstaller = (Resolve-Path -LiteralPath $Path).Path
    # NSIS requires /D as the last argument, without quotes, including paths with spaces.
    $process = Start-Process -FilePath $resolvedInstaller -WindowStyle Hidden -PassThru `
        -ArgumentList "/S /D=$installRoot"
    if (-not $process.WaitForExit(180000)) { $process.Kill(); throw "$Phase installation timed out" }
    if ($process.ExitCode -ne 0) { throw "$Phase installation failed: $($process.ExitCode)" }
    $executable = Join-Path $installRoot 'SHARPpy Reimagined.exe'
    if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
        $executables = @(Get-ChildItem -LiteralPath $installRoot -Filter '*.exe' -File | Where-Object Name -NotLike '*uninstall*')
        if ($executables.Count -ne 1) { throw "$Phase has no unique installed desktop executable" }
        $executable = $executables[0].FullName
    }
    & "$PSScriptRoot/verify_desktop_artifact.ps1" -Executable $executable -ExpectedVersion $Version `
        -Sample $savedFile -Report (Join-Path $reportRoot "$Phase-runtime.json") `
        -RequireWinter:($Phase -eq 'upgraded') | Out-Host
    return $executable
}

$previousExecutable = Install-Candidate $PreviousInstaller $PreviousVersion 'previous'
$marker = Join-Path $installRoot 'upgrade-preservation-check.txt'
'User-created file retained during in-place upgrade.' | Set-Content -LiteralPath $marker -Encoding utf8
$markerHash = (Get-FileHash -LiteralPath $marker -Algorithm SHA256).Hash
$staleCode = Join-Path $installRoot 'backend/_internal/obsolete-upgrade-check.pyc'
$staleWheel = Join-Path $installRoot 'backend/sharpmod_rs-0.0.0-test.whl'
$staleMetadata = Join-Path $installRoot 'backend/_internal/sharpmod_rs-0.0.0.dist-info'
New-Item -ItemType Directory -Path $staleMetadata | Out-Null
'Old installed code' | Set-Content -LiteralPath $staleCode
'Old installed wheel' | Set-Content -LiteralPath $staleWheel
'Old installed package metadata' | Set-Content -LiteralPath (Join-Path $staleMetadata 'METADATA')
$backendUserFile = Join-Path $installRoot 'backend/saved-user-sounding.spc'
Copy-Item -LiteralPath $sampleFile -Destination $backendUserFile
$backendUserHash = (Get-FileHash -LiteralPath $backendUserFile -Algorithm SHA256).Hash
$candidateExecutable = Install-Candidate $Installer $ExpectedVersion 'upgraded'
if ($previousExecutable -ne $candidateExecutable) { throw 'Upgrade changed the installation path' }
if ((Get-FileHash -LiteralPath $marker -Algorithm SHA256).Hash -ne $markerHash -or `
    (Get-FileHash -LiteralPath $savedFile -Algorithm SHA256).Hash -ne $sampleHash -or `
    (Get-FileHash -LiteralPath $backendUserFile -Algorithm SHA256).Hash -ne $backendUserHash) {
    throw 'Upgrade modified preserved data'
}
foreach ($stale in @($staleCode, $staleWheel, $staleMetadata)) {
    if (Test-Path -LiteralPath $stale) { throw "Obsolete installed runtime entry remains: $stale" }
}
$cleanup = Get-Content -LiteralPath (Join-Path $installRoot 'installer-cleanup.json') -Raw | ConvertFrom-Json
if ($cleanup.ok -ne $true -or $cleanup.version -ne $ExpectedVersion -or $cleanup.removed_files -lt 3) {
    throw 'Installed runtime cleanup was not verified'
}
[ordered]@{
    ok = $true
    previous_version = $PreviousVersion
    version = $ExpectedVersion
    installed_executable = $candidateExecutable
    installer_sha256 = (Get-FileHash -LiteralPath $Installer -Algorithm SHA256).Hash.ToLowerInvariant()
    preserved_sounding_sha256 = $sampleHash.ToLowerInvariant()
    preserved_user_file = $true
    obsolete_runtime_removed = $true
    cleanup = $cleanup
    limitation = 'Checks silent NSIS in-place upgrade, frozen runtime, rendering and saved files; browser preference migration and interactive updater restart still require the desktop acceptance run.'
} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $reportRoot 'upgrade.json') -Encoding utf8
