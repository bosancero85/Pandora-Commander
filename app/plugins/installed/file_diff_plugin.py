"""Pandora® Commander – Plugin: Datei-Vergleich (Diff).

Fügt dem Rechtsklick-Kontextmenü der Dateipanels den Eintrag
"Dateien vergleichen …" hinzu, sobald genau zwei Einträge markiert
sind. Je nach erkanntem Dateityp wird eine von zwei
Vergleichsstrategien verwendet:

    * Text-Dateien (per Encoding-Erkennungsversuch als UTF-8/Latin-1
      lesbar und ohne NUL-Bytes): zeilenweiser Unified-Diff über
      ``difflib``, farblich hervorgehoben (hinzugefügt/entfernt/
      geändert) in einem Splitscreen-Textvergleich.
    * Binärdateien (Bilder, Archive, ausführbare Dateien, etc.):
      SHA-256-Hash-Vergleich plus Byte-für-Byte-Ermittlung der
      ersten abweichenden Position, da ein Zeilen-Diff hier keinen
      Sinn ergibt.

Der eigentliche Lese- und Hash-Vorgang läuft synchron im UI-Thread,
da Diff-Vergleiche naturgemäß auf einzelne, meist kleine bis
mittelgroße Dateien angewendet werden; sehr große Dateien (>50 MB)
werden vor dem Laden abgefangen und stattdessen direkt im
Binärmodus verglichen, um die Oberfläche nicht einfrieren zu lassen.
"""

from __future__ import annotations

import difflib
import hashlib
from pathlib import Path
from typing import Any

from PyQt6.QtGui import QAction, QColor, QTextCharFormat
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)
from PyQt6.QtCore import Qt

from app.core.filesystem.file_model import format_size
from app.core.logging_setup import get_logger
from app.plugins.plugin_manager import PandoraPlugin

logger = get_logger(__name__)

_MAX_TEXT_COMPARE_BYTES = 50 * 1024 * 1024  # 50 MB – darüber gilt eine Datei als "zu groß für Text-Diff".
_HASH_CHUNK_SIZE = 1024 * 1024

_COLOR_ADDED = QColor("#1f3d1f")
_COLOR_REMOVED = QColor("#3d1f1f")
_COLOR_CHANGED = QColor("#3d3a1f")


def _try_read_as_text(path: Path, max_bytes: int) -> list[str] | None:
    """Versucht, eine Datei zeilenweise als Text zu lesen.

    Returns:
        Liste der Zeilen (mit Zeilenende) bei Erfolg, sonst None
        (Datei ist zu groß oder enthält vermutlich Binärdaten).
    """
    try:
        size_bytes = path.stat().st_size
    except OSError:
        return None
    if size_bytes > max_bytes:
        return None

    try:
        raw = path.read_bytes()
    except OSError:
        return None

    if b"\x00" in raw:
        return None  # NUL-Byte deutet stark auf eine Binärdatei hin.

    for encoding in ("utf-8", "latin-1"):
        try:
            text = raw.decode(encoding)
            return text.splitlines(keepends=True)
        except UnicodeDecodeError:
            continue
    return None


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_HASH_CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def _first_difference_offset(path_a: Path, path_b: Path) -> int | None:
    """Ermittelt die erste abweichende Byte-Position zweier Dateien.

    Returns:
        Byte-Offset der ersten Abweichung, oder None, wenn die
        Dateien (soweit vergleichbar) identisch sind.
    """
    offset = 0
    with path_a.open("rb") as handle_a, path_b.open("rb") as handle_b:
        while True:
            chunk_a = handle_a.read(_HASH_CHUNK_SIZE)
            chunk_b = handle_b.read(_HASH_CHUNK_SIZE)
            if not chunk_a and not chunk_b:
                return None
            common_length = min(len(chunk_a), len(chunk_b))
            for index in range(common_length):
                if chunk_a[index] != chunk_b[index]:
                    return offset + index
            offset += common_length
            if len(chunk_a) != len(chunk_b):
                # Eine Datei ist an dieser Stelle zu Ende, die andere nicht.
                return offset


class TextDiffDialog(QDialog):
    """Zeigt einen zeilenweisen, farblich markierten Vergleich zweier Textdateien."""

    def __init__(self, path_a: Path, lines_a: list[str], path_b: Path, lines_b: list[str],
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Pandora® Commander – Diff: {path_a.name} ↔ {path_b.name}")
        self.resize(1000, 640)

        self._left_edit = QPlainTextEdit(readOnly=True)
        self._right_edit = QPlainTextEdit(readOnly=True)
        for editor in (self._left_edit, self._right_edit):
            editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        left_container = QVBoxLayout()
        left_widget = QWidget()
        left_widget.setLayout(left_container)
        left_container.addWidget(QLabel(str(path_a)))
        left_container.addWidget(self._left_edit)

        right_container = QVBoxLayout()
        right_widget = QWidget()
        right_widget.setLayout(right_container)
        right_container.addWidget(QLabel(str(path_b)))
        right_container.addWidget(self._right_edit)

        splitter.addWidget(left_widget)
        splitter.addWidget(right_widget)

        self._summary_label = QLabel()
        close_button = QPushButton("Schließen")
        close_button.clicked.connect(self.close)

        bottom_row = QHBoxLayout()
        bottom_row.addWidget(self._summary_label, stretch=1)
        bottom_row.addWidget(close_button)

        layout = QVBoxLayout(self)
        layout.addWidget(splitter, stretch=1)
        layout.addLayout(bottom_row)

        self._render_diff(lines_a, lines_b)

    def _render_diff(self, lines_a: list[str], lines_b: list[str]) -> None:
        matcher = difflib.SequenceMatcher(a=lines_a, b=lines_b, autojunk=False)
        added_count = 0
        removed_count = 0
        changed_count = 0

        left_cursor = self._left_edit.textCursor()
        right_cursor = self._right_edit.textCursor()

        for opcode, a_start, a_end, b_start, b_end in matcher.get_opcodes():
            left_segment = "".join(lines_a[a_start:a_end])
            right_segment = "".join(lines_b[b_start:b_end])

            if opcode == "equal":
                self._insert_plain(left_cursor, left_segment)
                self._insert_plain(right_cursor, right_segment)
            elif opcode == "delete":
                self._insert_highlighted(left_cursor, left_segment, _COLOR_REMOVED)
                removed_count += a_end - a_start
            elif opcode == "insert":
                self._insert_highlighted(right_cursor, right_segment, _COLOR_ADDED)
                added_count += b_end - b_start
            elif opcode == "replace":
                self._insert_highlighted(left_cursor, left_segment, _COLOR_CHANGED)
                self._insert_highlighted(right_cursor, right_segment, _COLOR_CHANGED)
                changed_count += max(a_end - a_start, b_end - b_start)

        self._summary_label.setText(
            f"{changed_count} geänderte, {added_count} hinzugefügte, "
            f"{removed_count} entfernte Zeile(n)."
        )

    @staticmethod
    def _insert_plain(cursor, text: str) -> None:
        cursor.insertText(text)

    @staticmethod
    def _insert_highlighted(cursor, text: str, color: QColor) -> None:
        if not text:
            return
        char_format = QTextCharFormat()
        char_format.setBackground(color)
        cursor.insertText(text, char_format)


class BinaryDiffDialog(QDialog):
    """Zeigt das Ergebnis eines Hash-/Byte-Vergleichs zweier Binärdateien."""

    def __init__(self, path_a: Path, path_b: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Pandora® Commander – Diff: {path_a.name} ↔ {path_b.name}")
        self.resize(560, 260)

        size_a = path_a.stat().st_size
        size_b = path_b.stat().st_size
        hash_a = _hash_file(path_a)
        hash_b = _hash_file(path_b)
        identical = hash_a == hash_b

        lines = [
            f"Datei A: {path_a}",
            f"  Größe: {format_size(size_a)}    SHA-256: {hash_a}",
            "",
            f"Datei B: {path_b}",
            f"  Größe: {format_size(size_b)}    SHA-256: {hash_b}",
            "",
        ]
        if identical:
            lines.append("✅ Die Dateien sind inhaltlich identisch.")
        else:
            lines.append("❌ Die Dateien unterscheiden sich.")
            if size_a != size_b:
                lines.append(f"   Unterschiedliche Größe: {format_size(size_a)} vs. {format_size(size_b)}.")
            offset = _first_difference_offset(path_a, path_b)
            if offset is not None:
                lines.append(f"   Erste Abweichung bei Byte-Offset {offset}.")

        text_view = QPlainTextEdit(readOnly=True)
        text_view.setPlainText("\n".join(lines))

        close_button = QPushButton("Schließen")
        close_button.clicked.connect(self.close)

        layout = QVBoxLayout(self)
        layout.addWidget(text_view, stretch=1)
        layout.addWidget(close_button, alignment=Qt.AlignmentFlag.AlignRight)


class FileDiffPlugin(PandoraPlugin):
    """Plugin, das einen Vergleich zweier markierter Dateien im Kontextmenü bereitstellt."""

    name = "Datei-Vergleich (Diff)"
    version = "1.0"
    author = "AKI_SystemDown®"
    description = (
        "Fügt dem Kontextmenü 'Dateien vergleichen …' hinzu: zeigt bei zwei "
        "markierten Textdateien einen farblich markierten Zeilen-Diff, bei "
        "Binärdateien einen Hash- und Byte-Offset-Vergleich."
    )

    def __init__(self) -> None:
        self._context: dict[str, Any] = {}
        self._open_dialogs: list[QDialog] = []

    def on_load(self, context: dict[str, Any]) -> None:
        self._context = context
        logger.info("%s geladen.", self.name)

    def on_unload(self) -> None:
        for dialog in self._open_dialogs:
            dialog.close()
        self._open_dialogs.clear()

    def build_context_menu_entries(
        self, context: dict[str, Any], selected_paths: list[Path]
    ) -> list[QAction]:
        file_paths = [path for path in selected_paths if path.is_file()]
        if len(file_paths) != 2:
            return []

        main_window = context.get("main_window")
        action = QAction("Dateien vergleichen …", main_window)
        action.triggered.connect(
            lambda checked=False, paths=tuple(file_paths): self._compare(paths[0], paths[1])
        )
        return [action]

    def _compare(self, path_a: Path, path_b: Path) -> None:
        main_window = self._context.get("main_window")

        lines_a = _try_read_as_text(path_a, _MAX_TEXT_COMPARE_BYTES)
        lines_b = _try_read_as_text(path_b, _MAX_TEXT_COMPARE_BYTES)

        try:
            if lines_a is not None and lines_b is not None:
                dialog: QDialog = TextDiffDialog(path_a, lines_a, path_b, lines_b, parent=main_window)
            else:
                dialog = BinaryDiffDialog(path_a, path_b, parent=main_window)
        except OSError as error:
            QMessageBox.critical(
                main_window, "Fehler beim Vergleich", f"Dateien konnten nicht gelesen werden: {error}"
            )
            return

        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.destroyed.connect(
            lambda: self._open_dialogs.remove(dialog) if dialog in self._open_dialogs else None
        )
        self._open_dialogs.append(dialog)
        dialog.show()
