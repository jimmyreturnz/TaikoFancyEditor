# Portable Windows Release

Taiko Fancy Arranger v1.0.0 uses a PyInstaller one-folder Windows build.

## For players

1. Download `TaikoFancyArranger-Windows-x64.zip` from GitHub Releases.
2. Extract the complete ZIP.
3. Open the extracted `TaikoFancyArranger` folder.
4. Run `TaikoFancyArranger.exe`.

Python and PySide6 do not need to be installed on the player's PC.

Do not move or distribute `TaikoFancyArranger.exe` by itself. The adjacent DLLs, Qt plugins, Python runtime, and support files are part of the application.

## For maintainers

Run `build_windows.bat` on Windows. The portable folder is created at:

```text
dist\TaikoFancyArranger```

Test the complete folder on a clean Windows PC or Windows Sandbox before publishing the release ZIP.
