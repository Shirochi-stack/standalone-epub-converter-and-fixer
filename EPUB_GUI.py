#!/usr/bin/env python3
"""
Standalone PySide6 GUI for the existing EPUB conversion pipeline.

Modes:
  1. Fix EPUB: extract an EPUB, then recompile it with epub_converter.py.
  2. Convert to EPUB: compile one or more existing extracted HTML folders.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
import traceback
import zipfile
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_FILE_NAME = "config.json"
GUI_CONFIG_KEY = "epub_gui"


DEFAULT_ENV_DEFAULTS = {
    "ATTACH_CSS_TO_CHAPTERS": "0",
    "DISABLE_AUTOMATIC_COVER_CREATION": "1",
    "DISABLE_EPUB_GALLERY": "1",
    "ENABLE_IMAGE_COMPRESSION": "0",
    "EPUB_CSS_OVERRIDE_PATH": "",
    "EPUB_LAYOUT_MODE": "auto",
    "EPUB_PATH": "",
    "EPUB_USE_HTML_METHOD": "0",
    "EXCLUDE_COVER_COMPRESSION": "1",
    "EXCLUDE_GIF_COMPRESSION": "1",
    "EXTRACTION_WORKERS": "4",
    "FIX_EMPTY_ATTR_TAGS_EPUB": "0",
    "FONTCONFIG_FILE": "",
    "FORCE_NCX_ONLY": "1",
    "IMAGE_COMPRESSION_QUALITY": "80",
    "NUMBER_SPACING_TOKEN_FIX": "0",
    "OUTPUT_DIRECTORY": "",
    "RASTERIZE_SVG_FALLBACK": "1",
    "REMOVE_DUPLICATE_H1_P": "0",
    "USE_P_TAG_TOC_FALLBACK": "0",
}

CHOICE_ENVS = {
    "EPUB_LAYOUT_MODE": ["auto", "epub2", "epub3"],
    "NUMBER_SPACING_TOKEN_FIX": [
        ("1", "Standard - separate words from numbers"),
        ("2", "Standard + all-caps tokens"),
    ],
}

PATH_ENVS = {
    "EPUB_CSS_OVERRIDE_PATH": "file",
    "EPUB_PATH": "file",
    "OUTPUT_DIRECTORY": "directory",
    "FONTCONFIG_FILE": "file",
}

SECRET_ENVS = set()
HIDDEN_ENVS = {"DEBUG_MODE"}
ALWAYS_SET_TEXT_ENVS = {"EPUB_LAYOUT_MODE", "EXTRACTION_WORKERS"}
FORCE_TEXT_ENVS = {
    "EPUB_CSS_OVERRIDE_PATH",
    "EPUB_LAYOUT_MODE",
    "EPUB_PATH",
    "EXTRACTION_WORKERS",
    "FONTCONFIG_FILE",
    "IMAGE_COMPRESSION_QUALITY",
    "NUMBER_SPACING_TOKEN_FIX",
    "OUTPUT_DIRECTORY",
}

ENV_DISPLAY_ORDER = [
    "EPUB_LAYOUT_MODE",
    "FORCE_NCX_ONLY",
    "FIX_EMPTY_ATTR_TAGS_EPUB",
    "OUTPUT_DIRECTORY",
    "ATTACH_CSS_TO_CHAPTERS",
    "EPUB_CSS_OVERRIDE_PATH",
    "FONTCONFIG_FILE",
    "DISABLE_AUTOMATIC_COVER_CREATION",
    "DISABLE_EPUB_GALLERY",
    "ENABLE_IMAGE_COMPRESSION",
    "IMAGE_COMPRESSION_QUALITY",
]

ENV_DISPLAY_LABELS = {
    "FORCE_NCX_ONLY": "Use NCX Only (Compatibility)",
    "FIX_EMPTY_ATTR_TAGS_EPUB": "Fix Empty Attribute Tags (EPUB) - LLM Token Fix",
}

ENV_TOOLTIPS = {
    "EPUB_LAYOUT_MODE": "Choose EPUB folder/output style. Auto detects EPUB2 OEBPS/Text vs EPUB3 flat OEBPS from the source when possible.",
    "FORCE_NCX_ONLY": "Write NCX navigation only for compatibility with older readers. Recommended for broad EPUB reader support.",
    "OUTPUT_DIRECTORY": "Optional root folder for generated output. Leave disabled to write next to each input.",
    "ATTACH_CSS_TO_CHAPTERS": "Inject stylesheet links into chapter XHTML files. Enable if your reader ignores manifest-only CSS.",
    "EPUB_CSS_OVERRIDE_PATH": "Optional CSS file to use instead of the built-in/default extracted styles.",
    "FONTCONFIG_FILE": "Optional Fontconfig config file used by CairoSVG when rasterizing SVG assets with custom fonts.",
    "DISABLE_AUTOMATIC_COVER_CREATION": "Skip generated cover pages. Existing cover pages and cover metadata are still preserved when present.",
    "DISABLE_EPUB_GALLERY": "Skip the extra image gallery page. Images referenced by chapters are still embedded.",
    "ENABLE_IMAGE_COMPRESSION": "Compress embedded images before writing the EPUB.",
    "IMAGE_COMPRESSION_QUALITY": "JPEG/WebP compression quality when image compression is enabled. Higher means larger but clearer.",
    "EPUB_PATH": "Source EPUB path used for source navigation/metadata hints. Fix EPUB mode sets this automatically.",
    "EPUB_USE_HTML_METHOD": "Serialize chapter content with HTML-style output instead of strict XML. Use only for reader whitespace quirks.",
    "EXCLUDE_COVER_COMPRESSION": "When image compression is enabled, keep the cover image at original quality.",
    "EXCLUDE_GIF_COMPRESSION": "When image compression is enabled, leave GIF files untouched.",
    "EXTRACTION_WORKERS": "Number of parallel workers for chapter/image processing. Lower this if your system gets unstable.",
    "FIX_EMPTY_ATTR_TAGS_EPUB": "Escapes LLM-tokenized tags like <tag attr=\"\"></tag> so they render as visible text instead of being dropped by EPUB readers.",
    "NUMBER_SPACING_TOKEN_FIX": "Enable to fix run-together letter/number text. Standard handles normal words; all-caps mode also handles acronym-like tokens.",
    "RASTERIZE_SVG_FALLBACK": "Create PNG fallbacks for SVG images for readers with weak SVG support.",
    "REMOVE_DUPLICATE_H1_P": "Remove a paragraph that exactly duplicates the preceding chapter heading.",
    "USE_P_TAG_TOC_FALLBACK": "Allow paragraph text to be used as a fallback chapter title when heading tags are missing.",
}


def runtime_base_dir() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "executable"):
        return Path(sys.executable).resolve().parent
    return SCRIPT_DIR


def config_path() -> Path:
    return runtime_base_dir() / CONFIG_FILE_NAME


def read_config_json() -> dict:
    path = config_path()
    if not path.is_file():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def write_config_json(data: dict) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.tmp")
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(temp_path, path)


def configure_stdio() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        try:
            if stream and hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def clean_default(value: str | None) -> str:
    if value is None:
        return ""
    value = str(value).strip()
    while value and value[-1] in ")]}":
        value = value[:-1].rstrip()
    if len(value) >= 2 and value[0] in "\"'" and value[-1] == value[0]:
        return value[1:-1]
    return value


def discover_env_defaults() -> dict[str, str]:
    defaults = dict(DEFAULT_ENV_DEFAULTS)
    patterns = [
        re.compile(r"os\.environ\.get\(\s*['\"]([A-Z0-9_]+)['\"]\s*(?:,\s*([^)]+))?\)"),
        re.compile(r"os\.getenv\(\s*['\"]([A-Z0-9_]+)['\"]\s*(?:,\s*([^)]+))?\)"),
        re.compile(r"os\.environ\[\s*['\"]([A-Z0-9_]+)['\"]\s*\]"),
    ]
    for script_name in ("epub_converter.py", "chapter_extraction_worker.py", "chapter_extraction_manager.py"):
        script_path = SCRIPT_DIR / script_name
        if not script_path.exists():
            continue
        text = script_path.read_text(encoding="utf-8", errors="ignore")
        for pattern in patterns:
            for match in pattern.finditer(text):
                name = match.group(1)
                if name in HIDDEN_ENVS:
                    continue
                default = clean_default(match.group(2) if len(match.groups()) > 1 else "")
                defaults.setdefault(name, default)
                if default and not defaults[name]:
                    defaults[name] = default
    visible_names = set(defaults) - HIDDEN_ENVS
    ordered_names = [name for name in ENV_DISPLAY_ORDER if name in visible_names]
    ordered_names.extend(name for name in sorted(visible_names) if name not in ordered_names)
    return {name: defaults[name] for name in ordered_names}


def safe_leaf(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", name).strip(" ._")
    return cleaned or "book"


def unique_output_dir(path: Path) -> Path:
    if not path.exists() or not any(path.iterdir()):
        return path
    stamp = time.strftime("%Y%m%d_%H%M%S")
    candidate = path.with_name(f"{path.name}_{stamp}")
    counter = 2
    while candidate.exists():
        candidate = path.with_name(f"{path.name}_{stamp}_{counter}")
        counter += 1
    return candidate


def fix_output_dir(epub_path: Path, env: dict[str, str]) -> Path:
    root = env.get("OUTPUT_DIRECTORY", "").strip()
    leaf = f"{safe_leaf(epub_path.stem)}_recompiled"
    if root:
        return unique_output_dir(Path(root).expanduser().resolve() / leaf)
    return unique_output_dir(epub_path.parent / leaf)


def newest_epub(directory: Path) -> str:
    epubs = sorted(directory.glob("*.epub"), key=lambda p: p.stat().st_mtime, reverse=True)
    return str(epubs[0]) if epubs else ""


def find_standard_epub_opf(folder: Path) -> Path | None:
    container = folder / "META-INF" / "container.xml"
    if container.is_file():
        try:
            from xml.etree import ElementTree as ET
            root = ET.fromstring(container.read_bytes())
            for elem in root.iter():
                if elem.tag.rsplit("}", 1)[-1] == "rootfile":
                    full_path = elem.attrib.get("full-path")
                    if full_path:
                        candidate = folder / Path(full_path.replace("/", os.sep))
                        if candidate.is_file():
                            return candidate
        except Exception:
            pass

    preferred_roots = ["OEBPS", "OEBS", "OPS", "EPUB"]
    for root_name in preferred_roots:
        root = folder / root_name
        if root.is_dir():
            for opf in root.rglob("*.opf"):
                return opf

    opfs = list(folder.glob("*.opf"))
    return opfs[0] if opfs else None


def is_standard_epub_folder(folder: Path) -> bool:
    return folder.is_dir() and find_standard_epub_opf(folder) is not None


def normalized_folder_output_dir(folder: Path, env: dict[str, str]) -> Path:
    root = env.get("OUTPUT_DIRECTORY", "").strip()
    leaf = f"{safe_leaf(folder.name)}_normalized"
    if root:
        return unique_output_dir(Path(root).expanduser().resolve() / leaf)
    return unique_output_dir(folder.parent / leaf)


def zip_epub_folder(folder: Path) -> str:
    handle = tempfile.NamedTemporaryFile(prefix="epub_folder_", suffix=".epub", delete=False)
    temp_path = handle.name
    handle.close()

    mimetype = folder / "mimetype"
    with zipfile.ZipFile(temp_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        if mimetype.is_file():
            zf.write(mimetype, "mimetype", compress_type=zipfile.ZIP_STORED)
        for file_path in folder.rglob("*"):
            if not file_path.is_file():
                continue
            rel = file_path.relative_to(folder).as_posix()
            if rel == "mimetype":
                continue
            zf.write(file_path, rel)
    return temp_path


def log_line(message: str) -> None:
    print(str(message), flush=True)


def apply_job_environment(env: dict[str, str], mode: str, input_path: str) -> None:
    env = {
        str(key): str(value)
        for key, value in env.items()
    }
    if mode == "convert" and "EPUB_PATH" not in env:
        os.environ.pop("EPUB_PATH", None)
    for key, value in env.items():
        os.environ[str(key)] = str(value)
    if mode == "fix":
        os.environ["EPUB_PATH"] = input_path
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")


def run_compile(base_dir: Path) -> str:
    from epub_converter import EPUBCompiler, set_stop_flag

    set_stop_flag(False)
    compiler = EPUBCompiler(str(base_dir), log_callback=log_line)
    compiler.compile()
    output_path = compiler.last_epub_output_path or newest_epub(base_dir)
    if not output_path or not Path(output_path).exists():
        raise RuntimeError("Compilation finished without creating an EPUB file")
    return output_path


def run_pipeline_job(config_path: str) -> int:
    configure_stdio()
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)

        mode = config["mode"]
        input_path = str(Path(config["input_path"]).expanduser().resolve())
        env = {str(k): str(v) for k, v in (config.get("env") or {}).items()}
        env = {
            key: value
            for key, value in env.items()
        }
        apply_job_environment(env, mode, input_path)

        if mode == "fix":
            epub_path = Path(input_path)
            if not epub_path.is_file() or epub_path.suffix.lower() != ".epub":
                raise ValueError(f"Fix EPUB mode expects an .epub file: {input_path}")

            output_dir = fix_output_dir(epub_path, env)
            log_line(f"[JOB] Fix EPUB: {epub_path}")
            log_line(f"[JOB] Extracting to: {output_dir}")
            from chapter_extraction_worker import run_chapter_extraction

            result = run_chapter_extraction(str(epub_path), str(output_dir))
            if not result or not result.get("success"):
                raise RuntimeError((result or {}).get("error", "Chapter extraction failed"))

            log_line(f"[JOB] Recompiling extracted folder: {output_dir}")
            output_path = run_compile(output_dir)
        elif mode == "convert":
            folder = Path(input_path)
            if not folder.is_dir():
                raise ValueError(f"Convert to EPUB mode expects a folder: {input_path}")
            if is_standard_epub_folder(folder):
                from chapter_extraction_worker import run_chapter_extraction

                temp_epub = ""
                try:
                    output_dir = normalized_folder_output_dir(folder, env)
                    temp_epub = zip_epub_folder(folder)
                    os.environ["EPUB_PATH"] = temp_epub
                    log_line(f"[JOB] Convert standard EPUB folder: {folder}")
                    log_line(f"[JOB] Normalizing OEBPS/OPF structure to: {output_dir}")
                    result = run_chapter_extraction(temp_epub, str(output_dir))
                    if not result or not result.get("success"):
                        raise RuntimeError((result or {}).get("error", "EPUB folder normalization failed"))
                    try:
                        with open(output_dir / "source_epub.txt", "w", encoding="utf-8") as f:
                            f.write(str(folder))
                    except OSError:
                        pass
                    log_line(f"[JOB] Compiling normalized folder: {output_dir}")
                    output_path = run_compile(output_dir)
                finally:
                    if temp_epub:
                        try:
                            os.remove(temp_epub)
                        except OSError:
                            pass
            else:
                log_line(f"[JOB] Convert folder: {folder}")
                output_path = run_compile(folder)
        else:
            raise ValueError(f"Unknown mode: {mode}")

        log_line("[JOB_RESULT] " + json.dumps({"success": True, "output": output_path}, ensure_ascii=False))
        return 0
    except Exception as exc:
        log_line(f"[JOB_ERROR] {exc}")
        log_line(traceback.format_exc())
        log_line("[JOB_RESULT] " + json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False))
        return 1


def run_chapter_extraction_flag(args: list[str]) -> int:
    configure_stdio()
    if len(args) < 2:
        print("Usage: --run-chapter-extraction <epub_path> <output_dir>", flush=True)
        return 1
    from chapter_extraction_worker import run_chapter_extraction

    result = run_chapter_extraction(args[0], args[1])
    return 0 if result.get("success") else 1


def run_compress_worker_flag() -> int:
    configure_stdio()
    from epub_converter import run_compress_worker_loop

    return run_compress_worker_loop()


def worker_flag_main() -> int | None:
    if len(sys.argv) <= 1:
        return None
    flag = sys.argv[1]
    if flag == "--run-job":
        if len(sys.argv) < 3:
            print("Usage: --run-job <config.json>", flush=True)
            return 1
        return run_pipeline_job(sys.argv[2])
    if flag == "--run-chapter-extraction":
        return run_chapter_extraction_flag(sys.argv[2:])
    if flag == "--run-compress-worker":
        return run_compress_worker_flag()
    return None


_worker_exit = worker_flag_main()
if _worker_exit is not None:
    raise SystemExit(_worker_exit)


try:
    import dpi_setup
except Exception as exc:
    dpi_setup = None
    print(f"DPI setup unavailable: {exc}", flush=True)
else:
    dpi_setup.configure()


from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, QTimer, Signal, Slot
from PySide6.QtGui import QColor, QDragEnterEvent, QDropEvent, QPalette, QTextCursor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


class DropListWidget(QListWidget):
    paths_dropped = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setAlternatingRowColors(True)
        self.setMinimumHeight(180)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        paths = []
        for url in event.mimeData().urls():
            if url.isLocalFile():
                paths.append(url.toLocalFile())
        if paths:
            self.paths_dropped.emit(paths)
            event.acceptProposedAction()
        else:
            super().dropEvent(event)


class EnvVarRow(QWidget):
    changed = Signal()

    def __init__(self, name: str, default: str, parent=None):
        super().__init__(parent)
        self.name = name
        self.label = ENV_DISPLAY_LABELS.get(name, name)
        self.tooltip = ENV_TOOLTIPS.get(name, f"Set {name} for the converter process.")
        self.default = default
        self.is_bool = self._is_boolean(name, default)
        self.enable_box: QCheckBox | None = None
        self.value_widget = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 3, 8, 3)
        layout.setSpacing(14)

        if self.is_bool:
            box = QCheckBox(self.label)
            box.setChecked(default == "1")
            box.setToolTip(self.tooltip)
            box.toggled.connect(lambda *_: self.changed.emit())
            layout.addWidget(box, 1)
            self.value_widget = box
        else:
            self.enable_box = QCheckBox(self.label)
            self.enable_box.setChecked(name in ALWAYS_SET_TEXT_ENVS)
            self.enable_box.setToolTip(self.tooltip)
            self.enable_box.toggled.connect(lambda *_: self.changed.emit())
            layout.addWidget(self.enable_box, 0)

            if name in CHOICE_ENVS:
                combo = QComboBox()
                choices = CHOICE_ENVS[name]
                uses_labels = bool(choices and isinstance(choices[0], tuple))
                combo.setEditable(False)
                if uses_labels:
                    selected_index = -1
                    for index, (value, label) in enumerate(choices):
                        combo.addItem(label, value)
                        if str(value) == str(default):
                            selected_index = index
                    if selected_index >= 0:
                        combo.setCurrentIndex(selected_index)
                else:
                    combo.addItems(choices)
                    combo.setCurrentText(default)
                self.value_widget = combo
            else:
                line = QLineEdit()
                line.setText(default)
                line.setPlaceholderText(default or name)
                if name in SECRET_ENVS:
                    line.setEchoMode(QLineEdit.Password)
                self.value_widget = line

            self.value_widget.setToolTip(self.tooltip)
            if isinstance(self.value_widget, QComboBox):
                self.value_widget.currentIndexChanged.connect(lambda *_: self.changed.emit())
                self.value_widget.currentTextChanged.connect(lambda *_: self.changed.emit())
            elif isinstance(self.value_widget, QLineEdit):
                self.value_widget.textChanged.connect(lambda *_: self.changed.emit())
            self.value_widget.setEnabled(self.enable_box.isChecked())
            self.enable_box.toggled.connect(self.value_widget.setEnabled)
            layout.addWidget(self.value_widget, 1)

            if name in PATH_ENVS:
                browse = QPushButton("Browse")
                browse.setToolTip(self.tooltip)
                browse.clicked.connect(self._browse_path)
                browse.setEnabled(self.enable_box.isChecked())
                self.enable_box.toggled.connect(browse.setEnabled)
                layout.addWidget(browse, 0)

    def _is_boolean(self, name: str, default: str) -> bool:
        return name not in FORCE_TEXT_ENVS and default in {"0", "1"}

    def _browse_path(self) -> None:
        kind = PATH_ENVS.get(self.name)
        current = self.value()
        if kind == "directory":
            path = QFileDialog.getExistingDirectory(self, f"Select {self.name}", current or str(SCRIPT_DIR))
        else:
            path, _ = QFileDialog.getOpenFileName(self, f"Select {self.name}", current or str(SCRIPT_DIR))
        if path:
            self.set_value(path)
            if self.enable_box:
                self.enable_box.setChecked(True)

    def value(self) -> str:
        if isinstance(self.value_widget, QCheckBox):
            return "1" if self.value_widget.isChecked() else "0"
        if isinstance(self.value_widget, QComboBox):
            data = self.value_widget.currentData()
            if data is not None:
                return str(data).strip()
            return self.value_widget.currentText().strip()
        if isinstance(self.value_widget, QLineEdit):
            return self.value_widget.text().strip()
        return ""

    def set_value(self, value: str) -> None:
        if isinstance(self.value_widget, QCheckBox):
            self.value_widget.setChecked(str(value) == "1")
        elif isinstance(self.value_widget, QComboBox):
            for index in range(self.value_widget.count()):
                if str(self.value_widget.itemData(index)) == str(value):
                    self.value_widget.setCurrentIndex(index)
                    return
            self.value_widget.setCurrentText(value)
        elif isinstance(self.value_widget, QLineEdit):
            self.value_widget.setText(value)

    def enabled_for_collection(self) -> bool:
        if self.is_bool:
            return True
        return bool(self.enable_box and self.enable_box.isChecked())

    def config_state(self) -> dict[str, object]:
        return {
            "enabled": self.enabled_for_collection(),
            "value": self.value(),
        }

    def apply_config_state(self, state: object) -> None:
        if not isinstance(state, dict):
            return
        if "value" in state:
            self.set_value(str(state.get("value") or ""))
        if self.enable_box is not None and "enabled" in state:
            self.enable_box.setChecked(bool(state.get("enabled")))


class EnvPanel(QWidget):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows: dict[str, EnvVarRow] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        hint = QLabel("Environment variables")
        hint.setObjectName("PanelTitle")
        outer.addWidget(hint)

        grid_host = QWidget()
        grid = QVBoxLayout(grid_host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(2)

        for name, default in discover_env_defaults().items():
            row = EnvVarRow(name, default)
            self.rows[name] = row
            row.changed.connect(self.changed.emit)
            grid.addWidget(row)

        grid.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(grid_host)
        outer.addWidget(scroll, 1)

    def values(self) -> dict[str, str]:
        env = {}
        for name, row in self.rows.items():
            if row.enabled_for_collection():
                env[name] = row.value()
        return env

    def set_output_directory(self, path: str) -> None:
        row = self.rows.get("OUTPUT_DIRECTORY")
        if row:
            row.set_value(path)
            if row.enable_box:
                row.enable_box.setChecked(bool(path))

    def config_state(self) -> dict[str, dict[str, object]]:
        return {name: row.config_state() for name, row in self.rows.items()}

    def apply_config_state(self, state: object) -> None:
        if not isinstance(state, dict):
            return
        for name, row_state in state.items():
            row = self.rows.get(name)
            if row is not None:
                row.apply_config_state(row_state)


class JobSignals(QObject):
    started = Signal(str, object)
    log = Signal(str)
    finished = Signal(str, bool, dict)


class ProcessJob(QRunnable):
    def __init__(self, job_id: str, config: dict):
        super().__init__()
        self.job_id = job_id
        self.config = config
        self.signals = JobSignals()

    @Slot()
    def run(self) -> None:
        config_path = ""
        process = None
        result = {}
        success = False
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as f:
                json.dump(self.config, f, ensure_ascii=False, indent=2)
                config_path = f.name

            if getattr(sys, "frozen", False):
                cmd = [sys.executable, "--run-job", config_path]
            else:
                cmd = [sys.executable, str(Path(__file__).resolve()), "--run-job", config_path]

            env = os.environ.copy()
            process_env = {
                str(k): str(v)
                for k, v in (self.config.get("env") or {}).items()
            }
            env.update(process_env)
            env["PYTHONIOENCODING"] = "utf-8"
            env["PYTHONLEGACYWINDOWSSTDIO"] = "0"

            creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                creationflags=creationflags,
                bufsize=1,
            )
            self.signals.started.emit(self.job_id, process)

            assert process.stdout is not None
            for line in process.stdout:
                text = line.rstrip()
                if text.startswith("[JOB_RESULT]"):
                    try:
                        result = json.loads(text[len("[JOB_RESULT]"):].strip())
                    except json.JSONDecodeError:
                        result = {"success": False, "error": "Could not parse job result"}
                else:
                    self.signals.log.emit(f"{self.config['label']}: {text}")

            return_code = process.wait()
            success = return_code == 0 and result.get("success", False)
            if not result:
                result = {"success": success, "error": f"Process exited with code {return_code}"}
        except Exception as exc:
            result = {"success": False, "error": str(exc)}
            self.signals.log.emit(f"{self.config.get('label', self.job_id)}: {traceback.format_exc()}")
        finally:
            if config_path:
                try:
                    os.remove(config_path)
                except OSError:
                    pass
            self.signals.finished.emit(self.job_id, success, result)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Standalone EPUB Converter")
        self.resize(1420, 800)
        self.setMinimumSize(1180, 720)
        self.thread_pool = QThreadPool.globalInstance()
        self.active_processes: dict[str, subprocess.Popen] = {}
        self.total_jobs = 0
        self.done_jobs = 0
        self.loading_config = True
        self.config_save_timer = QTimer(self)
        self.config_save_timer.setSingleShot(True)
        self.config_save_timer.timeout.connect(self.save_config)

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel("EPUB standalone pipeline")
        title.setObjectName("Title")
        header.addWidget(title, 1)
        self.status_label = QLabel("Ready")
        self.status_label.setObjectName("StatusLabel")
        header.addWidget(self.status_label, 0)
        root.addLayout(header)

        mode_box = QGroupBox("Mode")
        mode_layout = QHBoxLayout(mode_box)
        mode_layout.setSpacing(22)
        self.fix_radio = QRadioButton("Fix EPUB")
        self.convert_radio = QRadioButton("Convert to EPUB")
        self.fix_radio.setChecked(True)
        self.mode_group = QButtonGroup(self)
        self.mode_group.addButton(self.fix_radio)
        self.mode_group.addButton(self.convert_radio)
        mode_layout.addWidget(self.fix_radio)
        mode_layout.addWidget(self.convert_radio)
        mode_layout.addStretch(1)
        root.addWidget(mode_box)

        splitter = QSplitter(Qt.Horizontal)
        root.addWidget(splitter, 1)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 8, 0)
        left_layout.setSpacing(10)

        input_box = QGroupBox("Inputs")
        input_layout = QVBoxLayout(input_box)
        drop_hint = QLabel("Drag EPUB files here for Fix EPUB, or folders/OEBPS unpacked EPUBs for Convert to EPUB.")
        drop_hint.setObjectName("Hint")
        input_layout.addWidget(drop_hint)
        self.input_list = DropListWidget()
        self.input_list.paths_dropped.connect(self.add_paths)
        input_layout.addWidget(self.input_list, 1)

        input_buttons = QHBoxLayout()
        self.add_button = QPushButton("Add EPUBs")
        self.add_button.clicked.connect(self.add_inputs)
        remove_button = QPushButton("Remove")
        remove_button.clicked.connect(self.remove_selected)
        clear_button = QPushButton("Clear")
        clear_button.clicked.connect(self.input_list.clear)
        input_buttons.addWidget(self.add_button)
        input_buttons.addWidget(remove_button)
        input_buttons.addWidget(clear_button)
        input_buttons.addStretch(1)
        input_layout.addLayout(input_buttons)
        left_layout.addWidget(input_box, 3)

        run_box = QGroupBox("Run")
        run_layout = QGridLayout(run_box)
        run_layout.addWidget(QLabel("Parallel jobs"), 0, 0)
        self.parallel_spin = QSpinBox()
        self.parallel_spin.setRange(1, max(1, (os.cpu_count() or 4)))
        self.parallel_spin.setValue(min(2, max(1, os.cpu_count() or 2)))
        run_layout.addWidget(self.parallel_spin, 0, 1)
        output_button = QPushButton("Output Root")
        output_button.clicked.connect(self.choose_output_root)
        run_layout.addWidget(output_button, 0, 2)

        self.start_button = QPushButton("Start")
        self.start_button.setObjectName("PrimaryButton")
        self.start_button.clicked.connect(self.start_jobs)
        self.stop_button = QPushButton("Stop")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop_jobs)
        run_layout.addWidget(self.start_button, 1, 0, 1, 2)
        run_layout.addWidget(self.stop_button, 1, 2)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        run_layout.addWidget(self.progress, 2, 0, 1, 3)
        left_layout.addWidget(run_box)

        log_box = QGroupBox("Log")
        log_layout = QVBoxLayout(log_box)
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setLineWrapMode(QTextEdit.NoWrap)
        log_layout.addWidget(self.log_view)
        left_layout.addWidget(log_box, 4)

        self.env_panel = EnvPanel()
        env_frame = QFrame()
        env_frame.setFrameShape(QFrame.StyledPanel)
        env_frame.setMinimumWidth(560)
        env_layout = QVBoxLayout(env_frame)
        env_layout.addWidget(self.env_panel)

        splitter.addWidget(left)
        splitter.addWidget(env_frame)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([760, 620])

        self.fix_radio.toggled.connect(self.update_mode_ui)
        self.fix_radio.toggled.connect(lambda *_: self.schedule_config_save())
        self.convert_radio.toggled.connect(lambda *_: self.schedule_config_save())
        self.parallel_spin.valueChanged.connect(lambda *_: self.schedule_config_save())
        self.env_panel.changed.connect(self.schedule_config_save)
        self.load_config()
        self.loading_config = False
        self.update_mode_ui()

    def mode(self) -> str:
        return "fix" if self.fix_radio.isChecked() else "convert"

    def gui_config_state(self) -> dict[str, object]:
        return {
            "mode": self.mode(),
            "parallel_jobs": self.parallel_spin.value(),
            "env": self.env_panel.config_state(),
        }

    def load_config(self) -> None:
        gui_config = read_config_json().get(GUI_CONFIG_KEY, {})
        if not isinstance(gui_config, dict):
            return

        mode = str(gui_config.get("mode", "")).lower()
        if mode == "convert":
            self.convert_radio.setChecked(True)
        elif mode == "fix":
            self.fix_radio.setChecked(True)

        try:
            parallel_jobs = int(gui_config.get("parallel_jobs", self.parallel_spin.value()))
            self.parallel_spin.setValue(max(self.parallel_spin.minimum(), min(self.parallel_spin.maximum(), parallel_jobs)))
        except (TypeError, ValueError):
            pass

        self.env_panel.apply_config_state(gui_config.get("env", {}))

    def schedule_config_save(self) -> None:
        if self.loading_config:
            return
        self.config_save_timer.start(250)

    def save_config(self) -> None:
        if self.loading_config:
            return
        config = read_config_json()
        config[GUI_CONFIG_KEY] = self.gui_config_state()
        try:
            write_config_json(config)
        except Exception as exc:
            self.append_log(f"Could not save {CONFIG_FILE_NAME}: {exc}")

    def closeEvent(self, event) -> None:
        if self.config_save_timer.isActive():
            self.config_save_timer.stop()
            self.save_config()
        super().closeEvent(event)

    def update_mode_ui(self) -> None:
        if self.mode() == "fix":
            self.add_button.setText("Add EPUBs")
            self.status_label.setText("Fix mode: EPUB -> extract -> recompile")
        else:
            self.add_button.setText("Add Folder")
            self.status_label.setText("Convert mode: folder -> EPUB")

    def append_log(self, message: str) -> None:
        self.log_view.append(message)
        self.log_view.moveCursor(QTextCursor.End)

    def add_inputs(self) -> None:
        if self.mode() == "fix":
            paths, _ = QFileDialog.getOpenFileNames(
                self,
                "Select EPUB files",
                str(SCRIPT_DIR),
                "EPUB files (*.epub);;All files (*.*)",
            )
            self.add_paths(paths)
        else:
            path = QFileDialog.getExistingDirectory(self, "Select input folder", str(SCRIPT_DIR))
            if path:
                self.add_paths([path])

    def add_paths(self, paths: list[str]) -> None:
        normalized = []
        if self.mode() == "fix":
            for raw in paths:
                path = Path(raw)
                if path.is_dir():
                    normalized.extend(str(p.resolve()) for p in path.rglob("*.epub"))
                elif path.is_file() and path.suffix.lower() == ".epub":
                    normalized.append(str(path.resolve()))
        else:
            normalized = [str(Path(p).resolve()) for p in paths if Path(p).is_dir()]

        existing = {self.input_list.item(i).data(Qt.UserRole) for i in range(self.input_list.count())}
        added = 0
        for path in normalized:
            if path in existing:
                continue
            item = QListWidgetItem(path)
            item.setData(Qt.UserRole, path)
            self.input_list.addItem(item)
            existing.add(path)
            added += 1

        if added:
            self.append_log(f"Added {added} input(s).")
        elif paths:
            expected = "EPUB files" if self.mode() == "fix" else "folders"
            self.append_log(f"No new {expected} found in dropped/selected items.")

    def remove_selected(self) -> None:
        for item in self.input_list.selectedItems():
            self.input_list.takeItem(self.input_list.row(item))

    def choose_output_root(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select output root", str(SCRIPT_DIR))
        if path:
            self.env_panel.set_output_directory(path)

    def collect_inputs(self) -> list[str]:
        return [self.input_list.item(i).data(Qt.UserRole) for i in range(self.input_list.count())]

    def start_jobs(self) -> None:
        inputs = self.collect_inputs()
        if not inputs:
            QMessageBox.information(self, "No inputs", "Add at least one EPUB or folder first.")
            return

        env = {
            key: value
            for key, value in self.env_panel.values().items()
        }
        self.thread_pool.setMaxThreadCount(self.parallel_spin.value())
        self.total_jobs = len(inputs)
        self.done_jobs = 0
        self.progress.setRange(0, self.total_jobs)
        self.progress.setValue(0)
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.status_label.setText(f"Running 0/{self.total_jobs}")
        self.append_log(f"Starting {self.total_jobs} job(s) with up to {self.parallel_spin.value()} parallel process(es).")

        for index, path in enumerate(inputs, 1):
            label = Path(path).name
            config = {
                "mode": self.mode(),
                "input_path": path,
                "env": env,
                "label": label,
            }
            job_id = f"job-{index}-{time.time_ns()}"
            job = ProcessJob(job_id, config)
            job.signals.started.connect(self.job_started)
            job.signals.log.connect(self.append_log)
            job.signals.finished.connect(self.job_finished)
            self.thread_pool.start(job)

    @Slot(str, object)
    def job_started(self, job_id: str, process: object) -> None:
        self.active_processes[job_id] = process

    @Slot(str, bool, dict)
    def job_finished(self, job_id: str, success: bool, result: dict) -> None:
        self.active_processes.pop(job_id, None)
        self.done_jobs += 1
        self.progress.setValue(self.done_jobs)
        if success:
            self.append_log(f"Done: {result.get('output', 'EPUB created')}")
        else:
            self.append_log(f"Failed: {result.get('error', 'Unknown error')}")
        self.status_label.setText(f"Running {self.done_jobs}/{self.total_jobs}")

        if self.done_jobs >= self.total_jobs:
            self.start_button.setEnabled(True)
            self.stop_button.setEnabled(False)
            self.status_label.setText("Finished")
            self.append_log("All jobs finished.")

    def stop_jobs(self) -> None:
        for process in list(self.active_processes.values()):
            try:
                process.terminate()
            except Exception:
                pass
        self.append_log("Stop requested for active worker process(es).")
        self.stop_button.setEnabled(False)


def checkbox_tick_asset() -> str:
    asset_path = Path(tempfile.gettempdir()) / "epub_gui_checkbox_tick.svg"
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 14 14">'
        '<path d="M3 7.2 5.8 10 11.2 4" fill="none" stroke="#f4f4f4" '
        'stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round"/>'
        '</svg>'
    )
    try:
        if not asset_path.exists() or asset_path.read_text(encoding="utf-8", errors="ignore") != svg:
            asset_path.write_text(svg, encoding="utf-8")
    except OSError:
        pass
    return asset_path.as_posix()


def dark_stylesheet() -> str:
    tick = checkbox_tick_asset()
    return """
    QWidget {
        background: #07080a;
        color: #ececec;
        font-family: Segoe UI, Arial, sans-serif;
        font-size: 10pt;
    }
    QLabel#Title {
        font-size: 18pt;
        font-weight: 700;
    }
    QLabel#Hint, QLabel#StatusLabel {
        color: #9a9a9a;
    }
    QLabel#PanelTitle {
        font-size: 12pt;
        font-weight: 700;
        padding: 4px 0;
    }
    QGroupBox {
        border: 1px solid #24262b;
        border-radius: 6px;
        margin-top: 10px;
        padding: 10px;
        background: #0b0d10;
    }
    QGroupBox::title {
        subcontrol-origin: margin;
        left: 10px;
        padding: 0 4px;
        color: #d7d7d7;
        background: transparent;
    }
    QListWidget, QTextEdit, QLineEdit, QComboBox, QSpinBox {
        background: #050608;
        border: 1px solid #22252b;
        border-radius: 5px;
        padding: 5px;
        selection-background-color: #3a3a3a;
        selection-color: #ffffff;
    }
    QListWidget::item {
        padding: 5px;
    }
    QListWidget::item:hover {
        background: #111318;
    }
    QListWidget::item:selected {
        background: #2a2a2a;
    }
    QPushButton {
        background: #171a20;
        border: 1px solid #30333a;
        border-radius: 5px;
        padding: 7px 12px;
    }
    QPushButton:hover {
        background: #23262d;
    }
    QPushButton:pressed {
        background: #2e3138;
    }
    QPushButton:disabled {
        color: #5f6166;
        background: #101216;
        border-color: #202226;
    }
    QPushButton#PrimaryButton {
        background: #343434;
        border-color: #5a5a5a;
        color: white;
        font-weight: 700;
    }
    QPushButton#PrimaryButton:hover {
        background: #424242;
    }
    QPushButton#PrimaryButton:pressed {
        background: #4b4b4b;
    }
    QScrollArea, QFrame {
        border: 1px solid #24262b;
        border-radius: 6px;
        background: #0b0d10;
    }
    QCheckBox, QRadioButton {
        background: transparent;
        spacing: 14px;
        padding: 2px 10px 2px 0;
    }
    QCheckBox::indicator, QRadioButton::indicator {
        width: 15px;
        height: 15px;
        background: transparent;
        border: 1px solid #5a5a5a;
    }
    QCheckBox::indicator {
        border-radius: 3px;
    }
    QRadioButton::indicator {
        border-radius: 7px;
    }
    QCheckBox::indicator:unchecked, QRadioButton::indicator:unchecked {
        background: transparent;
    }
    QCheckBox::indicator:unchecked:hover, QRadioButton::indicator:unchecked:hover {
        border-color: #8a8a8a;
        background: rgba(255, 255, 255, 0.03);
    }
    QCheckBox::indicator:checked {
        border-color: #cfcfcf;
        background: rgba(230, 230, 230, 0.18);
        image: url("__CHECKBOX_TICK__");
    }
    QCheckBox::indicator:checked:hover {
        background: rgba(230, 230, 230, 0.26);
        image: url("__CHECKBOX_TICK__");
    }
    QRadioButton::indicator:checked {
        border-color: #cfcfcf;
        background: qradialgradient(cx:0.5, cy:0.5, radius:0.52, fx:0.5, fy:0.5,
                                    stop:0 #d8d8d8, stop:0.34 #d8d8d8,
                                    stop:0.36 transparent, stop:1 transparent);
    }
    QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {
        border-color: #34363b;
        background: transparent;
    }
    QCheckBox::indicator:checked:disabled {
        border-color: #4a4c51;
        background: rgba(230, 230, 230, 0.10);
        image: url("__CHECKBOX_TICK__");
    }
    QComboBox::drop-down {
        border: none;
        width: 24px;
        background: transparent;
    }
    QComboBox QAbstractItemView {
        background: #07080a;
        border: 1px solid #24262b;
        selection-background-color: #303030;
    }
    QScrollBar:vertical, QScrollBar:horizontal {
        background: #07080a;
        border: none;
        margin: 0;
    }
    QScrollBar::handle:vertical, QScrollBar::handle:horizontal {
        background: #2b2d31;
        border-radius: 4px;
        min-height: 28px;
        min-width: 28px;
    }
    QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {
        background: #3a3c40;
    }
    QScrollBar::add-line, QScrollBar::sub-line,
    QScrollBar::add-page, QScrollBar::sub-page {
        background: transparent;
        border: none;
    }
    QProgressBar {
        border: 1px solid #24262b;
        border-radius: 5px;
        text-align: center;
        background: #050608;
    }
    QProgressBar::chunk {
        background: #4a4a4a;
        border-radius: 4px;
    }
    """.replace("__CHECKBOX_TICK__", tick)


def apply_dark_palette(app: QApplication) -> None:
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor("#07080a"))
    palette.setColor(QPalette.WindowText, QColor("#ececec"))
    palette.setColor(QPalette.Base, QColor("#050608"))
    palette.setColor(QPalette.AlternateBase, QColor("#0b0d10"))
    palette.setColor(QPalette.ToolTipBase, QColor("#111318"))
    palette.setColor(QPalette.ToolTipText, QColor("#ececec"))
    palette.setColor(QPalette.Text, QColor("#ececec"))
    palette.setColor(QPalette.Button, QColor("#171a20"))
    palette.setColor(QPalette.ButtonText, QColor("#ececec"))
    palette.setColor(QPalette.BrightText, QColor("#ffffff"))
    palette.setColor(QPalette.Highlight, QColor("#3a3a3a"))
    palette.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    app.setPalette(palette)


def launch_gui() -> int:
    configure_stdio()
    try:
        import multiprocessing
        multiprocessing.freeze_support()
    except Exception:
        pass

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    apply_dark_palette(app)
    font_scale = 1.0
    if dpi_setup is not None:
        font_scale = dpi_setup.apply_font_scale(app)
    stylesheet = dark_stylesheet()
    scale_stylesheet = getattr(dpi_setup, "_scale_stylesheet_font_sizes", None) if dpi_setup is not None else None
    if scale_stylesheet is not None:
        stylesheet = scale_stylesheet(stylesheet, font_scale)
    app.setStyleSheet(stylesheet)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(launch_gui())
