"""Pandora® Commander – Qt-Tabellenmodell für ein Dateimanager-Panel.

Bindet app.core.filesystem.file_model (reines Python) an Qt an, indem
es ein QAbstractTableModel bereitstellt, das von einer QTableView
(oder QTreeView im Tabellenmodus) direkt verwendet werden kann.

Jede Seite des zweispaltigen Dateimanagers (links/rechts) erhält
später eine eigene Instanz dieses Modells (siehe app/ui/widgets/
file_panel.py in einer der nächsten Dateien).
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt, pyqtSignal
from PyQt6.QtGui import QColor

from app.core.filesystem.file_model import EntryType, FileEntry, format_size, scan_directory
from app.core.filesystem.file_tags import LABEL_COLORS, TagsManager
from app.core.logging_setup import get_logger
from app.utils.thumbnail_provider import ThumbnailProvider

logger = get_logger(__name__)

# Spaltenindizes als benannte Konstanten, damit sie an anderer Stelle
# (z. B. beim Verbinden von Spaltenbreiten oder beim Sortieren) nicht
# als "magische Zahlen" auftauchen.
COLUMN_NAME: int = 0
COLUMN_SIZE: int = 1
COLUMN_MODIFIED: int = 2
COLUMN_TYPE: int = 3
COLUMN_TAGS: int = 4

_COLUMN_HEADERS: tuple[str, ...] = ("Name", "Größe", "Geändert", "Typ", "Tags")

#: Alphakanal, mit dem eine Farbmarkierung als dezente Zeilenfärbung
#: hinterlegt wird, damit der Zellentext weiterhin gut lesbar bleibt.
_ROW_TINT_ALPHA: int = 55

_TYPE_LABELS: dict[str, str] = {
    EntryType.DIRECTORY: "Ordner",
    EntryType.FILE: "Datei",
    EntryType.SYMLINK: "Verknüpfung",
    EntryType.PARENT: "Übergeordnet",
}


class FilePanelModel(QAbstractTableModel):
    """Tabellenmodell für ein einzelnes Dateimanager-Panel.

    Kapselt den aktuellen Pfad, die eingelesenen FileEntry-Objekte und
    stellt die für QAbstractTableModel nötigen Methoden bereit
    (rowCount, columnCount, data, headerData). Zusätzlich bietet es
    Komfortmethoden wie set_directory(), entry_at() und sort(), die
    von der Panel-Ansicht direkt genutzt werden.

    Signals:
        directory_changed: Wird nach erfolgreichem Verzeichniswechsel
            mit dem neuen Pfad gesendet.
        error_occurred: Wird gesendet, wenn ein Verzeichnis nicht
            gelesen werden konnte, mit einer für Nutzer verständlichen
            Fehlermeldung als Argument.
    """

    directory_changed = pyqtSignal(Path)
    error_occurred = pyqtSignal(str)

    def __init__(
        self,
        initial_directory: Path | None = None,
        show_hidden: bool = False,
        tags_manager: TagsManager | None = None,
        thumbnail_provider: ThumbnailProvider | None = None,
        parent: object = None,
    ) -> None:
        super().__init__(parent)
        self._entries: list[FileEntry] = []
        self._current_directory: Path = initial_directory or Path.home()
        self._show_hidden: bool = show_hidden
        self._sort_column: int = COLUMN_NAME
        self._sort_ascending: bool = True
        self._tags_manager: TagsManager = tags_manager or TagsManager()
        self._thumbnail_provider: ThumbnailProvider = thumbnail_provider or ThumbnailProvider()
        self._thumbnail_provider.thumbnail_ready.connect(self._on_thumbnail_ready)

        self.set_directory(self._current_directory)

    # ------------------------------------------------------------------
    # QAbstractTableModel-Pflichtmethoden
    # ------------------------------------------------------------------

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        if parent.isValid():
            return 0
        return len(self._entries)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        if parent.isValid():
            return 0
        return len(_COLUMN_HEADERS)

    def data(
        self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole
    ) -> object:
        if not index.isValid():
            return None
        if not (0 <= index.row() < len(self._entries)):
            return None

        entry = self._entries[index.row()]
        column = index.column()

        if role == Qt.ItemDataRole.DisplayRole:
            return self._display_value(entry, column)

        if role == Qt.ItemDataRole.TextAlignmentRole and column == COLUMN_SIZE:
            return Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter

        if role == Qt.ItemDataRole.ToolTipRole and column == COLUMN_NAME:
            tags_info = self._tags_manager.get(entry.path)
            if tags_info.tags:
                return f"{entry.path}\nTags: {', '.join(tags_info.tags)}"
            return str(entry.path)

        if role == Qt.ItemDataRole.BackgroundRole and entry.entry_type != EntryType.PARENT:
            color_name = self._tags_manager.get(entry.path).color
            if color_name is not None:
                hex_color = LABEL_COLORS.get(color_name)
                if hex_color is not None:
                    color = QColor(hex_color)
                    color.setAlpha(_ROW_TINT_ALPHA)
                    return color

        if role == Qt.ItemDataRole.DecorationRole and column == COLUMN_NAME:
            icon = self._thumbnail_provider.icon_for(entry)
            if icon is not None:
                return icon

        return None

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> object:  # noqa: N802
        if orientation != Qt.Orientation.Horizontal:
            return None
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if not (0 <= section < len(_COLUMN_HEADERS)):
            return None
        return _COLUMN_HEADERS[section]

    # ------------------------------------------------------------------
    # Hilfsmethoden
    # ------------------------------------------------------------------

    def _display_value(self, entry: FileEntry, column: int) -> str:
        """Liefert den Anzeigetext einer Zelle für eine gegebene Spalte."""
        if column == COLUMN_NAME:
            return entry.name
        if column == COLUMN_SIZE:
            return entry.display_size
        if column == COLUMN_MODIFIED:
            if entry.entry_type == EntryType.PARENT:
                return ""
            return entry.display_modified
        if column == COLUMN_TYPE:
            if entry.entry_type == EntryType.FILE and entry.extension:
                return f"{entry.extension.upper()}-Datei"
            return _TYPE_LABELS.get(entry.entry_type, "")
        if column == COLUMN_TAGS:
            if entry.entry_type == EntryType.PARENT:
                return ""
            return ", ".join(self._tags_manager.get(entry.path).tags)
        return ""

    # ------------------------------------------------------------------
    # Öffentliche API für die Panel-Ansicht
    # ------------------------------------------------------------------

    @property
    def current_directory(self) -> Path:
        """Das aktuell angezeigte Verzeichnis."""
        return self._current_directory

    @property
    def show_hidden(self) -> bool:
        """Ob versteckte Dateien aktuell angezeigt werden."""
        return self._show_hidden

    def set_show_hidden(self, show_hidden: bool) -> None:
        """Schaltet die Anzeige versteckter Dateien um und lädt neu.

        Args:
            show_hidden: True, um versteckte Dateien anzuzeigen.
        """
        if show_hidden == self._show_hidden:
            return
        self._show_hidden = show_hidden
        self.refresh()

    def set_directory(self, directory: Path) -> bool:
        """Wechselt das angezeigte Verzeichnis und lädt dessen Inhalt.

        Bei Fehlern (fehlende Berechtigung, Pfad existiert nicht mehr)
        bleibt das Modell auf dem zuletzt gültigen Verzeichnis stehen
        und error_occurred wird gesendet, statt eine Exception nach
        außen dringen zu lassen.

        Args:
            directory: Das neue Zielverzeichnis.

        Returns:
            True bei Erfolg, False wenn ein Fehler auftrat.
        """
        try:
            new_entries = scan_directory(directory, show_hidden=self._show_hidden)
        except (FileNotFoundError, NotADirectoryError, PermissionError) as error:
            message = f"Verzeichnis konnte nicht geöffnet werden: {error}"
            logger.warning(message)
            self.error_occurred.emit(message)
            return False

        self.beginResetModel()
        self._entries = new_entries
        self._current_directory = directory
        self._apply_sort()
        self.endResetModel()

        self.directory_changed.emit(directory)
        return True

    def refresh(self) -> bool:
        """Lädt das aktuelle Verzeichnis erneut ein.

        Returns:
            True bei Erfolg, False bei Fehlern (siehe set_directory).
        """
        return self.set_directory(self._current_directory)

    def entry_at(self, row: int) -> FileEntry | None:
        """Liefert den FileEntry einer bestimmten Zeile.

        Args:
            row: Zeilenindex.

        Returns:
            Der FileEntry oder None, falls der Index ungültig ist.
        """
        if 0 <= row < len(self._entries):
            return self._entries[row]
        return None

    @property
    def tags_manager(self) -> TagsManager:
        """Die von diesem Modell verwendete Tag-/Farbmarkierungs-Verwaltung."""
        return self._tags_manager

    @property
    def thumbnail_provider(self) -> ThumbnailProvider:
        """Die von diesem Modell verwendete Miniaturansichten-Verwaltung."""
        return self._thumbnail_provider

    def _on_thumbnail_ready(self, path: Path) -> None:
        """Zeichnet genau die Zeile neu, deren Miniaturansicht fertig geladen wurde."""
        for row, entry in enumerate(self._entries):
            if entry.path == path:
                index = self.index(row, COLUMN_NAME)
                self.dataChanged.emit(index, index, [Qt.ItemDataRole.DecorationRole])
                break

    def notify_visual_change(self) -> None:
        """Löst ein Neuzeichnen aller Zellen aus, ohne das Verzeichnis neu einzulesen.

        Wird nach Änderungen an Tags/Farbmarkierungen aufgerufen
        (Anzeige/Tooltip/Hintergrundfarbe hängen vom TagsManager ab,
        der außerhalb dieses Modells verändert werden kann).
        """
        if not self._entries:
            return
        top_left = self.index(0, 0)
        bottom_right = self.index(len(self._entries) - 1, self.columnCount() - 1)
        self.dataChanged.emit(
            top_left,
            bottom_right,
            [
                Qt.ItemDataRole.DisplayRole,
                Qt.ItemDataRole.BackgroundRole,
                Qt.ItemDataRole.ToolTipRole,
            ],
        )

    def sort(self, column: int, order: Qt.SortOrder = Qt.SortOrder.AscendingOrder) -> None:  # noqa: N802
        """Sortiert das Modell nach einer Spalte (Qt-Standard-Hook).

        Wird von QTableView automatisch aufgerufen, wenn der Nutzer
        auf einen Spaltenkopf klickt (bei aktiviertem Sorting).
        Die ".."-Zeile bleibt dabei immer an erster Stelle.

        Args:
            column: Zu sortierende Spalte (siehe COLUMN_*-Konstanten).
            order: Auf- oder absteigende Sortierung.
        """
        self._sort_column = column
        self._sort_ascending = order == Qt.SortOrder.AscendingOrder

        self.layoutAboutToBeChanged.emit()
        self._apply_sort()
        self.layoutChanged.emit()

    def _apply_sort(self) -> None:
        """Wendet die aktuell konfigurierte Sortierung auf _entries an."""
        key_func = self._sort_key_for_column(self._sort_column)

        parent_entries = [e for e in self._entries if e.entry_type == EntryType.PARENT]
        directory_entries = [e for e in self._entries if e.is_directory and e.entry_type != EntryType.PARENT]
        file_entries = [e for e in self._entries if not e.is_directory]

        directory_entries.sort(key=key_func, reverse=not self._sort_ascending)
        file_entries.sort(key=key_func, reverse=not self._sort_ascending)

        self._entries = parent_entries + directory_entries + file_entries

    def _sort_key_for_column(self, column: int):
        """Liefert die Sortier-Schlüsselfunktion für eine Spalte."""
        if column == COLUMN_SIZE:
            return lambda e: e.size_bytes
        if column == COLUMN_MODIFIED:
            return lambda e: e.modified
        if column == COLUMN_TYPE:
            return lambda e: (e.extension, e.name.lower())
        if column == COLUMN_TAGS:
            return lambda e: ", ".join(self._tags_manager.get(e.path).tags).lower()
        return lambda e: e.name.lower()
