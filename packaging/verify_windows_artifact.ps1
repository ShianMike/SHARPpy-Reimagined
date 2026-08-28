[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string] $Executable,

    [Parameter(Mandatory = $true)]
    [string] $ExpectedVersion
)

$ErrorActionPreference = "Stop"
$resolvedExecutable = (Resolve-Path -LiteralPath $Executable).Path
$versionInfo = (Get-Item -LiteralPath $resolvedExecutable).VersionInfo

if ($versionInfo.FileVersion -ne $ExpectedVersion) {
    throw "FileVersion mismatch for ${resolvedExecutable}: expected $ExpectedVersion, got $($versionInfo.FileVersion)"
}
if ($versionInfo.ProductVersion -ne $ExpectedVersion) {
    throw "ProductVersion mismatch for ${resolvedExecutable}: expected $ExpectedVersion, got $($versionInfo.ProductVersion)"
}
if ($versionInfo.OriginalFilename -ne "SHARPpy-Reimagined.exe") {
    throw "Unexpected OriginalFilename for ${resolvedExecutable}: $($versionInfo.OriginalFilename)"
}

[pscustomobject]@{
    executable = $resolvedExecutable
    file_version = $versionInfo.FileVersion
    product_version = $versionInfo.ProductVersion
    original_filename = $versionInfo.OriginalFilename
} | ConvertTo-Json
