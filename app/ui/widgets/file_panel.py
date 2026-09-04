"""Pandora® Commander – Panel-Widget (eine Seite des Dateimanagers).

Kombiniert eine Pfad-/Breadcrumb-Leiste, eine QTableView (gebunden an
FilePanelModel) und eine Statuszeile (Anzahl Ordner/Dateien, Größe)
zu einem eigenständigen Widget. Zwei Instanzen dieses Widgets bilden
später die linke und rechte Seite des Hauptfensters.
"""

from __future__ import annotations

import os
from pathlib import Path

from PyQt6.QtCore import QMimeData, QPoint, QSize, QUrl, Qt, pyqtSignal
from PyQt6.QtGui import QDrag, QDragEnterEvent, QDragMoveEvent, QDropEvent, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QLabel,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from app.core.filesystem.file_model import EntryType, FileEntry, format_size
from app.core.filesystem.file_tags import TagsManager
from app.core.logging_setup import get_logger
from app.ui.widgets.breadcrumb_bar import BreadcrumbBar
from app.ui.widgets.file_panel_model import COLUMN_NAME, FilePanelModel
from app.utils.thumbnail_provider import ThumbnailProvider

logger = get_logger(__name__)

#: Kantenlänge der in der Panel-Tabelle angezeigten Bild-Miniaturansichten.
_THUMBNAIL_DISPLAY_SIZE = 24


def _same_filesystem(path_a: Path, path_b: Path) -> bool:
    """Prüft, ob zwei Pfade auf demselben Dateisystem (Gerät) liegen.

    Wird für die Standard-Aktion beim Ziehen&Ablegen benötigt: Beim
    Verschieben innerhalb desselben Dateisystems ist ein einfaches
    Umbenennen möglich (schnell), über Dateisystemgrenzen hinweg ist
    dagegen grundsätzlich ein Kopieren+Löschen nötig – Total Commander
    & Co. wählen deshalb standardmäßig "Verschieben" innerhalb und
    "Kopieren" über Geräte hinweg.

    Args:
        path_a: Erster Pfad (muss existieren).
        path_b: Zweiter Pfad (muss existieren).

    Returns:
        True, wenn beide Pfade auf demselben Gerät liegen; im
        Zweifel (z. B. bei Zugriffsfehlern) False.
    """
    try:
        return os.stat(path_a).st_dev == os.stat(path_b).st_dev
    except OSError:
        return False


class _FilePanelTableView(QTableView):
    """QTableView mit Unterstützung für Drag&Drop zwischen Panels/OS.

    Zieht der Nutzer markierte Zeilen heraus, werden sie als
    ``text/uri-list`` (Standard-Dateien-MIME-Typ) angeboten, sodass
    sowohl das jeweils andere Panel als auch externe Programme
    (Explorer, Nautilus, Dolphin, …) sie annehmen können. Beim
    Ablegen – egal ob aus dem anderen Panel oder von außen – wird
    ``drop_requested`` mit den Quellpfaden, dem Zielverzeichnis und
    der gewünschten Aktion (Verschieben ja/nein) gesendet; das
    Hauptfenster führt die eigentliche Operation aus.

    Aktionswahl beim Ablegen:
        * Ohne Zusatztaste: Verschieben innerhalb desselben
          Dateisystems, sonst Kopieren (Explorer/Nautilus-Konvention).
        * Strg gedrückt: erzwingt Kopieren.
        * Umschalt gedrückt: erzwingt Verschieben.
    """

    drop_requested = pyqtSignal(list, Path, bool)  # sources, destination, move

    def __init__(self, panel: "FilePanel", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._panel = panel
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.CopyAction)

    # ------------------------------------------------------------------
    # Ziehen (Ausgangspunkt: dieses Panel)
    # ------------------------------------------------------------------

    def startDrag(self, supportedActions: Qt.DropAction) -> None:  # noqa: N802
        """Startet den Drag-Vorgang mit den markierten Dateien als URLs."""
        paths = self._panel.selected_paths()
        if not paths:
            return

        mime_data = QMimeData()
        mime_data.setUrls([QUrl.fromLocalFile(str(path)) for path in paths])

        drag = QDrag(self)
        drag.setMimeData(mime_data)
        drag.exec(Qt.DropAction.CopyAction | Qt.DropAction.MoveAction)

    # ------------------------------------------------------------------
    # Ablegen (Ziel: dieses Panel)
    # ------------------------------------------------------------------

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        mime_data = event.mimeData()
        if not mime_data.hasUrls():
            event.ignore()
            return

        sources = [
            Path(url.toLocalFile())
            for url in mime_data.urls()
            if url.isLocalFile() and url.toLocalFile()
        ]
        if not sources:
            event.ignore()
            return

        destination = self._resolve_drop_target(event.position().toPoint())

        # Ablegen auf sich selbst (Quelle == Ziel) ignorieren.
        sources = [source for source in sources if source.parent != destination]
        if not sources:
            event.ignore()
            return

        modifiers = event.modifiers()
        if modifiers & Qt.KeyboardModifier.ControlModifier:
            move = False
        elif modifiers & Qt.KeyboardModifier.ShiftModifier:
            move = True
        else:
            move = _same_filesystem(sources[0], destination)

        event.acceptProposedAction()
        self.drop_requested.emit(sources, destination, move)

    def _resolve_drop_target(self, local_pos: QPoint) -> Path:
        """Ermittelt das Zielverzeichnis für eine Ablageposition.

        Wird über einem Ordnereintrag abgelegt, ist dieser Ordner das
        Ziel; ansonsten (leere Fläche, Datei-Zeile, ".."-Zeile) das
        aktuell angezeigte Verzeichnis dieses Panels.

        Args:
            local_pos: Ablageposition relativ zum Viewport.

        Returns:
            Absoluter Zielpfad für die Dateioperation.
        """
        index = self.indexAt(local_pos)
        if index.isValid():
            entry = self._panel.model.entry_at(index.row())
            if entry is not None and entry.is_directory and entry.entry_type != EntryType.PARENT:
                return entry.path
        return self._panel.current_directory


class FilePanel(QWidget):
    """Eine Seite (links oder rechts) des zweispaltigen Dateimanagers.

    Navigation:
        * Doppelklick auf einen Ordner (oder "..") wechselt hinein.
        * Doppelklick auf eine Datei sendet file_activated (z. B. zum
          Öffnen im integrierten Editor).
        * Enter in der Pfadleiste springt direkt zum eingegebenen Pfad.
        * navigate_to() kann auch programmatisch aufgerufen werden
          (z. B. von Favoriten oder der Breadcrumb-Leiste späterer
          Dateien).

    Signals:
        path_activated: Wird gesendet, wenn der Nutzer erfolgreich in
            ein Verzeichnis navigiert ist (neuer Pfad als Argument).
            Wird u. a. für den Verlauf (Vor/Zurück) benötigt.
        file_activated: Wird gesendet, wenn per Doppelklick eine
            Datei (kein Ordner) geöffnet werden soll.
        status_message: Textmeldungen, die von der Statusleiste des
            Hauptfensters angezeigt werden können (z. B. Fehler).
        context_menu_requested: Wird bei Rechtsklick auf die Tabelle
            gesendet, mit der globalen Bildschirmposition als Argument.
            Das Hauptfenster baut daraus das eigentliche Kontextmenü,
            da es die dafür nötigen QAction-Objekte (Kopieren,
            Verschieben, Löschen, Einfügen, …) zentral verwaltet.
        drop_requested: Wird gesendet, wenn per Drag&Drop Dateien auf
            diesem Panel abgelegt wurden (Quelle: dieses oder das
            andere Panel, oder ein externes Programm). Argumente:
            Liste der Quellpfade, Zielverzeichnis, Verschieben-Flag.
    """

    path_activated = pyqtSignal(Path)
    file_activated = pyqtSignal(Path)
    status_message = pyqtSignal(str)
    context_menu_requested = pyqtSignal(QPoint)
    drop_requested = pyqtSignal(list, Path, bool)

    def __init__(
        self,
        initial_directory: Path | None = None,
        tags_manager: TagsManager | None = None,
        thumbnail_provider: ThumbnailProvider | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        self._model = FilePanelModel(
            initial_directory=initial_directory,
            tags_manager=tags_manager,
            thumbnail_provider=thumbnail_provider,
        )
        self._model.directory_changed.connect(self._on_directory_changed)
        self._model.error_occurred.connect(self._on_model_error)

        self._breadcrumb_bar = BreadcrumbBar()
        self._table_view = _FilePanelTableView(self)
        self._table_view.drop_requested.connect(self.drop_requested)
        self._status_label = QLabel()

        self._setup_ui()
        self._breadcrumb_bar.set_path(self._model.current_directory)
        self._update_status_label()

    # ------------------------------------------------------------------
    # UI-Aufbau
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        """Baut das Layout: Breadcrumb-Leiste oben, Tabelle mittig, Status unten."""
        self._breadcrumb_bar.path_selected.connect(self._on_breadcrumb_path_selected)
        self._breadcrumb_bar.setFixedHeight(28)

        edit_shortcut = QShortcut(QKeySequence("Ctrl+L"), self)
        edit_shortcut.activated.connect(self._breadcrumb_bar.enter_edit_mode)

        self._table_view.setModel(self._model)
        self._table_view.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._table_view.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self._table_view.setAlternatingRowColors(True)
        self._table_view.setSortingEnabled(True)
        self._table_view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table_view.setIconSize(QSize(_THUMBNAIL_DISPLAY_SIZE, _THUMBNAIL_DISPLAY_SIZE))
        self._table_view.verticalHeader().setVisible(False)
        self._table_view.verticalHeader().setDefaultSectionSize(_THUMBNAIL_DISPLAY_SIZE + 8)
        self._table_view.horizontalHeader().setStretchLastSection(False)
        self._table_view.horizontalHeader().setSectionResizeMode(
            COLUMN_NAME, QHeaderView.ResizeMode.Stretch
        )
        self._table_view.doubleClicked.connect(self._on_row_double_clicked)
        self._table_view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table_view.customContextMenuRequested.connect(
            self._on_context_menu_requested
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)
        layout.addWidget(self._breadcrumb_bar)
        layout.addWidget(self._table_view)
        layout.addWidget(self._status_label)

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def navigate_to(self, path: Path) -> bool:
        """Wechselt programmatisch in ein Verzeichnis.

        Args:
            path: Zielverzeichnis.

        Returns:
            True bei Erfolg, False wenn der Wechsel fehlschlug (z. B.
            weil der Pfad nicht existiert oder nicht lesbar ist).
        """
        return self._model.set_directory(path)

    def refresh(self) -> None:
        """Lädt das aktuell angezeigte Verzeichnis neu ein."""
        self._model.refresh()

    def refresh_decorations(self) -> None:
        """Zeichnet Tag-/Farbmarkierungs-Anzeige neu, ohne neu einzulesen."""
        self._model.notify_visual_change()

    @property
    def tags_manager(self) -> TagsManager:
        """Die von diesem Panel verwendete Tag-/Farbmarkierungs-Verwaltung."""
        return self._model.tags_manager

    @property
    def thumbnail_provider(self) -> ThumbnailProvider:
        """Die von diesem Panel verwendete Miniaturansichten-Verwaltung."""
        return self._model.thumbnail_provider

    def selected_entries_count(self) -> int:
        """Anzahl aktuell in der Tabelle ausgewählter Zeilen.

        Returns:
            Anzahl ausgewählter (eindeutiger) Zeilen.
        """
        selection_model = self._table_view.selectionModel()
        if selection_model is None:
            return 0
        return len({index.row() for index in selection_model.selectedRows()})

    def selected_entries(self) -> list[FileEntry]:
        """Liefert die aktuell markierten Einträge dieses Panels.

        Die ".."-Zeile zum Aufsteigen ins Elternverzeichnis wird auch
        bei versehentlicher Mehrfachauswahl nie mit zurückgegeben, da
        sie kein sinnvolles Ziel für Kopieren/Verschieben/Löschen ist.

        Returns:
            Liste der markierten FileEntry-Objekte, in Anzeigereihen-
            folge. Leer, wenn nichts ausgewählt ist.
        """
        selection_model = self._table_view.selectionModel()
        if selection_model is None:
            return []

        rows = sorted({index.row() for index in selection_model.selectedRows()})
        entries: list[FileEntry] = []
        for row in rows:
            entry = self._model.entry_at(row)
            if entry is None or entry.entry_type == EntryType.PARENT:
                continue
            entries.append(entry)
        return entries

    def selected_paths(self) -> list[Path]:
        """Komfortmethode: Pfade der aktuell markierten Einträge.

        Returns:
            Liste der Pfade, in derselben Reihenfolge wie
            selected_entries().
        """
        return [entry.path for entry in self.selected_entries()]

    def clear_selection(self) -> None:
        """Hebt die aktuelle Markierung in der Tabelle auf.

        Wird u. a. nach erfolgreich abgeschlossenen Dateioperationen
        aufgerufen, damit nicht versehentlich bereits verschobene
        oder gelöschte Einträge "markiert" erscheinen, sobald das
        Panel neu geladen wurde.
        """
        selection_model = self._table_view.selectionModel()
        if selection_model is not None:
            selection_model.clearSelection()

    @property
    def current_directory(self) -> Path:
        """Das aktuell in diesem Panel angezeigte Verzeichnis."""
        return self._model.current_directory

    @property
    def model(self) -> FilePanelModel:
        """Zugriff auf das zugrundeliegende Tabellenmodell."""
        return self._model

    # ------------------------------------------------------------------
    # Interne Slots / Reaktionen
    # ------------------------------------------------------------------

    def _on_row_double_clicked(self, index) -> None:  # noqa: ANN001 - QModelIndex
        """Reagiert auf Doppelklick: navigiert in Ordner, aktiviert Dateien."""
        entry = self._model.entry_at(index.row())
        if entry is None:
            return
        if entry.is_directory:
            self.navigate_to(entry.path)
        elif entry.entry_type != EntryType.PARENT:
            self.file_activated.emit(entry.path)

    def _on_context_menu_requested(self, local_pos: QPoint) -> None:
        """Reagiert auf Rechtsklick in der Tabelle.

        Sorgt dafür, dass die rechtsgeklickte Zeile Teil der Auswahl
        ist (Rechtsklick auf eine nicht markierte Zeile ersetzt die
        Auswahl, analog zum Verhalten von Explorer/Nautilus/Dolphin),
        und meldet die globale Position weiter, damit das Hauptfenster
        dort sein Kontextmenü anzeigen kann.

        Args:
            local_pos: Klickposition relativ zum Viewport der Tabelle.
        """
        index = self._table_view.indexAt(local_pos)
        if index.isValid():
            selection_model = self._table_view.selectionModel()
            already_selected = selection_model is not None and index.row() in {
                selected.row() for selected in selection_model.selectedRows()
            }
            if not already_selected:
                self._table_view.selectRow(index.row())

        global_pos = self._table_view.viewport().mapToGlobal(local_pos)
        self.context_menu_requested.emit(global_pos)

    def _on_breadcrumb_path_selected(self, target: Path) -> None:
        """Reagiert auf Klick eines Breadcrumb-Segments oder bestätigte Texteingabe."""
        if not self.navigate_to(target):
            # Bei Fehlschlag (Pfad existiert nicht/nicht lesbar) die
            # Breadcrumb-Leiste auf den weiterhin gültigen, aktuellen
            # Pfad zurücksetzen statt den ungültigen Zielpfad stehen
            # zu lassen.
            self._breadcrumb_bar.set_path(self._model.current_directory)
            self.status_message.emit(f"Pfad nicht erreichbar: {target}")

    def _on_directory_changed(self, new_path: Path) -> None:
        """Aktualisiert Breadcrumb-Leiste/Statuszeile und meldet den Wechsel weiter."""
        self._breadcrumb_bar.set_path(new_path)
        self._update_status_label()
        self.path_activated.emit(new_path)

    def _on_model_error(self, message: str) -> None:
        """Leitet Modellfehler als Statusmeldung weiter."""
        self.status_message.emit(message)

    # ------------------------------------------------------------------
    # Anzeige-Helfer
    # ------------------------------------------------------------------

    def _update_status_label(self) -> None:
        """Berechnet und setzt den Text der Statuszeile.

        Zeigt die Anzahl Ordner, Anzahl Dateien und die
        Gesamtgröße aller sichtbaren Dateien (ohne Ordnergrößen,
        da diese standardmäßig nicht rekursiv berechnet werden).
        """
        self._status_label.setText(self.build_status_text())

    def build_status_text(self) -> str:
        """Baut den Statuszeilen-Text aus dem aktuellen Modellinhalt.

        Als eigenständige Methode (statt inline in _update_status_label)
        ausgelagert, damit die reine Text-Logik unabhängig von einem
        echten QLabel getestet werden kann.

        Returns:
            Formatierter Statustext, z. B. "3 Ordner, 12 Dateien, 4.20 MB".
        """
        folder_count = 0
        file_count = 0
        total_size = 0

        for row in range(self._model.rowCount()):
            entry = self._model.entry_at(row)
            if entry is None or entry.entry_type == EntryType.PARENT:
                continue
            if entry.is_directory:
                folder_count += 1
            else:
                file_count += 1
                total_size += entry.size_bytes

        return (
            f"{folder_count} Ordner, {file_count} Dateien, "
            f"{format_size(total_size)}"
        )
