# build_windows.ps1
# Usage: run this on a Windows machine (PowerShell) from the project root.
# This script installs dependencies in a virtualenv, installs Playwright browsers,
# and builds a distributable folder using PyInstaller.

param(
    [int]$Workers = 4,
    [switch]$NoKill
)

function Stop-RunningApp {
    if ($NoKill) {
        Write-Host "== Skipping process cleanup because -NoKill was passed =="
        return
    }

    Write-Host "== Stopping old SMTP Unlock processes if any =="
    $names = @("smtp_unlock", "smtp_unlock_gui")
    foreach ($name in $names) {
        Get-Process -Name $name -ErrorAction SilentlyContinue | ForEach-Object {
            Write-Host "Stopping $($_.ProcessName) pid=$($_.Id)"
            try {
                Stop-Process -Id $_.Id -Force -ErrorAction Stop
            } catch {
                Write-Host "Warning: could not stop pid=$($_.Id): $_"
            }
        }
    }
    Start-Sleep -Seconds 1
}

function Remove-PathWithRetry($Path) {
    if (-not (Test-Path $Path)) {
        return
    }

    for ($i = 1; $i -le 5; $i++) {
        try {
            Remove-Item $Path -Recurse -Force -ErrorAction Stop
            return
        } catch {
            if ($i -eq 5) {
                throw
            }
            Write-Host "Warning: cannot remove $Path yet ($i/5): $_"
            Start-Sleep -Seconds 2
        }
    }
}

function Copy-PathWithRetry($Source, $Destination) {
    for ($i = 1; $i -le 5; $i++) {
        try {
            Copy-Item $Source $Destination -Recurse -Force -ErrorAction Stop
            return
        } catch {
            if ($i -eq 5) {
                throw
            }
            Write-Host "Warning: cannot copy $Source to $Destination yet ($i/5): $_"
            Start-Sleep -Seconds 2
        }
    }
}

Stop-RunningApp

Write-Host "== Cleaning old build output =="
Remove-PathWithRetry "dist\smtp_unlock"
Remove-PathWithRetry "dist\smtp_unlock_gui"
Remove-PathWithRetry "build\smtp_unlock"
Remove-PathWithRetry "build\smtp_unlock_gui"

Write-Host "== Build script: creating virtualenv and installing deps =="
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

Write-Host "== Install Playwright browser binaries =="
python -m playwright install chromium

Write-Host "== PyInstaller build (CLI entry point) =="
pyinstaller --noconfirm --clean --onedir --name smtp_unlock run.py

Write-Host "== PyInstaller build (GUI app entry point) =="
pyinstaller --noconfirm --clean --onedir --windowed --name smtp_unlock_gui run_gui.py

Write-Host "== Merging GUI into main bundle =="
$guiDist = "dist\smtp_unlock_gui"
$mainDist = "dist\smtp_unlock"
if (Test-Path $guiDist) {
    Copy-PathWithRetry "$guiDist\*" "$mainDist\"
    Remove-PathWithRetry $guiDist
}

Write-Host "== Build finished =="
Write-Host "Find output in the dist\smtp_unlock folder."
Write-Host "Tip: Test the program on a clean Windows machine:"
Write-Host "  .\dist\smtp_unlock\smtp_unlock_gui.exe"
Write-Host "  .\dist\smtp_unlock\smtp_unlock.exe"

# Copy Playwright browser binaries into the dist folder so runtime is bundled.
# Default Playwright browser location on Windows:
#   %USERPROFILE%\\AppData\\Local\\ms-playwright
try {
    $pwCache = Join-Path $env:USERPROFILE "AppData\\Local\\ms-playwright"
    $distDir = Join-Path "dist" "smtp_unlock"
    if ((Test-Path $pwCache) -and (Test-Path $distDir)) {
        Write-Host "== Copying Playwright browser runtime from $pwCache to $distDir\\ms-playwright =="
        robocopy $pwCache (Join-Path $distDir "ms-playwright") /E /NFL /NDL /NJH /NJS /nc /ns | Out-Null
        Write-Host "== Copy complete =="
    } else {
        Write-Host "== Playwright cache or dist folder not found. Skipping copy. =="
    }
} catch {
    Write-Host "Warning: failed to copy Playwright runtime: $_"
}
