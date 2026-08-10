[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string] $Executable,

    [Parameter(Mandatory = $true)]
    [string] $ExpectedVersion,

    [Parameter(Mandatory = $true)]
    [ValidateSet("Signed", "Unsigned")]
    [string] $SigningMode
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

$signature = Get-AuthenticodeSignature -LiteralPath $resolvedExecutable
if ($SigningMode -eq "Signed" -and $signature.Status -ne "Valid") {
    throw "Expected a valid Authenticode signature on ${resolvedExecutable}, got $($signature.Status)"
}
if ($SigningMode -eq "Unsigned" -and $signature.Status -ne "NotSigned") {
    throw "Expected an explicitly unsigned executable at ${resolvedExecutable}, got $($signature.Status)"
}

[pscustomobject]@{
    executable = $resolvedExecutable
    file_version = $versionInfo.FileVersion
    product_version = $versionInfo.ProductVersion
    original_filename = $versionInfo.OriginalFilename
    signing_mode = $SigningMode.ToLowerInvariant()
    signature_status = [string] $signature.Status
} | ConvertTo-Json
