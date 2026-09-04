"""Pandora® Commander – Suchdialog.

Modaler (aber nicht blockierender) Dialog zur Konfiguration und
Anzeige einer Dateisuche. Nutzt SearchWorker aus
core.search.search_engine, damit die Oberfläche während der Suche
reaktionsfähig bleibt. Ein Doppelklick auf einen Treffer schließt den
Dialog und meldet den gewählten Pfad über das Signal ``path_activated``.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

from app.core.logging_setup import get_logger
from app.core.search.search_engine import NamePatternMode, SearchCriteria, SearchHit, SearchWorker

logger = get_logger(__name__)


class SearchDialog(QDialog):
    """Dialog zur Konfiguration und Durchführung einer Dateisuche.

    Signals:
        path_activated: Wird gesendet, wenn ein Treffer doppelt
            angeklickt wird; übergibt den gewählten Path.

    Args:
        start_path: Vorausgefüllter Startordner für die Suche.
        parent: Optionales Eltern-Widget.
    """

    path_activated = pyqtSignal(Path)

    def __init__(self, start_path: Path, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Dateisuche")
        self.resize(640, 520)
        self._worker: SearchWorker | None = None

        self._path_edit = QLineEdit(str(start_path), self)
        browse_button = QPushButton("Durchsuchen …", self)
        browse_button.clicked.connect(self._browse_folder)

        self._name_edit = QLineEdit(self)
        self._name_edit.setPlaceholderText("z. B. *.txt oder Regex")
        self._pattern_mode_combo = QComboBox(self)
        self._pattern_mode_combo.addItems(["Wildcard", "Regex"])

        self._content_edit = QLineEdit(self)
        self._content_edit.setPlaceholderText("Optional: Textsuche im Dateiinhalt")

        self._min_size_edit = QLineEdit(self)
        self._min_size_edit.setPlaceholderText("Min. Größe (Bytes)")
        self._max_size_edit = QLineEdit(self)
        self._max_size_edit.setPlaceholderText("Max. Größe (Bytes)")

        self._extensions_edit = QLineEdit(self)
        self._extensions_edit.setPlaceholderText("Endungen, z. B. txt,py,md")

        self._case_sensitive_check = QCheckBox("Groß-/Kleinschreibung beachten", self)
        self._subfolders_check = QCheckBox("Unterordner einbeziehen", self)
        self._subfolders_check.setChecked(True)

        self._start_button = QPushButton("Suche starten", self)
        self._start_button.clicked.connect(self._start_search)
        self._cancel_button = QPushButton("Abbrechen", self)
        self._cancel_button.clicked.connect(self._cancel_search)
        self._cancel_button.setEnabled(False)

        self._status_label = QLabel("Bereit.", self)
        self._results_list = QListWidget(self)
        self._results_list.itemDoubleClicked.connect(self._on_item_double_clicked)

        self._build_layout(browse_button)

    def _build_layout(self, browse_button: QPushButton) -> None:
        form = QGridLayout()
        form.addWidget(QLabel("Startordner:"), 0, 0)
        form.addWidget(self._path_edit, 0, 1)
        form.addWidget(browse_button, 0, 2)

        form.addWidget(QLabel("Namensmuster:"), 1, 0)
        form.addWidget(self._name_edit, 1, 1)
        form.addWidget(self._pattern_mode_combo, 1, 2)

        form.addWidget(QLabel("Inhalt enthält:"), 2, 0)
        form.addWidget(self._content_edit, 2, 1, 1, 2)

        form.addWidget(QLabel("Größe:"), 3, 0)
        size_row = QHBoxLayout()
        size_row.addWidget(self._min_size_edit)
        size_row.addWidget(self._max_size_edit)
        form.addLayout(size_row, 3, 1, 1, 2)

        form.addWidget(QLabel("Dateitypen:"), 4, 0)
        form.addWidget(self._extensions_edit, 4, 1, 1, 2)

        options_row = QHBoxLayout()
        options_row.addWidget(self._case_sensitive_check)
        options_row.addWidget(self._subfolders_check)

        button_row = QHBoxLayout()
        button_row.addWidget(self._start_button)
        button_row.addWidget(self._cancel_button)
        button_row.addStretch(1)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addLayout(options_row)
        layout.addLayout(button_row)
        layout.addWidget(self._status_label)
        layout.addWidget(self._results_list, 1)

    def _browse_folder(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Startordner wählen", self._path_edit.text())
        if chosen:
            self._path_edit.setText(chosen)

    def _build_criteria(self) -> SearchCriteria | None:
        root = Path(self._path_edit.text())
        if not root.is_dir():
            self._status_label.setText("Ungültiger Startordner.")
            return None

        min_size = int(self._min_size_edit.text()) if self._min_size_edit.text().strip().isdigit() else None
        max_size = int(self._max_size_edit.text()) if self._max_size_edit.text().strip().isdigit() else None
        extensions = [e.strip() for e in self._extensions_edit.text().split(",") if e.strip()] or None

        return SearchCriteria(
            root_path=root,
            name_pattern=self._name_edit.text(),
            pattern_mode=NamePatternMode.REGEX if self._pattern_mode_combo.currentIndex() == 1 else NamePatternMode.WILDCARD,
            case_sensitive=self._case_sensitive_check.isChecked(),
            min_size_bytes=min_size,
            max_size_bytes=max_size,
            extensions=extensions,
            content_pattern=self._content_edit.text(),
            include_subfolders=self._subfolders_check.isChecked(),
        )

    def _start_search(self) -> None:
        criteria = self._build_criteria()
        if criteria is None:
            return

        self._results_list.clear()
        self._status_label.setText("Suche läuft …")
        self._start_button.setEnabled(False)
        self._cancel_button.setEnabled(True)

        self._worker = SearchWorker(criteria, self)
        self._worker.hit_found.connect(self._on_hit_found)
        self._worker.search_finished.connect(self._on_search_finished)
        self._worker.start()

    def _cancel_search(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self._status_label.setText("Suche wird abgebrochen …")

    def _on_hit_found(self, hit: SearchHit) -> None:
        label = f"{hit.path}  ({hit.size_bytes:,} Bytes)".replace(",", ".")
        if hit.matched_line:
            label += f"\n    → {hit.matched_line}"
        item = QListWidgetItem(label)
        item.setData(Qt.ItemDataRole.UserRole, str(hit.path))
        self._results_list.addItem(item)

    def _on_search_finished(self, count: int) -> None:
        self._status_label.setText(f"Suche abgeschlossen: {count} Treffer.")
        self._start_button.setEnabled(True)
        self._cancel_button.setEnabled(False)

    def _on_item_double_clicked(self, item: QListWidgetItem) -> None:
        path_str = item.data(Qt.ItemDataRole.UserRole)
        if path_str:
            self.path_activated.emit(Path(path_str))
            self.accept()
