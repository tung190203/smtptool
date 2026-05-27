BUILD_WINDOWS.md

Goal: Create a distributable for Windows so customers don't need to install Python or Playwright.

Important summary (simple):
- You must build the EXE on Windows or a Windows CI runner. Do not cross-compile from another OS.
- Use PyInstaller in onedir mode (one-folder) to reduce runtime problems with Playwright.
- If you do not have a Windows PC, use the included GitHub Actions workflow.

Build without a Windows PC:

Option A - GitHub Actions:
1. Push this project to GitHub.
2. Open the repo on GitHub.
3. Go to `Actions` -> `Build Windows Installer`.
4. Click `Run workflow`.
5. When the run finishes, download the `SMTP_Unlock_Setup` artifact.
6. Unzip the artifact and send `SMTP_Unlock_Setup.exe` to the customer.

Option B - AppVeyor, useful if GitHub Actions is blocked by billing:
1. Keep the GitHub repo public.
2. Sign in at appveyor.com with GitHub.
3. Add/import the `smtptool` GitHub repo.
4. AppVeyor will read `appveyor.yml` and build on Windows.
5. Download the `SMTP_Unlock_Setup` artifact from the AppVeyor build.
6. Send `SMTP_Unlock_Setup.exe` to the customer.

Quick steps (tested workflow):
1. On a Windows machine with Python 3.8+ installed, open PowerShell and go to the project folder.
2. If PowerShell blocks scripts, allow this session to run local scripts:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

3. To create the app folder only, run:

```powershell
.\build_windows.ps1
```

The script will:
- Create a `.venv` virtual environment and install `requirements.txt`.
- Run `python -m playwright install chromium` to download browser runtime.
- Run `pyinstaller --onedir` to produce `dist\smtp_unlock`.
- Copy Playwright browser runtime into `dist\smtp_unlock\ms-playwright`.

Notes & common pitfalls:
- Playwright requires browser binaries. The build step downloads them into the environment; the final `dist` folder must include these files. `onedir` is recommended because `onefile` (single EXE) often cannot bundle Playwright runtime cleanly.
- After building, test the `dist\smtp_unlock` folder on a clean Windows VM:

```powershell
.\dist\smtp_unlock\smtp_unlock_gui.exe
```

- CLI build is also available:

```powershell
.\dist\smtp_unlock\smtp_unlock.exe
```

Create a click-to-open installer:
1. Install Inno Setup 6 on the Windows build machine.
2. Run:

```powershell
.\inno_build.ps1
```

The installer output will be:

```text
installer\SMTP_Unlock_Setup.exe
```

After customers install it, they can open the app from the Desktop shortcut or Start Menu shortcut named `SMTP Unlock Tool`.
