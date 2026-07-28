param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string] $Path,

    [switch] $ImageOnly
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$resolvedPath = (Resolve-Path -LiteralPath $Path).Path
if ([System.Threading.Thread]::CurrentThread.ApartmentState -ne "STA") {
    $pwshPath = (Get-Process -Id $PID).Path
    $arguments = @(
        "-NoProfile",
        "-Sta",
        "-File",
        $PSCommandPath,
        "-Path",
        $resolvedPath
    )
    if ($ImageOnly) {
        $arguments += "-ImageOnly"
    }
    & $pwshPath @arguments
    exit $LASTEXITCODE
}

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$image = [System.Drawing.Image]::FromFile($resolvedPath)
try {
    $data = [System.Windows.Forms.DataObject]::new()
    $data.SetImage($image)
    if (-not $ImageOnly) {
        $files = [System.Collections.Specialized.StringCollection]::new()
        [void] $files.Add($resolvedPath)
        $data.SetFileDropList($files)
    }
    [System.Windows.Forms.Clipboard]::SetDataObject($data, $true)
}
finally {
    $image.Dispose()
}

Write-Output "Copied image to the Windows clipboard: $resolvedPath"
