[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string] $Artifacts,
    [Parameter(Mandatory = $true)][string] $ReportDirectory,
    [switch] $PublicUpdate
)
$ErrorActionPreference = 'Stop'
if ($env:GITHUB_ACTIONS -ne 'true' -or $env:RUNNER_ENVIRONMENT -ne 'github-hosted') {
    throw 'Native acceptance installs software only on a disposable hosted runner'
}
$report = [IO.Path]::GetFullPath($ReportDirectory)
if (-not $report.StartsWith(([IO.Path]::GetFullPath($env:RUNNER_TEMP).TrimEnd('\') + '\'), [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Native acceptance must use a new directory inside RUNNER_TEMP'
}
if (Test-Path -LiteralPath $report) { throw 'Native acceptance report directory already exists' }
New-Item -ItemType Directory -Path $report | Out-Null
$artifactsRoot = (Resolve-Path -LiteralPath $Artifacts).Path
$install = Join-Path $report 'installed'
$env:WEBVIEW2_USER_DATA_FOLDER = Join-Path $report 'webview-profile'
$env:WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS = '--remote-debugging-port=9224'
$env:SHARPPY_DESKTOP_CDP = 'http://127.0.0.1:9224'
$env:E2E_SCREENSHOT_DIR = $report
$desktop = $null
$present = @()
$starts = 0
$trackedProcesses = @{}
$debugPolicy = 'HKLM:\SOFTWARE\Policies\Microsoft\Edge\WebView2\AdditionalBrowserArguments'
$debugApp = 'sharppy-reimagined.exe'
if ((Get-ItemProperty -LiteralPath $debugPolicy -Name $debugApp -ErrorAction SilentlyContinue).$debugApp) {
    throw 'Refusing to replace an existing app-specific WebView2 test policy'
}
# Microsoft-supported app-scoped override for an elevated disposable runner.
New-Item -Path $debugPolicy -Force | Out-Null
New-ItemProperty -LiteralPath $debugPolicy -Name $debugApp -Value '--remote-debugging-address=127.0.0.1 --remote-debugging-port=9224' -PropertyType String | Out-Null
$gitSource = (& git rev-parse HEAD).Trim()
Get-CimInstance Win32_VideoController | Select-Object Name,DriverVersion,VideoProcessor,AdapterRAM |
    ConvertTo-Json | Set-Content -LiteralPath (Join-Path $report 'graphics.json')
[ordered]@{ source = $gitSource; runner = $env:RUNNER_OS; image = $env:ImageVersion; public_update = [bool]$PublicUpdate } |
    ConvertTo-Json | Set-Content -LiteralPath (Join-Path $report 'environment.json')
Get-CimInstance Win32_OperatingSystem | Select-Object Caption,Version,BuildNumber,OSArchitecture |
    ConvertTo-Json | Set-Content -LiteralPath (Join-Path $report 'windows.json')

function Verify-Installer([string] $Directory, [string] $Version) {
    $file = Join-Path $Directory "SHARPpy-Reimagined-v$Version-windows-x64-setup.exe"
    $sums = Get-Content -LiteralPath (Join-Path $Directory "SHARPpy-Reimagined-v$Version-SHA256SUMS.txt")
    $expected = @($sums | Where-Object { $_ -match ('^[a-f0-9]{64}  ' + [regex]::Escape([IO.Path]::GetFileName($file)) + '$') })
    if ($expected.Count -ne 1 -or (Get-FileHash -LiteralPath $file).Hash.ToLowerInvariant() -ne $expected[0].Substring(0,64)) {
        throw "Installer checksum mismatch: $Version"
    }
    return $file
}
function Install-Version([string] $File) {
    $process = Start-Process -FilePath $File -ArgumentList "/S /D=$install" -WindowStyle Hidden -PassThru
    if (-not $process.WaitForExit(180000)) { $process.Kill(); throw 'Installer timed out' }
    if ($process.ExitCode -ne 0) { throw "Installer exit code: $($process.ExitCode)" }
}
function Start-Desktop {
    $executables = @(Get-ChildItem -LiteralPath $install -Filter '*.exe' -File | Where-Object Name -NotLike '*uninstall*')
    if ($executables.Count -ne 1) { throw 'Installed desktop executable is not unique' }
    $script:starts++
    $script:trackedProcesses = @{}
    $script:desktop = Start-Process -FilePath $executables[0].FullName -PassThru `
        -RedirectStandardOutput (Join-Path $report "desktop-$starts.log") -RedirectStandardError (Join-Path $report "desktop-$starts-error.log")
    $script:desktop | Select-Object Id,SessionId,MainWindowHandle,MainWindowTitle |
        ConvertTo-Json | Set-Content -LiteralPath (Join-Path $report "desktop-$starts.json")
    $lastError = ''
    for ($attempt = 0; $attempt -lt 45; $attempt++) {
        if ($script:desktop.HasExited) { throw 'Desktop exited before its WebView2 started' }
        try {
            $pages = Invoke-RestMethod "$env:SHARPPY_DESKTOP_CDP/json/list" -TimeoutSec 2 -NoProxy
            $pages | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $report "startup-pages-$starts.json")
            if (@($pages | Where-Object url -Like 'http*tauri.localhost*').Count -gt 0) { return }
        } catch { $lastError = $_.ToString() }
        Start-Sleep -Seconds 1
    }
    Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -eq $script:desktop.Id -or $_.Name -eq 'msedgewebview2.exe' } |
        Select-Object ProcessId,ParentProcessId,ExecutablePath,CommandLine | ConvertTo-Json -Depth 5 |
        Set-Content -LiteralPath (Join-Path $report "startup-processes-$starts.json")
    throw "Desktop WebView2 did not expose its test endpoint: $lastError"
}
function Stop-Desktop {
    if ($null -ne $script:desktop -and -not $script:desktop.HasExited) {
        if (-not $script:desktop.CloseMainWindow() -or -not $script:desktop.WaitForExit(30000)) {
            throw 'Desktop did not close normally'
        }
    }
    Start-Sleep -Seconds 3
}
function Run-Phase([string] $Phase) {
    $env:SHARPPY_ACCEPTANCE_PHASE = $Phase
    $env:SHARPPY_MEASURE_OUT = Join-Path $report "$Phase.json"
    & ./node_modules/.bin/playwright.cmd test --config playwright.desktop.config.ts acceptance.spec.ts
    if ($LASTEXITCODE -ne 0) { throw "Native $Phase acceptance failed" }
}
function Process-Sample {
    $all = @(Get-CimInstance Win32_Process)
    # Keep surviving descendants after their parent exits; reject recycled PIDs.
    $ids = @([uint32]$script:desktop.Id) + @($all | Where-Object {
        $trackedProcesses.ContainsKey($_.ProcessId) -and $trackedProcesses[$_.ProcessId] -eq $_.CreationDate
    } | Select-Object -ExpandProperty ProcessId)
    do {
        $children = @($all | Where-Object { $_.ParentProcessId -in $ids -and $_.ProcessId -notin $ids } | Select-Object -ExpandProperty ProcessId)
        $ids += $children
    } while ($children.Count -gt 0)
    $owned = @($all | Where-Object ProcessId -In $ids)
    foreach ($process in $owned) { $script:trackedProcesses[$process.ProcessId] = $process.CreationDate }
    $processes = @($owned | ForEach-Object {
        $live = Get-Process -Id $_.ProcessId -ErrorAction SilentlyContinue
        if ($null -ne $live) {
            [ordered]@{ pid = $_.ProcessId; parent = $_.ParentProcessId; name = $_.Name; command = $_.CommandLine
                working = $live.WorkingSet64; private = $live.PrivateMemorySize64; handles = $live.HandleCount; threads = $live.Threads.Count }
        }
    })
    [ordered]@{ utc = [DateTime]::UtcNow.ToString('o'); processes = $processes } | ConvertTo-Json -Depth 5 -Compress |
        Add-Content -LiteralPath (Join-Path $report 'processes.ndjson')
    return $owned
}

Push-Location (Join-Path $PSScriptRoot '../frontend')
try {
    $previous = Join-Path $report 'previous'
    New-Item -ItemType Directory -Path $previous | Out-Null
    & gh release download v3.2.0 --repo $env:GITHUB_REPOSITORY --pattern '*-setup.exe' --pattern '*-SHA256SUMS.txt' --dir $previous
    if ($LASTEXITCODE -ne 0) { throw 'Previous stable release download failed' }
    $baseline = Verify-Installer $previous '3.2.0'
    $candidate = Verify-Installer $artifactsRoot '3.2.1'
    Install-Version $baseline
    Start-Desktop
    Run-Phase 'seed'
    if ($PublicUpdate) {
        $executablePath = $desktop.Path
        Run-Phase 'update'
        # The plugin/installer must restart the desktop itself. Do not start it here.
        $oldId = $desktop.Id
        for ($attempt = 0; $attempt -lt 180; $attempt++) {
            $restarted = @(Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -eq $executablePath -and $_.ProcessId -ne $oldId })
            if ($restarted.Count -eq 1) { $desktop = Get-Process -Id $restarted[0].ProcessId; break }
            Start-Sleep -Seconds 2
        }
        if ($desktop.Id -eq $oldId) { throw 'Interactive updater did not restart the installed desktop' }
    } else {
        Stop-Desktop
        Install-Version $candidate
        Start-Desktop
    }
    Run-Phase 'upgrade'
    Stop-Desktop
    Start-Desktop
    Run-Phase 'restart'
    Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'msedgewebview2.exe' -and $_.CommandLine -like "*$report*" } |
        Select-Object ProcessId,ParentProcessId,ExecutablePath,CommandLine |
        ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $report 'webview2.json')
    if (-not $PublicUpdate) {
        $tool = Join-Path $report 'PresentMon-2.6.0-x64.exe'
        & gh release download v2.6.0 --repo GameTechDev/PresentMon --pattern 'PresentMon-2.6.0-x64.exe' --dir $report
        if ($LASTEXITCODE -ne 0) { throw 'PresentMon download failed' }
        Get-FileHash -LiteralPath $tool | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $report 'presentmon-sha256.json')
        $env:SHARPPY_ACCEPTANCE_PHASE = 'soak'
        $env:SHARPPY_MEASURE_OUT = Join-Path $report 'soak.json'
        $workload = Start-Process -FilePath (Join-Path $PWD 'node_modules/.bin/playwright.cmd') -ArgumentList 'test --config playwright.desktop.config.ts acceptance.spec.ts' `
            -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $report 'workload.log') -RedirectStandardError (Join-Path $report 'workload-error.log')
        $started = [DateTime]::UtcNow
        $captures = 0
        $firstCaptureChecked = $false
        $lastWorkloadLine = $null
        Write-Host 'Starting 120-minute native memory soak; progress reports cumulative elapsed time.'
        while (-not $workload.HasExited) {
            if (([DateTime]::UtcNow - $started).TotalMinutes -gt 149) { $workload.Kill(); throw 'Native soak exceeded its deadline' }
            $owned = @(Process-Sample)
            if (Test-Path -LiteralPath (Join-Path $report 'workload.log')) {
                $line = Get-Content -LiteralPath (Join-Path $report 'workload.log') -Tail 1
                if ($line -and $line -cne $lastWorkloadLine) {
                    Write-Host $line
                    $lastWorkloadLine = $line
                }
            }
            if ($captures -lt 2 -and (Test-Path -LiteralPath (Join-Path $report "capture-$captures-ready.json"))) {
                $otherWebviews = @(Get-CimInstance Win32_Process -Filter "Name='msedgewebview2.exe'" | Where-Object ProcessId -NotIn $owned.ProcessId)
                if ($otherWebviews.Count -gt 0) { throw 'Refusing to capture another application WebView2 process' }
                $csv = Join-Path $report "presentation-$captures.csv"
                $present += Start-Process -FilePath $tool -PassThru -WindowStyle Hidden `
                    -RedirectStandardOutput (Join-Path $report "capture-$captures.log") -RedirectStandardError (Join-Path $report "capture-$captures-error.log") -ArgumentList `
                    "--process_name msedgewebview2.exe --process_name sharppy-reimagined.exe --output_file $csv --session_name SHARPPyAcceptance-$env:GITHUB_RUN_ID-$captures --timed 120 --terminate_after_timed --no_console_stats --write_display_metadata --v2_metrics"
                $captures++
            }
            if (-not $firstCaptureChecked -and $present.Count -gt 0 -and -not $present[0].HasExited -and
                ([DateTime]::UtcNow - $present[0].StartTime.ToUniversalTime()).TotalSeconds -gt 180) {
                $present[0].Kill()
                $captureError = 'UNVERIFIED native presentation: capture timed out; the 120-minute memory soak continues.'
                $captureError | Set-Content -LiteralPath (Join-Path $report 'presentation-error.log')
                Write-Host $captureError
                $firstCaptureChecked = $true
            }
            if (-not $firstCaptureChecked -and $present.Count -gt 0 -and $present[0].HasExited) {
                $displayed = @()
                if (Test-Path -LiteralPath (Join-Path $report 'presentation-0.csv')) {
                    $displayed = @(Import-Csv -LiteralPath (Join-Path $report 'presentation-0.csv') | Where-Object {
                        $_.DisplayedTime -match '^\d+(\.\d+)?$' -and [double]$_.DisplayedTime -gt 0
                    })
                }
                if ($present[0].ExitCode -ne 0 -or $displayed.Count -lt 100) {
                    $captureError = "UNVERIFIED native presentation: capture incomplete (exit=$($present[0].ExitCode), displayed frames=$($displayed.Count)); the 120-minute memory soak continues."
                    $captureError | Set-Content -LiteralPath (Join-Path $report 'presentation-error.log')
                    Write-Host $captureError
                } else {
                    Write-Host "Native capture collected $($displayed.Count) displayed frames; final presentation analysis pending."
                }
                $firstCaptureChecked = $true
            }
            Start-Sleep -Seconds 30
        }
        $workload.WaitForExit()
        if ($workload.ExitCode -ne 0) { throw "Native soak workload failed: $($workload.ExitCode)" }
        foreach ($capture in $present) {
            if (-not $capture.WaitForExit(180000)) { $capture.Kill(); 'Native capture exceeded its deadline' | Add-Content -LiteralPath (Join-Path $report 'presentation-error.log') }
        }
        if ($captures -ne 2 -or -not (Test-Path -LiteralPath (Join-Path $report 'soak-complete.json'))) { throw 'Native acceptance did not complete both presentation captures and the workload' }
    }
    Stop-Desktop
} finally {
    Pop-Location
    if ($null -ne $desktop -and -not $desktop.HasExited) { $desktop.CloseMainWindow() | Out-Null }
    Remove-ItemProperty -LiteralPath $debugPolicy -Name $debugApp
}
