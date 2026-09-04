"""Pandora® Commander – Plugin: Duplikat-Finder.

Durchsucht ein wählbares Verzeichnis rekursiv nach inhaltlich
identischen Dateien und stellt die gefundenen Gruppen in einem
eigenen Dialog dar, aus dem heraus überzählige Kopien gezielt
gelöscht werden können.

Vorgehen beim Scan (zweistufig, um bei großen Verzeichnissen nicht
jede Datei hashen zu müssen):
    1. Alle Dateien werden nach ihrer Dateigröße gruppiert.
       Gruppen mit nur einer Datei scheiden sofort aus – zwei
       Dateien unterschiedlicher Größe können nie identisch sein.
    2. Innerhalb jeder verbliebenen Größen-Gruppe wird pro Datei ein
       SHA-256-Hash gebildet. Dateien mit demselben Hash gelten als
       Duplikate und werden zu einer Ergebnisgruppe zusammengefasst.

Der komplette Scan läuft in einem QThread, damit die Oberfläche
währenddessen reaktionsfähig bleibt; der Nutzer kann jederzeit
abbrechen.

Im Ergebnisdialog wird pro Gruppe automatisch die erste Datei als
"behalten" markiert (Checkbox deaktiviert) und alle weiteren Dateien
zum Löschen vorausgewählt – das deckt den häufigsten Anwendungsfall
("alle bis auf eines löschen") bereits als Standardvorschlag ab,
lässt sich aber vor dem Löschen für jede Datei einzeln anpassen.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QThread, Qt, pyqtSignal
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.core.filesystem.file_model import format_size
from app.core.logging_setup import get_logger
from app.plugins.plugin_manager import PandoraPlugin

logger = get_logger(__name__)

_HASH_CHUNK_SIZE = 1024 * 1024  # 1 MiB pro Lese-Block beim Hashen.
_COLUMN_PATH = 0
_COLUMN_SIZE = 1

_PathData = int  # Qt.ItemDataRole.UserRole speichert hier den vollen Pfad als str.


@dataclass
class _DuplicateGroup:
    """Eine Gruppe inhaltlich identischer Dateien."""

    file_hash: str
    size_bytes: int
    paths: list[Path] = field(default_factory=list)


class _DuplicateScanWorker(QThread):
    """Durchsucht ein Verzeichnis rekursiv nach Duplikaten, ohne die UI zu blockieren."""

    progress_changed = pyqtSignal(int, int, str)  # aktuell, gesamt, aktueller Dateiname
    finished_with_result = pyqtSignal(list)  # list[_DuplicateGroup]
    failed = pyqtSignal(str)

    def __init__(self, root: Path) -> None:
        super().__init__()
        self._root = root
        self._abort_requested = False

    def request_abort(self) -> None:
        self._abort_requested = True

    def run(self) -> None:  # noqa: D102 - QThread-Standardmethode
        try:
            all_files = [entry for entry in self._root.rglob("*") if self._is_regular_file(entry)]
        except OSError as error:
            self.failed.emit(f"Verzeichnis konnte nicht durchsucht werden: {error}")
            return

        size_groups: dict[int, list[Path]] = {}
        for file_path in all_files:
            try:
                size_bytes = file_path.stat().st_size
            except OSError:
                continue
            size_groups.setdefault(size_bytes, []).append(file_path)

        candidates = [paths for paths in size_groups.values() if len(paths) > 1]
        total_candidates = sum(len(paths) for paths in candidates)
        processed = 0

        hash_groups: dict[tuple[int, str], _DuplicateGroup] = {}

        for paths in candidates:
            for file_path in paths:
                if self._abort_requested:
                    return
                processed += 1
                self.progress_changed.emit(processed, total_candidates, file_path.name)
                try:
                    file_hash = self._hash_file(file_path)
                    size_bytes = file_path.stat().st_size
                except OSError as error:
                    logger.warning("Datei übersprungen (%s): %s", file_path, error)
                    continue

                key = (size_bytes, file_hash)
                group = hash_groups.get(key)
                if group is None:
                    group = _DuplicateGroup(file_hash=file_hash, size_bytes=size_bytes)
                    hash_groups[key] = group
                group.paths.append(file_path)

        result = [group for group in hash_groups.values() if len(group.paths) > 1]
        result.sort(key=lambda group: group.size_bytes * len(group.paths), reverse=True)
        self.finished_with_result.emit(result)

    @staticmethod
    def _is_regular_file(path: Path) -> bool:
        try:
            return path.is_file() and not path.is_symlink()
        except OSError:
            return False

    @staticmethod
    def _hash_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while chunk := handle.read(_HASH_CHUNK_SIZE):
                digest.update(chunk)
        return digest.hexdigest()


class DuplicateFinderDialog(QDialog):
    """Zeigt den Verzeichnis-Auswahl-, Scan- und Ergebnisbereich des Duplikat-Finders."""

    def __init__(self, start_directory: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Pandora® Commander – Duplikat-Finder")
        self.resize(760, 560)
        self.setModal(False)

        self._worker: _DuplicateScanWorker | None = None
        self._groups: list[_DuplicateGroup] = []

        self._directory_edit = QLineEdit(str(start_directory))
        browse_button = QPushButton("Durchsuchen …")
        browse_button.clicked.connect(self._on_browse_clicked)

        self._scan_button = QPushButton("Scan starten")
        self._scan_button.clicked.connect(self._on_scan_clicked)
        self._abort_button = QPushButton("Abbrechen")
        self._abort_button.setEnabled(False)
        self._abort_button.clicked.connect(self._on_abort_clicked)

        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 1)
        self._progress_bar.setValue(0)

        self._status_label = QLabel("Bereit.")

        self._result_tree = QTreeWidget()
        self._result_tree.setColumnCount(2)
        self._result_tree.setHeaderLabels(["Datei / Gruppe", "Größe"])
        self._result_tree.header().resizeSection(0, 520)
        self._result_tree.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)

        self._delete_button = QPushButton("Markierte Dateien löschen")
        self._delete_button.setEnabled(False)
        self._delete_button.clicked.connect(self._on_delete_clicked)

        close_button = QPushButton("Schließen")
        close_button.clicked.connect(self.close)

        directory_row = QHBoxLayout()
        directory_row.addWidget(QLabel("Verzeichnis:"))
        directory_row.addWidget(self._directory_edit, stretch=1)
        directory_row.addWidget(browse_button)

        scan_row = QHBoxLayout()
        scan_row.addWidget(self._scan_button)
        scan_row.addWidget(self._abort_button)
        scan_row.addWidget(self._progress_bar, stretch=1)

        bottom_row = QHBoxLayout()
        bottom_row.addWidget(self._delete_button)
        bottom_row.addStretch(1)
        bottom_row.addWidget(close_button)

        layout = QVBoxLayout(self)
        layout.addLayout(directory_row)
        layout.addLayout(scan_row)
        layout.addWidget(self._status_label)
        layout.addWidget(self._result_tree, stretch=1)
        layout.addLayout(bottom_row)

    def _on_browse_clicked(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self, "Verzeichnis für Duplikatsuche wählen", self._directory_edit.text()
        )
        if chosen:
            self._directory_edit.setText(chosen)

    def _on_scan_clicked(self) -> None:
        root = Path(self._directory_edit.text()).expanduser()
        if not root.is_dir():
            QMessageBox.warning(self, "Ungültiges Verzeichnis", f"'{root}' ist kein gültiges Verzeichnis.")
            return

        self._result_tree.clear()
        self._groups = []
        self._delete_button.setEnabled(False)
        self._scan_button.setEnabled(False)
        self._abort_button.setEnabled(True)
        self._progress_bar.setRange(0, 0)
        self._status_label.setText(f"Durchsuche '{root}' …")

        self._worker = _DuplicateScanWorker(root)
        self._worker.progress_changed.connect(self._on_progress_changed)
        self._worker.finished_with_result.connect(self._on_scan_finished)
        self._worker.failed.connect(self._on_scan_failed)
        self._worker.start()

    def _on_abort_clicked(self) -> None:
        if self._worker is not None:
            self._worker.request_abort()
            self._status_label.setText("Abbruch angefordert …")

    def _on_progress_changed(self, current: int, total: int, file_name: str) -> None:
        if total > 0:
            self._progress_bar.setRange(0, total)
            self._progress_bar.setValue(current)
        self._status_label.setText(f"Hashe Kandidaten ({current}/{total}): {file_name}")

    def _on_scan_finished(self, groups: list[_DuplicateGroup]) -> None:
        self._groups = groups
        self._scan_button.setEnabled(True)
        self._abort_button.setEnabled(False)
        self._progress_bar.setRange(0, 1)
        self._progress_bar.setValue(1)
        self._populate_result_tree()

        total_wasted = sum(group.size_bytes * (len(group.paths) - 1) for group in groups)
        if groups:
            self._status_label.setText(
                f"{len(groups)} Duplikat-Gruppe(n) gefunden – "
                f"{format_size(total_wasted)} durch Duplikate belegt."
            )
        else:
            self._status_label.setText("Keine Duplikate gefunden.")
        self._delete_button.setEnabled(bool(groups))

    def _on_scan_failed(self, message: str) -> None:
        self._scan_button.setEnabled(True)
        self._abort_button.setEnabled(False)
        self._progress_bar.setRange(0, 1)
        self._progress_bar.setValue(0)
        self._status_label.setText("Fehler beim Scan.")
        QMessageBox.critical(self, "Fehler bei der Duplikatsuche", message)

    def _populate_result_tree(self) -> None:
        self._result_tree.clear()
        for group in self._groups:
            group_item = QTreeWidgetItem(
                [
                    f"{len(group.paths)} identische Dateien ({group.file_hash[:12]}…)",
                    format_size(group.size_bytes),
                ]
            )
            group_item.setFlags(group_item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            self._result_tree.addTopLevelItem(group_item)

            for index, path in enumerate(group.paths):
                file_item = QTreeWidgetItem([str(path), format_size(group.size_bytes)])
                file_item.setFlags(
                    file_item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled
                )
                # Erste Datei je Gruppe gilt als "behalten" (unchecked),
                # alle weiteren sind standardmäßig zum Löschen markiert.
                file_item.setCheckState(
                    _COLUMN_PATH,
                    Qt.CheckState.Unchecked if index == 0 else Qt.CheckState.Checked,
                )
                file_item.setData(_COLUMN_PATH, Qt.ItemDataRole.UserRole, str(path))
                group_item.addChild(file_item)

            group_item.setExpanded(True)

    def _on_delete_clicked(self) -> None:
        paths_to_delete: list[Path] = []
        for group_index in range(self._result_tree.topLevelItemCount()):
            group_item = self._result_tree.topLevelItem(group_index)
            for child_index in range(group_item.childCount()):
                file_item = group_item.child(child_index)
                if file_item.checkState(_COLUMN_PATH) == Qt.CheckState.Checked:
                    stored_path = file_item.data(_COLUMN_PATH, Qt.ItemDataRole.UserRole)
                    paths_to_delete.append(Path(stored_path))

        if not paths_to_delete:
            QMessageBox.information(self, "Keine Auswahl", "Es sind keine Dateien zum Löschen markiert.")
            return

        preview = "\n".join(str(path) for path in paths_to_delete[:15])
        if len(paths_to_delete) > 15:
            preview += f"\n… und {len(paths_to_delete) - 15} weitere."

        confirmed = QMessageBox.question(
            self,
            "Löschen bestätigen",
            f"{len(paths_to_delete)} Datei(en) unwiderruflich löschen?\n\n{preview}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirmed != QMessageBox.StandardButton.Yes:
            return

        failed_paths: list[tuple[Path, str]] = []
        deleted_count = 0
        for path in paths_to_delete:
            try:
                path.unlink()
                deleted_count += 1
            except OSError as error:
                failed_paths.append((path, str(error)))

        self._status_label.setText(f"{deleted_count} Datei(en) gelöscht.")
        if failed_paths:
            error_text = "\n".join(f"{path}: {message}" for path, message in failed_paths)
            QMessageBox.warning(
                self, "Einige Dateien konnten nicht gelöscht werden", error_text
            )

        # Ergebnis-Baum neu aufbauen, damit gelöschte Einträge verschwinden.
        for group in self._groups:
            group.paths = [path for path in group.paths if path not in paths_to_delete]
        self._groups = [group for group in self._groups if len(group.paths) > 1]
        self._populate_result_tree()
        self._delete_button.setEnabled(bool(self._groups))


class DuplicateFinderPlugin(PandoraPlugin):
    """Plugin, das eine rekursive Duplikatsuche mit Lösch-Dialog bereitstellt."""

    name = "Duplikat-Finder"
    version = "1.0"
    author = "AKI_SystemDown®"
    description = (
        "Durchsucht ein wählbares Verzeichnis rekursiv nach inhaltlich identischen "
        "Dateien (SHA-256) und ermöglicht das gezielte Löschen überzähliger Kopien "
        "über einen eigenen Dialog."
    )

    def __init__(self) -> None:
        self._context: dict[str, Any] = {}
        self._open_dialogs: list[DuplicateFinderDialog] = []

    def on_load(self, context: dict[str, Any]) -> None:
        self._context = context
        logger.info("%s geladen.", self.name)

    def on_unload(self) -> None:
        for dialog in self._open_dialogs:
            dialog.close()
        self._open_dialogs.clear()

    def register_menu_actions(self, context: dict[str, Any]) -> list[QAction]:
        main_window = context.get("main_window")
        action = QAction("Duplikate suchen …", main_window)
        action.triggered.connect(self._open_dialog)
        return [action]

    def _open_dialog(self) -> None:
        start_directory = self._determine_start_directory()
        main_window = self._context.get("main_window")
        dialog = DuplicateFinderDialog(start_directory, parent=main_window)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.destroyed.connect(lambda: self._open_dialogs.remove(dialog) if dialog in self._open_dialogs else None)
        self._open_dialogs.append(dialog)
        dialog.show()

    def _determine_start_directory(self) -> Path:
        left_panel = self._context.get("left_panel")
        current_directory = getattr(left_panel, "current_directory", None)
        if isinstance(current_directory, Path):
            return current_directory
        return Path.home()
