BUILD_MAC.md

Goal: Build a macOS distributable so customers can run the tool with minimal setup.

Quick summary
- You must run these steps on a Mac.
- This repo includes `build_mac.sh` which builds a folder-based bundle using PyInstaller and copies Playwright browser runtime.
- For a user-friendly double-clickable app that bypasses Gatekeeper warnings, you should codesign and notarize the bundle.

Simple build (tested workflow)
1. On macOS, open Terminal and go to project folder:

```bash
cd /path/to/project
chmod +x build_mac.sh
./build_mac.sh
```

2. The script creates `.venv`, installs deps, runs `pyinstaller --onedir` and copies Playwright cache to `dist/smtp_unlock/ms-playwright`.
3. Test the app by running the executable in `dist/smtp_unlock`:

```bash
cd dist/smtp_unlock
./run
```

Codesign & Notarize (recommended if distributing to customers)
- You need an Apple Developer account and a Developer ID Application certificate.
- Codesign the app (example for an onedir bundle):

```bash
# Example signing for binaries in the onedir
codesign --deep --force --options runtime --sign "Developer ID Application: Your Name (TEAMID)" dist/smtp_unlock/run
```

- Create a ZIP and submit to Apple notarization service (example using `notarytool`):

```bash
# Create zip
cd dist
zip -r smtp_unlock.zip smtp_unlock

# Upload for notarization (you must have configured notarytool)
xcrun notarytool submit smtp_unlock.zip --team-id YOUR_TEAM_ID --apple-id you@apple.com --password @keychain:AC_PASSWORD --wait

# Staple the notarization ticket
xcrun stapler staple dist/smtp_unlock
```

Notes & pitfalls
- Playwright browser runtime is large; bundling will increase package size considerably.
- Notarization and codesigning require Apple Developer membership and working credentials.
- If you want a proper `.app` bundle (macOS application), consider using `py2app` or a packaging tool that creates a `.app` wrapper; the current script uses `pyinstaller --onedir` which gives an executable folder.

If you'd like, I can:
- provide a sample `codesign` and `notarytool` command template filled with placeholders for your team ID and Apple ID, or
- create a `build_mac_app.sh` that uses `py2app` to produce a `.app` bundle instead of a simple onedir bundle.
