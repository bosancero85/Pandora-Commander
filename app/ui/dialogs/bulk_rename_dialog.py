"""Pandora® Commander – Dialog für Massenumbenennung.

Bietet eine Live-Vorschau der Ergebnisnamen, bevor die Umbenennung
tatsächlich mit apply_rename() angewendet wird. Konfliktbehaftete
Zeilen werden farblich hervorgehoben.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from app.core.filesystem.bulk_rename import RenameRule, apply_rename, preview_rename
from app.core.logging_setup import get_logger
from app.themes.dark_theme import PALETTE

logger = get_logger(__name__)


class BulkRenameDialog(QDialog):
    """Dialog zur Massenumbenennung einer Dateiauswahl.

    Args:
        paths: Die umzubenennenden Dateien.
        parent: Optionales Eltern-Widget.
    """

    def __init__(self, paths: list[Path], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Massenumbenennung ({len(paths)} Dateien)")
        self.resize(720, 520)
        self._paths = paths

        self._pattern_edit = QLineEdit("{name}_{n}{ext}", self)
        self._start_spin = QSpinBox(self)
        self._start_spin.setRange(0, 999999)
        self._start_spin.setValue(1)
        self._step_spin = QSpinBox(self)
        self._step_spin.setRange(1, 1000)
        self._step_spin.setValue(1)
        self._padding_spin = QSpinBox(self)
        self._padding_spin.setRange(1, 10)
        self._padding_spin.setValue(2)

        self._search_edit = QLineEdit(self)
        self._search_edit.setPlaceholderText("Regex-Suche (optional)")
        self._replace_edit = QLineEdit(self)
        self._replace_edit.setPlaceholderText("Ersetzen durch")

        self._lowercase_check = QCheckBox("Kleinschreibung erzwingen", self)
        self._uppercase_check = QCheckBox("Großschreibung erzwingen", self)

        self._table = QTableWidget(self)
        self._table.setColumnCount(2)
        self._table.setHorizontalHeaderLabels(["Aktueller Name", "Neuer Name"])
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        buttons.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(self._apply)
        buttons.rejected.connect(self.reject)

        self._build_layout(buttons)
        self._connect_live_preview()
        self._update_preview()

    def _build_layout(self, buttons: QDialogButtonBox) -> None:
        form = QGridLayout()
        form.addWidget(QLabel("Muster:"), 0, 0)
        form.addWidget(self._pattern_edit, 0, 1, 1, 3)
        form.addWidget(QLabel("Start:"), 1, 0)
        form.addWidget(self._start_spin, 1, 1)
        form.addWidget(QLabel("Schritt:"), 1, 2)
        form.addWidget(self._step_spin, 1, 3)
        form.addWidget(QLabel("Stellen:"), 2, 0)
        form.addWidget(self._padding_spin, 2, 1)
        form.addWidget(QLabel("Suchen (Regex):"), 3, 0)
        form.addWidget(self._search_edit, 3, 1)
        form.addWidget(QLabel("Ersetzen:"), 3, 2)
        form.addWidget(self._replace_edit, 3, 3)
        form.addWidget(self._lowercase_check, 4, 0, 1, 2)
        form.addWidget(self._uppercase_check, 4, 2, 1, 2)

        hint = QLabel("Platzhalter: {name} {ext} {n} {date} {time}", self)
        hint.setStyleSheet(f"color: {PALETTE.text_secondary};")

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(hint)
        layout.addWidget(self._table, 1)
        layout.addWidget(buttons)

    def _connect_live_preview(self) -> None:
        for widget in (
            self._pattern_edit,
            self._search_edit,
            self._replace_edit,
        ):
            widget.textChanged.connect(self._update_preview)
        for spin in (self._start_spin, self._step_spin, self._padding_spin):
            spin.valueChanged.connect(self._update_preview)
        for check in (self._lowercase_check, self._uppercase_check):
            check.toggled.connect(self._update_preview)

    def _current_rule(self) -> RenameRule:
        return RenameRule(
            pattern=self._pattern_edit.text() or "{name}{ext}",
            start_number=self._start_spin.value(),
            step=self._step_spin.value(),
            padding=self._padding_spin.value(),
            search_regex=self._search_edit.text(),
            replace_with=self._replace_edit.text(),
            lowercase=self._lowercase_check.isChecked(),
            uppercase=self._uppercase_check.isChecked(),
        )

    def _update_preview(self) -> None:
        self._preview_items = preview_rename(self._paths, self._current_rule())
        self._table.setRowCount(len(self._preview_items))
        for row, item in enumerate(self._preview_items):
            original_item = QTableWidgetItem(item.original_path.name)
            new_item = QTableWidgetItem(item.new_name)
            if item.conflict:
                new_item.setForeground(QColor(PALETTE.danger))
                new_item.setText(f"{item.new_name}  ⚠ Konflikt")
            self._table.setItem(row, 0, original_item)
            self._table.setItem(row, 1, new_item)

    def _apply(self) -> None:
        conflicts = sum(1 for item in self._preview_items if item.conflict)
        if conflicts:
            QMessageBox.warning(
                self,
                "Konflikte vorhanden",
                f"{conflicts} Datei(en) haben Namenskonflikte und werden übersprungen.",
            )

        outcome = apply_rename(self._preview_items)
        if outcome.failed:
            details = "\n".join(f"{path.name}: {reason}" for path, reason in outcome.failed)
            QMessageBox.warning(self, "Einige Dateien konnten nicht umbenannt werden", details)

        logger.info("Massenumbenennung: %d erfolgreich, %d fehlgeschlagen", len(outcome.renamed), len(outcome.failed))
        self.accept()
