# SMTP Unlock Tool - Windows Usage

## Install

1. Run `SMTP_Unlock_Setup.exe`.
2. If Windows shows a warning, choose `More info` -> `Run anyway` when available.
3. Install the app.
4. Open `SMTP Unlock Tool` from Desktop or Start Menu.

If Windows says an Application Control policy blocked the file, the machine is blocking unsigned apps by policy. Use another Windows machine or ask the system administrator to allow the app.

## Run

1. Paste accounts into the text box, one account per line:

```text
email@example.com|password
email@example.com|password|refresh_token|client_id
```

If your data already has `refresh_token|client_id`, keep those columns. The app accepts them but only uses `email` and `password`.

2. Choose the number of threads.
3. Click `Chạy`.

## Output

Results are saved under:

```text
%LOCALAPPDATA%\SMTP Unlock Tool\output
```

Main files:

```text
enabled.txt
failed.txt
run.log
```

Startup errors, if any, are saved to:

```text
%LOCALAPPDATA%\SMTP Unlock Tool\startup_error.log
%LOCALAPPDATA%\SMTP Unlock Tool\gui_console.log
```

## Build

Windows builds are produced by AppVeyor or GitHub Actions using:

```text
build_windows.ps1
inno_build.ps1
installer.iss
```
