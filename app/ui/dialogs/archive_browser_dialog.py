"""Pandora® Commander – Archiv-Browser.

Erlaubt es, ein Archiv "wie einen Ordner" zu öffnen: Der Inhalt wird
als navigierbare Ordnerstruktur dargestellt (Doppelklick auf einen
Ordner-Eintrag steigt hinein, ".." steigt eine Ebene auf), ohne dass
das gesamte Archiv vorher entpackt werden muss – ``list_archive()``
liest nur das Inhaltsverzeichnis.

Einzelne Dateien lassen sich per Doppelklick in einem temporären
Verzeichnis extrahieren und direkt in der eingebauten Vorschau
(``PreviewWidget``) ansehen. Eine Auswahl von Dateien/Ordnern kann
gezielt in ein reales Zielverzeichnis entpackt werden.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.core.archive.archive_handler import (
    ArchiveEntryInfo,
    ArchiveError,
    extract_archive,
    list_archive,
)
from app.core.filesystem.file_model import format_size
from app.core.logging_setup import get_logger
from app.themes.dark_theme import PALETTE
from app.ui.widgets.preview_widget import PreviewWidget

logger = get_logger(__name__)

_NAME_COLUMN = 0
_SIZE_COLUMN = 1
_TYPE_COLUMN = 2

_ROLE_ENTRY = Qt.ItemDataRole.UserRole
_ROLE_IS_DIR = Qt.ItemDataRole.UserRole + 1
_ROLE_CHILD_NAME = Qt.ItemDataRole.UserRole + 2


class ArchiveBrowserDialog(QDialog):
    """Zeigt den Inhalt eines Archivs als navigierbaren Ordnerbaum.

    Args:
        archive_path: Pfad zur Archivdatei (ZIP/TAR/TAR.GZ/TAR.BZ2/7Z).
        parent: Optionales Eltern-Widget.
    """

    def __init__(self, archive_path: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._archive_path = archive_path
        self._current_prefix = ""  # "" = Archiv-Wurzel, sonst z. B. "sub/ordner"
        self._temp_dir = Path(tempfile.mkdtemp(prefix="pandora_archive_"))

        self.setWindowTitle(f"Archiv – {archive_path.name}")
        self.resize(880, 560)

        try:
            self._entries: list[ArchiveEntryInfo] = list_archive(archive_path)
        except ArchiveError as error:
            QMessageBox.critical(self, "Archiv öffnen", str(error))
            self._entries = []

        self._breadcrumb_label = QLabel(self)
        self._up_button = QPushButton("⬆ Nach oben", self)
        self._up_button.clicked.connect(self._navigate_up)

        self._table = QTableWidget(self)
        self._table.setColumnCount(3)
        self._table.setHorizontalHeaderLabels(["Name", "Größe", "Typ"])
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.cellDoubleClicked.connect(self._on_row_activated)
        self._table.itemSelectionChanged.connect(self._on_selection_changed)

        self._preview = PreviewWidget(self)

        self._extract_selected_button = QPushButton("Auswahl entpacken …", self)
        self._extract_selected_button.clicked.connect(self._extract_selected)
        self._extract_all_button = QPushButton("Alles entpacken …", self)
        self._extract_all_button.clicked.connect(self._extract_all)

        self._status_label = QLabel(f"{len(self._entries)} Einträge im Archiv.", self)

        self._button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        self._button_box.rejected.connect(self.reject)
        self._button_box.accepted.connect(self.accept)
        self._button_box.button(QDialogButtonBox.StandardButton.Close).clicked.connect(
            self.accept
        )

        self._build_layout()
        self._refresh_table()

    # ------------------------------------------------------------------
    # Aufbau
    # ------------------------------------------------------------------

    def _build_layout(self) -> None:
        breadcrumb_row = QHBoxLayout()
        breadcrumb_row.addWidget(self._up_button)
        breadcrumb_row.addWidget(self._breadcrumb_label, 1)

        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.addWidget(self._table)
        splitter.addWidget(self._preview)
        splitter.setSizes([520, 360])

        extract_row = QHBoxLayout()
        extract_row.addWidget(self._extract_selected_button)
        extract_row.addWidget(self._extract_all_button)
        extract_row.addStretch(1)

        layout = QVBoxLayout(self)
        layout.addLayout(breadcrumb_row)
        layout.addWidget(splitter, 1)
        layout.addLayout(extract_row)
        layout.addWidget(self._status_label)
        layout.addWidget(self._button_box)

        self.setStyleSheet(
            f"QDialog {{ background-color: {PALETTE.surface}; color: {PALETTE.text_primary}; }}"
            f"QLabel {{ color: {PALETTE.text_primary}; }}"
        )

    # ------------------------------------------------------------------
    # Virtuelle Navigation innerhalb des Archivs
    # ------------------------------------------------------------------

    def _immediate_children(
        self, prefix: str
    ) -> tuple[list[str], list[tuple[str, ArchiveEntryInfo]]]:
        """Ermittelt Unterordner und Dateien direkt unterhalb von ``prefix``.

        Args:
            prefix: Virtueller Pfad innerhalb des Archivs ("" = Wurzel).

        Returns:
            Tupel aus (sortierte Ordnernamen, Liste von (Dateiname, Eintrag)).
        """
        child_dirs: set[str] = set()
        child_files: list[tuple[str, ArchiveEntryInfo]] = []

        for entry in self._entries:
            name = entry.name.replace("\\", "/").strip("/")
            if prefix:
                if not name.startswith(prefix + "/"):
                    continue
                remainder = name[len(prefix) + 1 :]
            else:
                remainder = name

            if not remainder:
                continue

            if "/" in remainder:
                child_dirs.add(remainder.split("/", 1)[0])
            elif entry.is_dir:
                child_dirs.add(remainder)
            else:
                child_files.append((remainder, entry))

        return sorted(child_dirs), sorted(child_files, key=lambda item: item[0].lower())

    def _refresh_table(self) -> None:
        self._breadcrumb_label.setText(f"/{self._current_prefix}" if self._current_prefix else "/")
        self._up_button.setEnabled(bool(self._current_prefix))

        folders, files = self._immediate_children(self._current_prefix)

        self._table.setRowCount(0)
        self._table.setRowCount(len(folders) + len(files))

        row = 0
        for folder_name in folders:
            self._set_row(row, folder_name, size_text="—", type_text="Ordner", is_dir=True)
            row += 1
        for file_name, entry in files:
            self._set_row(
                row,
                file_name,
                size_text=format_size(entry.size_bytes),
                type_text="Datei",
                is_dir=False,
                entry=entry,
            )
            row += 1

        self._status_label.setText(
            f"{len(folders)} Ordner, {len(files)} Datei(en) auf dieser Ebene "
            f"({len(self._entries)} Einträge insgesamt im Archiv)."
        )

    def _set_row(
        self,
        row: int,
        name: str,
        size_text: str,
        type_text: str,
        is_dir: bool,
        entry: ArchiveEntryInfo | None = None,
    ) -> None:
        name_item = QTableWidgetItem(("📁 " if is_dir else "📄 ") + name)
        name_item.setData(_ROLE_IS_DIR, is_dir)
        name_item.setData(_ROLE_CHILD_NAME, name)
        name_item.setData(_ROLE_ENTRY, entry)
        self._table.setItem(row, _NAME_COLUMN, name_item)
        self._table.setItem(row, _SIZE_COLUMN, QTableWidgetItem(size_text))
        self._table.setItem(row, _TYPE_COLUMN, QTableWidgetItem(type_text))

    def _navigate_up(self) -> None:
        if not self._current_prefix:
            return
        parts = self._current_prefix.split("/")
        self._current_prefix = "/".join(parts[:-1])
        self._refresh_table()

    def _on_row_activated(self, row: int, _column: int) -> None:
        name_item = self._table.item(row, _NAME_COLUMN)
        if name_item is None:
            return

        if name_item.data(_ROLE_IS_DIR):
            child_name = name_item.data(_ROLE_CHILD_NAME)
            self._current_prefix = (
                f"{self._current_prefix}/{child_name}" if self._current_prefix else child_name
            )
            self._refresh_table()
            return

        entry: ArchiveEntryInfo | None = name_item.data(_ROLE_ENTRY)
        if entry is not None:
            self._extract_and_preview(entry)

    def _on_selection_changed(self) -> None:
        selected_rows = {index.row() for index in self._table.selectedIndexes()}
        if len(selected_rows) == 1:
            row = next(iter(selected_rows))
            name_item = self._table.item(row, _NAME_COLUMN)
            entry: ArchiveEntryInfo | None = (
                name_item.data(_ROLE_ENTRY) if name_item is not None else None
            )
            if entry is not None:
                self._extract_and_preview(entry, silent=True)

    # ------------------------------------------------------------------
    # Extraktion (temporär für Vorschau, dauerhaft auf Wunsch)
    # ------------------------------------------------------------------

    def _extract_and_preview(self, entry: ArchiveEntryInfo, silent: bool = False) -> None:
        try:
            extract_archive(self._archive_path, self._temp_dir, members=[entry.name])
            extracted_path = self._temp_dir / entry.name
            if extracted_path.exists():
                self._preview.show_path(extracted_path)
            elif not silent:
                QMessageBox.information(
                    self, "Vorschau", "Für diesen Eintrag ist keine Vorschau möglich."
                )
        except ArchiveError as error:
            if not silent:
                QMessageBox.warning(self, "Vorschau", f"Extraktion fehlgeschlagen:\n{error}")

    def _selected_member_names(self) -> list[str]:
        names: list[str] = []
        seen_rows: set[int] = set()
        for index in self._table.selectedIndexes():
            if index.row() in seen_rows:
                continue
            seen_rows.add(index.row())
            name_item = self._table.item(index.row(), _NAME_COLUMN)
            if name_item is None:
                continue
            child_name = name_item.data(_ROLE_CHILD_NAME)
            full_name = f"{self._current_prefix}/{child_name}" if self._current_prefix else child_name
            if name_item.data(_ROLE_IS_DIR):
                prefix = full_name + "/"
                names.extend(
                    entry.name
                    for entry in self._entries
                    if entry.name.replace("\\", "/").startswith(prefix)
                )
            else:
                names.append(full_name)
        return names

    def _extract_selected(self) -> None:
        members = self._selected_member_names()
        if not members:
            QMessageBox.information(self, "Entpacken", "Bitte zuerst eine Auswahl treffen.")
            return
        destination = QFileDialog.getExistingDirectory(self, "Zielordner wählen")
        if not destination:
            return
        try:
            extract_archive(self._archive_path, Path(destination), members=members)
        except ArchiveError as error:
            QMessageBox.critical(self, "Entpacken", str(error))
            return
        self._status_label.setText(f'{len(members)} Eintrag/Einträge nach "{destination}" entpackt.')

    def _extract_all(self) -> None:
        destination = QFileDialog.getExistingDirectory(self, "Zielordner wählen")
        if not destination:
            return
        try:
            extract_archive(self._archive_path, Path(destination))
        except ArchiveError as error:
            QMessageBox.critical(self, "Entpacken", str(error))
            return
        self._status_label.setText(f'Gesamtes Archiv nach "{destination}" entpackt.')

    # ------------------------------------------------------------------
    # Aufräumen
    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:  # noqa: D102 - Qt-Override
        import shutil

        shutil.rmtree(self._temp_dir, ignore_errors=True)
        super().closeEvent(event)
