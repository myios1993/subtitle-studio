# SubtitleStudio Build Issue - Fix Applied

## Problem
Double-clicking `dist/SubtitleStudio/SubtitleStudio.exe` fails to launch the application.

## Root Cause
PyInstaller bundles `python311.dll` in the `_internal/` subdirectory, but Windows requires it to be located **next to the `.exe` file** (in `dist/SubtitleStudio/`) for the bundled Python runtime to load correctly.

## Fix Applied
1. **Copy the missing DLL** (immediate fix):
   ```bash
   cp "dist/SubtitleStudio/_internal/python311.dll" "dist/SubtitleStudio/"
   ```

2. **Update `subtitle-studio.spec`** for future builds — add to `binaries`:
   ```python
   binaries = [
       (r'dist/SubtitleStudio/_internal/python311.dll', r'dist/SubtitleStudio'),
   ]
   ```

3. **Rebuild**:
   ```bash
   pyinstaller subtitle-studio.spec
   ```

## Verification
The EXE launches and runs successfully when executed directly.