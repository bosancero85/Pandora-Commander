"""Pandora® Commander – Dialog für Datei-/Ordnervergleich.

Vergleicht die aktuellen Pfade des linken und rechten Panels
rekursiv über CompareWorker und zeigt das Ergebnis farblich
kategorisiert an (identisch / unterschiedlich / nur links / nur rechts).
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from app.core.filesystem.compare import CompareEntry, CompareOptions, CompareStatus, CompareWorker
from app.core.filesystem.hash_tools import HashAlgorithm
from app.core.logging_setup import get_logger
from app.themes.dark_theme import PALETTE

logger = get_logger(__name__)

_STATUS_LABELS = {
    CompareStatus.IDENTICAL: "Identisch",
    CompareStatus.DIFFERENT: "Unterschiedlich",
    CompareStatus.ONLY_LEFT: "Nur links",
    CompareStatus.ONLY_RIGHT: "Nur rechts",
}

_STATUS_COLORS = {
    CompareStatus.IDENTICAL: PALETTE.text_secondary,
    CompareStatus.DIFFERENT: PALETTE.warning,
    CompareStatus.ONLY_LEFT: PALETTE.accent,
    CompareStatus.ONLY_RIGHT: PALETTE.accent,
}


class CompareDialog(QDialog):
    """Dialog zum Vergleich zweier Ordnerbäume.

    Args:
        left_root: Wurzelordner der linken Seite.
        right_root: Wurzelordner der rechten Seite.
        parent: Optionales Eltern-Widget.
    """

    def __init__(self, left_root: Path, right_root: Path, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Ordnervergleich")
        self.resize(760, 540)
        self._left_root = left_root
        self._right_root = right_root
        self._worker: CompareWorker | None = None

        self._header_label = QLabel(f"{left_root}  ↔  {right_root}", self)

        self._hash_check = QCheckBox("Exakten Hashvergleich verwenden (langsamer)", self)
        self._algorithm_combo = QComboBox(self)
        self._algorithm_combo.addItems([a.value.upper() for a in HashAlgorithm])
        self._algorithm_combo.setCurrentText(HashAlgorithm.SHA256.value.upper())
        self._only_diff_check = QCheckBox("Nur Unterschiede anzeigen", self)

        self._start_button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Close, self
        )
        self._start_button_box.button(QDialogButtonBox.StandardButton.Ok).setText("Vergleichen")
        self._start_button_box.accepted.connect(self._start_compare)
        self._start_button_box.rejected.connect(self.reject)

        self._status_label = QLabel("Bereit.", self)
        self._tree = QTreeWidget(self)
        self._tree.setHeaderLabels(["Relativer Pfad", "Status"])
        self._tree.setColumnWidth(0, 480)

        self._build_layout()

    def _build_layout(self) -> None:
        options_row = QHBoxLayout()
        options_row.addWidget(self._hash_check)
        options_row.addWidget(self._algorithm_combo)
        options_row.addWidget(self._only_diff_check)
        options_row.addStretch(1)

        layout = QVBoxLayout(self)
        layout.addWidget(self._header_label)
        layout.addLayout(options_row)
        layout.addWidget(self._status_label)
        layout.addWidget(self._tree, 1)
        layout.addWidget(self._start_button_box)

    def _start_compare(self) -> None:
        options = CompareOptions(
            use_hash=self._hash_check.isChecked(),
            algorithm=HashAlgorithm(self._algorithm_combo.currentText().lower()),
            only_show_differences=self._only_diff_check.isChecked(),
        )
        self._tree.clear()
        self._status_label.setText("Vergleiche …")
        self._start_button_box.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)

        self._worker = CompareWorker(self._left_root, self._right_root, options, self)
        self._worker.progress_changed.connect(self._on_progress)
        self._worker.finished_compare.connect(self._on_finished)
        self._worker.start()

    def _on_progress(self, index: int, total: int, relative_path: str) -> None:
        self._status_label.setText(f"({index}/{total}) {relative_path}")

    def _on_finished(self, entries: list[CompareEntry]) -> None:
        self._status_label.setText(f"Vergleich abgeschlossen: {len(entries)} Einträge.")
        self._start_button_box.button(QDialogButtonBox.StandardButton.Ok).setEnabled(True)

        for entry in entries:
            item = QTreeWidgetItem([entry.relative_path, _STATUS_LABELS[entry.status]])
            color = QColor(_STATUS_COLORS[entry.status])
            item.setForeground(1, color)
            self._tree.addTopLevelItem(item)
