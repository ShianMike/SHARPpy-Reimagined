function Add-RetiredRuntimeFixtures([string] $BackendRoot, [version] $PreviousVersion) {
    $inventory = Join-Path $BackendRoot '.sharppy-runtime-files'
    $hasInventory = Test-Path -LiteralPath $inventory -PathType Leaf
    if ($hasInventory) {
        $header = @(Get-Content -LiteralPath $inventory -TotalCount 2)
        if ($header.Count -ne 2 -or
            $header[0] -cne 'io.github.shianmike.sharppy-reimagined/runtime-files/v1' -or
            $header[1] -ne $PreviousVersion.ToString()) {
            throw 'Installed baseline runtime inventory is invalid'
        }
    } elseif ($PreviousVersion -ge [version]'3.8.4') {
        throw 'Installed baseline runtime inventory is missing'
    }

    $metadataDirectory = '_internal/sharpmod_rs-0.0.0-upgrade-check.dist-info'
    $relativePaths = @(
        '_internal/obsolete-upgrade-check.pyc'
        'sharpmod_rs-0.0.0-upgrade-check.whl'
        "$metadataDirectory/METADATA"
    )
    $paths = foreach ($relative in $relativePaths) {
        $path = Join-Path $BackendRoot $relative
        if (Test-Path -LiteralPath $path) { throw "Fixture already exists: $path" }
        $parent = Split-Path -Parent $path
        if (!(Test-Path -LiteralPath $parent -PathType Container)) {
            New-Item -ItemType Directory -Path $parent | Out-Null
        }
        'Retired bundled runtime fixture' | Set-Content -LiteralPath $path -Encoding utf8
        $path
    }
    # Declare these synthetic files as old bundled resources in the disposable install.
    # Older installers without an inventory still exercise legacy code-file detection.
    if ($hasInventory) {
        Add-Content -LiteralPath $inventory -Value $relativePaths -Encoding utf8
    }
    [pscustomobject]@{
        Files = $paths
        MetadataDirectory = Join-Path $BackendRoot $metadataDirectory
    }
}
