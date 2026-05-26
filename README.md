# Standalone EPUB Converter and Fixer

A standalone PySide6 desktop tool for recompiling and converting EPUB projects using the existing EPUB conversion pipeline.

## What It Does

This app provides two modes:

- **Fix EPUB**: Select one or more existing `.epub` files. The app extracts each EPUB in a temporary workspace, runs it through the chapter extraction process, then saves only the fixed `.epub` file to a `Fixed` subfolder under the output root using the original filename and extension. This is useful for repairing EPUB structure, navigation, metadata, and compatibility issues.
- **Convert to EPUB**: Select one or more folders containing extracted chapter/content files. The app compiles each folder into an EPUB saved under the output root. Standard EPUB folder layouts such as `OEBPS`, `OEBS`, OPF metadata, EPUB 2, and EPUB 3 structures are supported.

Both modes support multiple inputs and parallel processing.

## Features

- Dark PySide6 GUI
- Drag-and-drop input support
- Parallel EPUB processing
- EPUB fix/recompile workflow
- Folder-to-EPUB conversion workflow
- EPUB 2 and EPUB 3 layout support
- OPF/metadata-aware standard EPUB folder handling
- Source chapter filenames and extensions are retained by default
- Default output root is the executable folder on Windows, the folder containing the `.app` bundle on macOS, or the script folder when running from source
- Fixed EPUB output keeps the original EPUB filename in a `Fixed` subfolder under the output root
- Auto-saved GUI settings in `config.json`
- DPI-aware startup via `dpi_setup.py`
- Optional image compression
- Optional CSS attachment/override support
- NCX compatibility mode
- LLM token cleanup for empty-attribute tags
- PyInstaller `.spec` for building a Windows executable

## Running From Source

Install dependencies:

```powershell
pip install -r requirements.txt
```

Launch the GUI:

```powershell
python EPUB_GUI.py
```

Or use:

```powershell
launch_gui.bat
```

## Building the EXE

Use the included build script:

```powershell
build_exe.bat
```

The executable will be generated as `dist/EPUB Fixer and Converter.exe`.

## Notes

Runtime settings are saved to `config.json` next to the script, or next to the executable in frozen builds. If the default output root is not writable on macOS, output falls back to `~/Documents/EPUB Fixer and Converter`. Generated builds, caches, local config, and temporary outputs are intentionally ignored by Git.
