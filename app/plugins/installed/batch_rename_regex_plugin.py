"""Pandora® Commander – Plugin: Batch-Rename per Regex.

Fügt dem Rechtsklick-Kontextmenü der Dateipanels den Eintrag
"Batch-Umbenennen (Regex) …" hinzu, sobald mindestens zwei Einträge
markiert sind. Der Dialog erlaubt es, ein Suchmuster (regulärer
Ausdruck) und ein Ersetzungsmuster (inklusive Rückverweisen wie
``\\1``) einzugeben und die resultierenden neuen Dateinamen vor der
eigentlichen Umbenennung in einer Vorschau-Tabelle zu prüfen.

Sicherheitsmaßnahmen:
    * Ungültige reguläre Ausdrücke werden abgefangen und angezeigt,
      statt die Anwendung zum Absturz zu bringen.
    * Namenskollisionen (zwei Quell-Dateien würden auf denselben
      Zielnamen abgebildet, oder der Zielname existiert bereits und
      gehört nicht zur aktuellen Auswahl) werden in der Vorschau rot
      markiert und blockieren den "Umbenennen"-Button.
    * Dateien, deren Name durch das Suchmuster nicht verändert wird,
      werden in der Vorschau ausgegraut angezeigt und beim
      Umbenennen übersprungen.
    * Die eigentliche Umbenennung erfolgt erst nach expliziter
      Bestätigung und wird Datei für Datei protokolliert; einzelne
      Fehler (z. B. fehlende Schreibrechte) brechen den Vorgang für
      die übrigen Dateien nicht ab.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QColor
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.core.logging_setup import get_logger
from app.plugins.plugin_manager import PandoraPlugin

logger = get_logger(__name__)

_COLUMN_OLD_NAME = 0
_COLUMN_NEW_NAME = 1
_COLUMN_STATUS = 2

_COLOR_OK = QColor("#2ecc71")
_COLOR_CONFLICT = QColor("#e74c3c")
_COLOR_UNCHANGED = QColor("#7f8c8d")


@dataclass
class _RenamePreviewEntry:
    """Eine einzelne Zeile der Umbenennungs-Vorschau."""

    original_path: Path
    new_name: str
    status: str  # "ok" | "conflict" | "unchanged" | "invalid"


class BatchRenameDialog(QDialog):
    """Dialog zur Regex-basierten Stapelumbenennung markierter Dateien."""

    def __init__(self, paths: list[Path], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Pandora® Commander – Batch-Umbenennen (Regex)")
        self.resize(720, 480)

        self._paths = sorted(paths, key=lambda path: path.name.lower())
        self._preview_entries: list[_RenamePreviewEntry] = []

        self._pattern_edit = QLineEdit()
        self._pattern_edit.setPlaceholderText(r"z. B. IMG_(\d+)\.jpg")
        self._replacement_edit = QLineEdit()
        self._replacement_edit.setPlaceholderText(r"z. B. Foto_\1.jpg")
        self._case_insensitive_hint = QLabel(
            "Groß-/Kleinschreibung wird beachtet. Rückverweise wie \\1, \\2 sind erlaubt."
        )
        self._case_insensitive_hint.setStyleSheet("color: gray; font-size: 11px;")

        self._pattern_edit.textChanged.connect(self._update_preview)
        self._replacement_edit.textChanged.connect(self._update_preview)

        self._error_label = QLabel()
        self._error_label.setStyleSheet("color: #e74c3c;")
        self._error_label.setWordWrap(True)

        self._preview_table = QTableWidget(0, 3)
        self._preview_table.setHorizontalHeaderLabels(["Aktueller Name", "Neuer Name", "Status"])
        self._preview_table.horizontalHeader().setStretchLastSection(False)
        self._preview_table.setColumnWidth(0, 280)
        self._preview_table.setColumnWidth(1, 280)
        self._preview_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

        self._button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._button_box.button(QDialogButtonBox.StandardButton.Ok).setText("Umbenennen")
        self._button_box.accepted.connect(self._on_confirm_clicked)
        self._button_box.rejected.connect(self.reject)

        pattern_row = QHBoxLayout()
        pattern_row.addWidget(QLabel("Suchmuster (Regex):"))
        pattern_row.addWidget(self._pattern_edit, stretch=1)

        replacement_row = QHBoxLayout()
        replacement_row.addWidget(QLabel("Ersetzung:"))
        replacement_row.addWidget(self._replacement_edit, stretch=1)

        layout = QVBoxLayout(self)
        layout.addLayout(pattern_row)
        layout.addLayout(replacement_row)
        layout.addWidget(self._case_insensitive_hint)
        layout.addWidget(self._error_label)
        layout.addWidget(QLabel(f"{len(self._paths)} Datei(en) markiert:"))
        layout.addWidget(self._preview_table, stretch=1)
        layout.addWidget(self._button_box)

        self._update_preview()

    def _update_preview(self) -> None:
        pattern_text = self._pattern_edit.text()
        replacement_text = self._replacement_edit.text()
        self._error_label.setText("")
        self._preview_entries = []

        compiled_pattern: re.Pattern[str] | None = None
        if pattern_text:
            try:
                compiled_pattern = re.compile(pattern_text)
            except re.error as error:
                self._error_label.setText(f"Ungültiger regulärer Ausdruck: {error}")

        target_names_seen: dict[str, int] = {}
        existing_names = {path.name for path in self._paths}

        for original_path in self._paths:
            if compiled_pattern is None:
                new_name = original_path.name
                status = "unchanged"
            else:
                try:
                    new_name = compiled_pattern.sub(replacement_text, original_path.name)
                except re.error as error:
                    new_name = original_path.name
                    status = "invalid"
                    self._error_label.setText(f"Ungültiges Ersetzungsmuster: {error}")
                    self._preview_entries.append(
                        _RenamePreviewEntry(original_path, new_name, status)
                    )
                    continue
                status = "ok" if new_name != original_path.name else "unchanged"

            target_names_seen[new_name] = target_names_seen.get(new_name, 0) + 1
            self._preview_entries.append(_RenamePreviewEntry(original_path, new_name, status))

        # Kollisionen erkennen: doppelte Zielnamen innerhalb der Auswahl,
        # oder Zielname existiert bereits bei einer nicht umbenannten Datei.
        for entry in self._preview_entries:
            if entry.status == "invalid":
                continue
            duplicate_target = target_names_seen.get(entry.new_name, 0) > 1
            collides_with_existing = (
                entry.new_name != entry.original_path.name
                and entry.new_name in existing_names
                and entry.new_name not in {p.name for p in self._paths if p == entry.original_path}
            )
            if duplicate_target or (collides_with_existing and entry.new_name in existing_names):
                entry.status = "conflict"

        self._populate_table()
        has_conflicts = any(entry.status == "conflict" for entry in self._preview_entries)
        has_invalid = any(entry.status == "invalid" for entry in self._preview_entries)
        has_any_change = any(entry.status == "ok" for entry in self._preview_entries)
        ok_button = self._button_box.button(QDialogButtonBox.StandardButton.Ok)
        ok_button.setEnabled(has_any_change and not has_conflicts and not has_invalid)

    def _populate_table(self) -> None:
        self._preview_table.setRowCount(len(self._preview_entries))
        status_labels = {
            "ok": "wird umbenannt",
            "unchanged": "unverändert",
            "conflict": "Konflikt!",
            "invalid": "ungültig",
        }
        status_colors = {
            "ok": _COLOR_OK,
            "unchanged": _COLOR_UNCHANGED,
            "conflict": _COLOR_CONFLICT,
            "invalid": _COLOR_CONFLICT,
        }
        for row, entry in enumerate(self._preview_entries):
            old_item = QTableWidgetItem(entry.original_path.name)
            new_item = QTableWidgetItem(entry.new_name)
            status_item = QTableWidgetItem(status_labels[entry.status])
            color = status_colors[entry.status]
            for item in (old_item, new_item, status_item):
                item.setForeground(color)
            self._preview_table.setItem(row, _COLUMN_OLD_NAME, old_item)
            self._preview_table.setItem(row, _COLUMN_NEW_NAME, new_item)
            self._preview_table.setItem(row, _COLUMN_STATUS, status_item)

    def _on_confirm_clicked(self) -> None:
        entries_to_rename = [entry for entry in self._preview_entries if entry.status == "ok"]
        if not entries_to_rename:
            self.reject()
            return

        preview_text = "\n".join(
            f"{entry.original_path.name}  →  {entry.new_name}" for entry in entries_to_rename[:15]
        )
        if len(entries_to_rename) > 15:
            preview_text += f"\n… und {len(entries_to_rename) - 15} weitere."

        confirmed = QMessageBox.question(
            self,
            "Umbenennung bestätigen",
            f"{len(entries_to_rename)} Datei(en) umbenennen?\n\n{preview_text}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirmed != QMessageBox.StandardButton.Yes:
            return

        failed: list[tuple[Path, str]] = []
        renamed_count = 0
        for entry in entries_to_rename:
            target_path = entry.original_path.with_name(entry.new_name)
            try:
                entry.original_path.rename(target_path)
                renamed_count += 1
            except OSError as error:
                failed.append((entry.original_path, str(error)))
                logger.error("Umbenennen fehlgeschlagen (%s): %s", entry.original_path, error)

        if failed:
            error_text = "\n".join(f"{path.name}: {message}" for path, message in failed)
            QMessageBox.warning(
                self,
                "Einige Dateien konnten nicht umbenannt werden",
                f"{renamed_count} von {len(entries_to_rename)} erfolgreich umbenannt.\n\n{error_text}",
            )
        self.accept()


class BatchRenameRegexPlugin(PandoraPlugin):
    """Plugin, das eine Regex-basierte Stapelumbenennung im Kontextmenü bereitstellt."""

    name = "Batch-Rename (Regex)"
    version = "1.0"
    author = "AKI_SystemDown®"
    description = (
        "Fügt dem Kontextmenü 'Batch-Umbenennen (Regex) …' hinzu: benennt mehrere "
        "markierte Dateien anhand eines regulären Ausdrucks um, inklusive "
        "Vorschau, Kollisionserkennung und Bestätigungsdialog vor der Ausführung."
    )

    def __init__(self) -> None:
        self._context: dict[str, Any] = {}

    def on_load(self, context: dict[str, Any]) -> None:
        self._context = context
        logger.info("%s geladen.", self.name)

    def build_context_menu_entries(
        self, context: dict[str, Any], selected_paths: list[Path]
    ) -> list[QAction]:
        if len(selected_paths) < 2:
            return []

        main_window = context.get("main_window")
        active_panel = context.get("active_panel")

        action = QAction("Batch-Umbenennen (Regex) …", main_window)
        action.triggered.connect(
            lambda checked=False, paths=list(selected_paths), panel=active_panel: self._open_dialog(
                paths, panel
            )
        )
        return [action]

    def _open_dialog(self, paths: list[Path], panel: Any) -> None:
        main_window = self._context.get("main_window")
        dialog = BatchRenameDialog(paths, parent=main_window)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            if panel is not None and hasattr(panel, "refresh"):
                panel.refresh()
