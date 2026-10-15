[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string] $Executable,
    [Parameter(Mandatory = $true)][string] $ExpectedVersion,
    [Parameter(Mandatory = $true)][string] $Sample,
    [Parameter(Mandatory = $true)][string] $Report
)
$ErrorActionPreference = "Stop"
$appExecutable = (Resolve-Path -LiteralPath $Executable).Path
$sampleFile = (Resolve-Path -LiteralPath $Sample).Path
$reportFile = [System.IO.Path]::GetFullPath($Report)
$versionInfo = (Get-Item -LiteralPath $appExecutable).VersionInfo
if ($versionInfo.FileVersion -ne $ExpectedVersion -or $versionInfo.ProductVersion -ne $ExpectedVersion) {
    throw "Desktop executable version does not match $ExpectedVersion"
}
$packagedBackend = Join-Path (Split-Path -Parent $appExecutable) 'backend/sharppy-backend.exe'
if (-not (Test-Path -LiteralPath $packagedBackend -PathType Leaf)) {
    throw "The desktop package is missing backend/sharppy-backend.exe"
}
$previousOverride = $env:SHARPPY_PYTHON
try {
    $env:SHARPPY_PYTHON = $null
    $startedUtc = [DateTime]::UtcNow
    $process = Start-Process -FilePath $appExecutable -WindowStyle Hidden `
        -WorkingDirectory ([System.IO.Path]::GetTempPath()) `
        -ArgumentList '--desktop-runtime-check', "`"$reportFile`"", "`"$sampleFile`"" -PassThru
    if (-not $process.WaitForExit(240000)) {
        $process.Kill()
        throw "Desktop runtime check exceeded four minutes"
    }
    if ($process.ExitCode -ne 0) {
        if (Test-Path -LiteralPath $reportFile) { Get-Content -LiteralPath $reportFile }
        throw "Desktop runtime check failed with exit code $($process.ExitCode)"
    }
    if ((Get-Item -LiteralPath $reportFile).LastWriteTimeUtc -lt $startedUtc) {
        throw "Desktop runtime report was not produced by this run"
    }
    $result = Get-Content -LiteralPath $reportFile -Raw | ConvertFrom-Json
    if (-not $result.ok -or $result.health.app.version -ne $ExpectedVersion -or `
        $result.health.python.sharpmodVersion -ne $ExpectedVersion -or `
        $result.health.scientificBackend.activeBackend -ne 'rust' -or `
        $result.health.scientificBackend.rustVersion -ne $ExpectedVersion -or `
        $result.station_count -lt 1 -or $result.sounding.levelCount -lt 2 -or $result.image_bytes -lt 24) {
        throw "Desktop runtime report failed its backend, import, or rendering checks"
    }
    $result | ConvertTo-Json -Depth 8
} finally {
    $env:SHARPPY_PYTHON = $previousOverride
}
