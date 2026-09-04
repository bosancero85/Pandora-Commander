"""Pandora® Commander – Hash-Dialog.

Berechnet MD5/SHA1/SHA256/SHA512 für eine Auswahl von Dateien über
HashWorker im Hintergrund und zeigt das Ergebnis tabellarisch an.
Bietet zusätzlich ein Eingabefeld, um einen erwarteten Hash für die
erste ausgewählte Datei zu verifizieren.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from app.core.filesystem.hash_tools import HashAlgorithm, HashResult, HashWorker, verify_hash
from app.core.logging_setup import get_logger
from app.themes.dark_theme import PALETTE

logger = get_logger(__name__)


class HashDialog(QDialog):
    """Dialog zur Hashberechnung einer Dateiauswahl.

    Args:
        paths: Zu hashende Dateien.
        parent: Optionales Eltern-Widget.
    """

    def __init__(self, paths: list[Path], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Hashwerkzeuge ({len(paths)} Dateien)")
        self.resize(760, 480)
        self._paths = paths
        self._worker: HashWorker | None = None

        self._checks: dict[HashAlgorithm, QCheckBox] = {
            algorithm: QCheckBox(algorithm.value.upper(), self) for algorithm in HashAlgorithm
        }
        self._checks[HashAlgorithm.SHA256].setChecked(True)

        self._compute_button = QPushButton("Berechnen", self)
        self._compute_button.clicked.connect(self._start_computation)

        self._table = QTableWidget(self)
        self._table.setColumnCount(1 + len(HashAlgorithm))
        self._table.setHorizontalHeaderLabels(["Datei"] + [a.value.upper() for a in HashAlgorithm])
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

        self._verify_edit = QLineEdit(self)
        self._verify_edit.setPlaceholderText("Erwarteten Hash der ersten Datei hier einfügen …")
        self._verify_button = QPushButton("Prüfen", self)
        self._verify_button.clicked.connect(self._verify_first_file)
        self._verify_result_label = QLabel("", self)

        self._status_label = QLabel("Bereit.", self)

        self._build_layout()

    def _build_layout(self) -> None:
        checks_row = QHBoxLayout()
        for check in self._checks.values():
            checks_row.addWidget(check)
        checks_row.addStretch(1)
        checks_row.addWidget(self._compute_button)

        verify_row = QHBoxLayout()
        verify_row.addWidget(QLabel("Verifizieren:"))
        verify_row.addWidget(self._verify_edit, 1)
        verify_row.addWidget(self._verify_button)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        buttons.button(QDialogButtonBox.StandardButton.Close).clicked.connect(self.accept)

        layout = QVBoxLayout(self)
        layout.addLayout(checks_row)
        layout.addWidget(self._status_label)
        layout.addWidget(self._table, 1)
        layout.addLayout(verify_row)
        layout.addWidget(self._verify_result_label)
        layout.addWidget(buttons)

    def _selected_algorithms(self) -> list[HashAlgorithm]:
        return [algo for algo, check in self._checks.items() if check.isChecked()]

    def _start_computation(self) -> None:
        algorithms = self._selected_algorithms()
        if not algorithms:
            self._status_label.setText("Bitte mindestens einen Algorithmus wählen.")
            return

        self._table.setRowCount(len(self._paths))
        for row, path in enumerate(self._paths):
            self._table.setItem(row, 0, QTableWidgetItem(path.name))

        self._compute_button.setEnabled(False)
        self._status_label.setText("Berechne Hashes …")

        self._worker = HashWorker(self._paths, algorithms, self)
        self._worker.progress_changed.connect(self._on_progress)
        self._worker.file_hashed.connect(self._on_file_hashed)
        self._worker.finished_all.connect(self._on_finished)
        self._worker.start()

    def _on_progress(self, index: int, total: int, path: str) -> None:
        self._status_label.setText(f"({index}/{total}) {Path(path).name}")

    def _on_file_hashed(self, result: HashResult) -> None:
        row = next((r for r in range(self._table.rowCount()) if self._table.item(r, 0) and self._table.item(r, 0).text() == result.path.name), None)
        if row is None:
            return
        if result.error:
            self._table.setItem(row, 1, QTableWidgetItem(f"Fehler: {result.error}"))
            return
        for column, algorithm in enumerate(HashAlgorithm, start=1):
            digest = result.digests.get(algorithm)
            if digest is not None:
                self._table.setItem(row, column, QTableWidgetItem(digest))

    def _on_finished(self, results: list) -> None:  # noqa: ARG002
        self._compute_button.setEnabled(True)
        self._status_label.setText("Berechnung abgeschlossen.")

    def _verify_first_file(self) -> None:
        if not self._paths:
            return
        expected = self._verify_edit.text().strip()
        if not expected:
            return
        algorithm = HashAlgorithm.SHA256
        for algo, check in self._checks.items():
            if check.isChecked():
                algorithm = algo
                break
        matches = verify_hash(self._paths[0], algorithm, expected)
        if matches:
            self._verify_result_label.setText("✓ Hash stimmt überein.")
            self._verify_result_label.setStyleSheet(f"color: {PALETTE.success};")
        else:
            self._verify_result_label.setText("✗ Hash stimmt NICHT überein.")
            self._verify_result_label.setStyleSheet(f"color: {PALETTE.danger};")
