<#
inno_build.ps1
Usage: Run this on the Windows build machine after running build_windows.ps1 (or let this script run it).
It will locate Inno Setup's `ISCC.exe` and compile `installer.iss` into an installer.exe.

Examples:
  # try auto-detect Inno Setup
  .\inno_build.ps1

  # specify ISCC path explicitly
  .\inno_build.ps1 -ISCCPath 'C:\Program Files (x86)\Inno Setup 6\ISCC.exe'
#>

param(
    [string]$ISCCPath = "",
    [switch]$SkipBuild
)

function Write-Info($m){ Write-Host "[i] $m" }
function Write-Err($m){ Write-Host "[!] $m" -ForegroundColor Red }

$projectRoot = (Get-Location).Path
$buildScript = Join-Path $projectRoot 'build_windows.ps1'
$installerScript = Join-Path $projectRoot 'installer.iss'

if (-not $SkipBuild) {
    if (Test-Path $buildScript) {
        Write-Info "Running build_windows.ps1 to produce dist\\smtp_unlock..."
        try {
            & powershell -ExecutionPolicy Bypass -File $buildScript
        } catch {
            Write-Err "build_windows.ps1 failed: $_"
            exit 1
        }
    } else {
        Write-Err "build_windows.ps1 not found. Please run your build step first or pass -SkipBuild."
        exit 1
    }
}

if (-not (Test-Path $installerScript)) {
    Write-Err "installer.iss not found in project root."
    exit 1
}

if ([string]::IsNullOrWhiteSpace($ISCCPath) -or -not (Test-Path $ISCCPath)) {
    # try common default locations
    $candidates = @(
        "C:\\Program Files (x86)\\Inno Setup 6\\ISCC.exe",
        "C:\\Program Files\\Inno Setup 6\\ISCC.exe",
        "$env:LOCALAPPDATA\\Programs\\Inno Setup 6\\ISCC.exe"
    )
    foreach ($c in $candidates) {
        if (Test-Path $c) { $ISCCPath = $c; break }
    }
}

if ([string]::IsNullOrWhiteSpace($ISCCPath) -or -not (Test-Path $ISCCPath)) {
    Write-Err "ISCC.exe not found. Install Inno Setup and pass -ISCCPath or ensure default path exists."
    Write-Err "Download: https://jrsoftware.org/isdl.php"
    Write-Err "Example: .\inno_build.ps1 -ISCCPath 'C:\Program Files (x86)\Inno Setup 6\ISCC.exe'"
    exit 1
}

Write-Info "Using ISCC: $ISCCPath"

# Run Inno Setup Compiler
$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = $ISCCPath
$psi.Arguments = "`"$installerScript`""
$psi.RedirectStandardOutput = $true
$psi.RedirectStandardError = $true
$psi.UseShellExecute = $false

$proc = [System.Diagnostics.Process]::Start($psi)
$stdout = $proc.StandardOutput.ReadToEnd()
$stderr = $proc.StandardError.ReadToEnd()
$proc.WaitForExit()

Write-Host $stdout
if ($stderr) { Write-Err $stderr }

if ($proc.ExitCode -ne 0) {
    Write-Err "ISCC returned exit code $($proc.ExitCode)"
    exit $proc.ExitCode
}

Write-Info "Installer build complete. Check the 'installer' folder for output."
exit 0
