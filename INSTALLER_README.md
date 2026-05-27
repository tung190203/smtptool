INSTALLER_README.md

Goal: Create a Windows installer (.exe) that bundles the built application (dist\smtp_unlock).

Steps (on Windows machine):

1. Build project using `build_windows.ps1` (PowerShell):

```powershell
cd C:\path\to\project
.\build_windows.ps1
```

2. After build finishes, you should have `dist\smtp_unlock` containing the program files and a copied `ms-playwright` folder with browser runtimes.

3. Install Inno Setup (https://jrsoftware.org/isinfo.php) on the Windows machine.

4. Open `installer.iss` in Inno Setup Compiler. Confirm `OutputDir` and `Source` paths are correct.

5. Build the installer via Inno Setup. The generated installer `.exe` will be in the `installer` folder.

Notes & tips:
- Test the installer on a clean Windows VM before distributing.
- The generated installer simply copies the `dist\smtp_unlock` folder into `Program Files` and creates shortcuts.
- If `run.exe` is missing, run the program in `dist\smtp_unlock` to verify it works before building the installer.

If you want, tôi có thể:
- thêm script tự động hóa Inno Setup build (requires Inno Setup command-line tools), hoặc
- cập nhật `run.py` để tạo 1 shortcut installer-friendly hoặc config mặc định cho khách hàng.
