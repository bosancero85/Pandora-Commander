"""Pandora® Commander – Plugin: Leere-Ordner-Finder.

Fügt dem Rechtsklick-Kontextmenü der Dateipanels den Eintrag
"Leere Ordner suchen …" hinzu, sobald mindestens ein Ordner markiert
ist. Durchsucht das markierte Verzeichnis (oder die markierten
Verzeichnisse) rekursiv nach Unterordnern, die keine Dateien
enthalten – auch dann, wenn sie selbst wiederum nur weitere leere
Unterordner enthalten ("verschachtelt leer").

Ein Ordner gilt als leer, wenn er (rekursiv betrachtet) keine
einzige Datei enthält; enthält er ausschließlich weitere leere
Unterordner, gelten sowohl er selbst als auch alle seine
Unterordner als leer und werden gemeinsam in der Ergebnisliste
aufgeführt (der oberste leere Ordner reicht zum Löschen – seine
leeren Kinder verschwinden automatisch mit).

Der Ergebnisdialog zeigt alle gefundenen leeren Ordner mit Pfad,
per Checkbox einzeln abwählbar (Standard: alle ausgewählt), und
erlaubt das gebündelte Löschen nach Bestätigung. Da beim Löschen
eines obersten leeren Ordners auch dessen leere Unterordner
mitentfernt werden, wird die Auswahl vor dem Löschen automatisch
bereinigt (Unterordner eines bereits zum Löschen markierten
Ordners werden nicht zusätzlich einzeln gelöscht, um doppelte
"Ordner nicht gefunden"-Fehler zu vermeiden).

Der Scan läuft in einem Hintergrund-Thread, damit große
Verzeichnisbäume die Oberfläche nicht blockieren.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QThread, Qt, pyqtSignal
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.core.logging_setup import get_logger
from app.plugins.plugin_manager import PandoraPlugin

logger = get_logger(__name__)


def _find_empty_directories(root: Path) -> list[Path]:
    """Findet rekursiv alle leeren Ordner unterhalb (und einschließlich) ``root``.

    Arbeitet bottom-up (``topdown=False``), damit ein Ordner, dessen
    Unterordner bereits als leer erkannt wurden, selbst ebenfalls
    korrekt als leer gilt, sofern er keine eigenen Dateien enthält.
    """
    empty_directories: set[str] = set()
    for current_root, subdirectories, files in os.walk(root, topdown=False, onerror=lambda error: None):
        if files:
            continue
        # Ordner gilt als leer, wenn alle direkten Unterordner
        # bereits als leer markiert wurden (oder es gar keine gibt).
        if all(str(Path(current_root) / name) in empty_directories for name in subdirectories):
            empty_directories.add(current_root)

    return [Path(path_str) for path_str in sorted(empty_directories)]


class _EmptyFolderScanWorker(QThread):
    """Durchsucht eine Liste von Wurzelordnern nach leeren Unterordnern."""

    finished_with_result = pyqtSignal(list)  # list[Path]
    failed = pyqtSignal(str)

    def __init__(self, roots: list[Path]) -> None:
        super().__init__()
        self._roots = roots

    def run(self) -> None:  # noqa: D102 - QThread-Standardmethode
        try:
            all_empty: list[Path] = []
            seen: set[str] = set()
            for root in self._roots:
                for empty_path in _find_empty_directories(root):
                    key = str(empty_path)
                    if key not in seen:
                        seen.add(key)
                        all_empty.append(empty_path)
        except OSError as error:
            self.failed.emit(str(error))
            return

        self.finished_with_result.emit(all_empty)


def _remove_redundant_children(paths: list[Path]) -> list[Path]:
    """Filtert Pfade heraus, die bereits Unterordner eines anderen ausgewählten Pfads sind.

    Verhindert, dass beim Löschen sowohl ein leerer Ordner als auch
    einer seiner (ebenfalls leeren) Unterordner einzeln adressiert
    werden – nach dem Löschen des Elternordners existiert der
    Unterordner ohnehin nicht mehr.
    """
    sorted_paths = sorted(paths, key=lambda path: len(path.parts))
    kept: list[Path] = []
    for candidate in sorted_paths:
        if not any(candidate != kept_path and kept_path in candidate.parents for kept_path in kept):
            kept.append(candidate)
    return kept


class EmptyFolderFinderDialog(QDialog):
    """Zeigt gefundene leere Ordner mit Checkbox-Auswahl zum gebündelten Löschen."""

    def __init__(self, roots: list[Path], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Pandora® Commander – Leere Ordner")
        self.resize(640, 480)

        self._status_label = QLabel(f"Durchsuche {len(roots)} Ordner …")
        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 0)

        self._list_widget = QListWidget()
        self._list_widget.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)

        self._delete_button = QPushButton("Markierte Ordner löschen")
        self._delete_button.setEnabled(False)
        self._delete_button.clicked.connect(self._on_delete_clicked)

        close_button = QPushButton("Schließen")
        close_button.clicked.connect(self.close)

        layout = QVBoxLayout(self)
        layout.addWidget(self._status_label)
        layout.addWidget(self._progress_bar)
        layout.addWidget(self._list_widget, stretch=1)
        layout.addWidget(self._delete_button)
        layout.addWidget(close_button)

        self._worker = _EmptyFolderScanWorker(roots)
        self._worker.finished_with_result.connect(self._on_scan_finished)
        self._worker.failed.connect(self._on_scan_failed)
        self._worker.start()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt-Überschreibung
        if self._worker.isRunning():
            self._worker.wait(50)
        super().closeEvent(event)

    def _on_scan_finished(self, empty_directories: list[Path]) -> None:
        self._progress_bar.setRange(0, 1)
        self._progress_bar.setValue(1)

        if not empty_directories:
            self._status_label.setText("Keine leeren Ordner gefunden.")
            return

        self._status_label.setText(f"{len(empty_directories)} leere(r) Ordner gefunden:")
        for directory in empty_directories:
            item = QListWidgetItem(str(directory))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            item.setData(Qt.ItemDataRole.UserRole, str(directory))
            self._list_widget.addItem(item)

        self._delete_button.setEnabled(True)

    def _on_scan_failed(self, message: str) -> None:
        self._status_label.setText("Fehler beim Scan.")
        self._progress_bar.setRange(0, 1)
        self._progress_bar.setValue(0)
        QMessageBox.critical(self, "Fehler", f"Leere Ordner konnten nicht gesucht werden: {message}")

    def _on_delete_clicked(self) -> None:
        checked_paths: list[Path] = []
        for row in range(self._list_widget.count()):
            item = self._list_widget.item(row)
            if item.checkState() == Qt.CheckState.Checked:
                checked_paths.append(Path(item.data(Qt.ItemDataRole.UserRole)))

        if not checked_paths:
            QMessageBox.information(self, "Keine Auswahl", "Es sind keine Ordner zum Löschen markiert.")
            return

        paths_to_delete = _remove_redundant_children(checked_paths)
        preview = "\n".join(str(path) for path in paths_to_delete[:15])
        if len(paths_to_delete) > 15:
            preview += f"\n… und {len(paths_to_delete) - 15} weitere."

        confirmed = QMessageBox.question(
            self,
            "Löschen bestätigen",
            f"{len(paths_to_delete)} leere(n) Ordner unwiderruflich löschen?\n\n{preview}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirmed != QMessageBox.StandardButton.Yes:
            return

        deleted_count = 0
        failed_paths: list[tuple[Path, str]] = []
        for directory in paths_to_delete:
            try:
                os.removedirs(directory)
                deleted_count += 1
            except OSError as error:
                failed_paths.append((directory, str(error)))

        self._status_label.setText(f"{deleted_count} Ordner gelöscht.")
        if failed_paths:
            error_text = "\n".join(f"{path}: {message}" for path, message in failed_paths)
            QMessageBox.warning(self, "Einige Ordner konnten nicht gelöscht werden", error_text)

        self._list_widget.clear()
        self._delete_button.setEnabled(False)


class EmptyFolderFinderPlugin(PandoraPlugin):
    """Plugin zum rekursiven Finden und gebündelten Löschen leerer Ordner."""

    name = "Leere-Ordner-Finder"
    version = "1.0"
    author = "AKI_SystemDown®"
    description = (
        "Fügt dem Kontextmenü 'Leere Ordner suchen …' hinzu: findet rekursiv alle "
        "leeren (auch verschachtelt leeren) Unterordner markierter Verzeichnisse und "
        "erlaubt das gebündelte Löschen nach Bestätigung."
    )

    def __init__(self) -> None:
        self._context: dict[str, Any] = {}
        self._open_dialogs: list[EmptyFolderFinderDialog] = []

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
        directories = [path for path in selected_paths if path.is_dir()]
        if not directories:
            return []

        main_window = context.get("main_window")
        action = QAction("Leere Ordner suchen …", main_window)
        action.triggered.connect(
            lambda checked=False, paths=directories: self._open_dialog(paths)
        )
        return [action]

    def _open_dialog(self, directories: list[Path]) -> None:
        main_window = self._context.get("main_window")
        dialog = EmptyFolderFinderDialog(directories, parent=main_window)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.destroyed.connect(
            lambda: self._open_dialogs.remove(dialog) if dialog in self._open_dialogs else None
        )
        self._open_dialogs.append(dialog)
        dialog.show()
