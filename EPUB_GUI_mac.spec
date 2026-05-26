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
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "PySide6.QtSvg",
]

for package in (
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
    excludes=["cairosvg", "cairocffi", "PySide6.scripts"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="EPUB Fixer and Converter",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="EPUB Fixer and Converter",
)

app = BUNDLE(
    coll,
    name="EPUB Fixer and Converter.app",
    icon=None,
    bundle_identifier="com.shirochi.epubconverter",
    info_plist={
        "CFBundleName": "EPUB Fixer and Converter",
        "CFBundleDisplayName": "EPUB Fixer and Converter",
        "CFBundleShortVersionString": "1.0.0",
        "CFBundleVersion": "1.0.0",
        "NSHighResolutionCapable": True,
    },
)
