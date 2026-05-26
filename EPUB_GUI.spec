# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_data_files, collect_submodules


datas = [
    ("epub_converter.py", "."),
    ("chapter_extraction_worker.py", "."),
    ("chapter_extraction_manager.py", "."),
    ("dpi_setup.py", "."),
    ("_empty_attr_fix.py", "."),
]

hiddenimports = [
    "epub_converter",
    "chapter_extraction_worker",
    "chapter_extraction_manager",
    "dpi_setup",
    "_empty_attr_fix",
    "ebooklib",
    "ebooklib.epub",
    "bs4",
    "lxml",
    "PIL",
]

for package in (
    "PySide6",
    "ebooklib",
    "bs4",
    "lxml",
    "PIL",
    "tinycss2",
    "cssselect2",
    "html5lib",
):
    try:
        hiddenimports += collect_submodules(package)
        datas += collect_data_files(package)
    except Exception:
        pass


a = Analysis(
    ["EPUB_GUI.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["cairosvg", "cairocffi"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="EPUB_GUI",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
