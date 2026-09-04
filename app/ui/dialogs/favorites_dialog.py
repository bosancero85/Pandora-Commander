"""Pandora® Commander – Favoritendialog.

Zeigt alle Favoritengruppen mit ihren Einträgen als Baumansicht,
erlaubt das Hinzufügen/Entfernen von Gruppen und Favoriten sowie den
Export/Import als JSON-Datei. Ein Doppelklick auf einen Favoriten
schließt den Dialog und meldet den gewählten Pfad.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from app.core.filesystem.favorites import FavoritesManager
from app.core.logging_setup import get_logger

logger = get_logger(__name__)

_GROUP_ROLE = Qt.ItemDataRole.UserRole
_PATH_ROLE = Qt.ItemDataRole.UserRole + 1


class FavoritesDialog(QDialog):
    """Dialog zur Verwaltung von Favoritenordnern.

    Signals:
        path_activated: Wird gesendet, wenn ein Favorit doppelt
            angeklickt wird; übergibt den gewählten Path.

    Args:
        favorites_manager: Zentraler FavoritesManager der Anwendung.
        current_path: Aktuell im Panel angezeigter Pfad, als Vorschlag
            beim Hinzufügen eines neuen Favoriten.
        parent: Optionales Eltern-Widget.
    """

    path_activated = pyqtSignal(Path)

    def __init__(self, favorites_manager: FavoritesManager, current_path: Path, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Favoriten")
        self.resize(480, 460)
        self._manager = favorites_manager
        self._current_path = current_path

        self._tree = QTreeWidget(self)
        self._tree.setHeaderLabels(["Name", "Pfad"])
        self._tree.itemDoubleClicked.connect(self._on_item_double_clicked)

        add_group_button = QPushButton("Neue Gruppe", self)
        add_group_button.clicked.connect(self._add_group)
        add_favorite_button = QPushButton("Aktuellen Ordner hinzufügen", self)
        add_favorite_button.clicked.connect(self._add_current_folder)
        remove_button = QPushButton("Entfernen", self)
        remove_button.clicked.connect(self._remove_selected)
        export_button = QPushButton("Exportieren …", self)
        export_button.clicked.connect(self._export)
        import_button = QPushButton("Importieren …", self)
        import_button.clicked.connect(self._import)

        button_row = QHBoxLayout()
        for button in (add_group_button, add_favorite_button, remove_button, export_button, import_button):
            button_row.addWidget(button)

        layout = QVBoxLayout(self)
        layout.addWidget(self._tree, 1)
        layout.addLayout(button_row)

        self._reload_tree()

    def _reload_tree(self) -> None:
        self._tree.clear()
        for group in self._manager.groups:
            group_item = QTreeWidgetItem([group.name, ""])
            group_item.setData(0, _GROUP_ROLE, group.name)
            for entry in group.entries:
                entry_item = QTreeWidgetItem([entry.name, entry.path])
                entry_item.setData(0, _GROUP_ROLE, group.name)
                entry_item.setData(0, _PATH_ROLE, entry.path)
                group_item.addChild(entry_item)
            self._tree.addTopLevelItem(group_item)
        self._tree.expandAll()

    def _add_group(self) -> None:
        name, confirmed = QInputDialog.getText(self, "Neue Gruppe", "Name der Gruppe:")
        if confirmed and name.strip():
            self._manager.add_group(name.strip())
            self._reload_tree()

    def _add_current_folder(self) -> None:
        selected_items = self._tree.selectedItems()
        group_name = "Allgemein"
        if selected_items:
            group_name = selected_items[0].data(0, _GROUP_ROLE) or group_name

        name, confirmed = QInputDialog.getText(
            self, "Favorit hinzufügen", "Anzeigename:", text=self._current_path.name
        )
        if confirmed and name.strip():
            self._manager.add_favorite(group_name, name.strip(), self._current_path)
            self._reload_tree()

    def _remove_selected(self) -> None:
        selected_items = self._tree.selectedItems()
        if not selected_items:
            return
        item = selected_items[0]
        path = item.data(0, _PATH_ROLE)
        group_name = item.data(0, _GROUP_ROLE)

        if path:
            self._manager.remove_favorite(group_name, path)
        else:
            confirmation = QMessageBox.question(
                self, "Gruppe entfernen", f"Gruppe '{group_name}' inklusive aller Favoriten entfernen?"
            )
            if confirmation == QMessageBox.StandardButton.Yes:
                self._manager.remove_group(group_name)
        self._reload_tree()

    def _export(self) -> None:
        target, _ = QFileDialog.getSaveFileName(self, "Favoriten exportieren", "favoriten.json", "JSON (*.json)")
        if target:
            self._manager.export_to_file(Path(target))

    def _import(self) -> None:
        source, _ = QFileDialog.getOpenFileName(self, "Favoriten importieren", "", "JSON (*.json)")
        if source:
            self._manager.import_from_file(Path(source), merge=True)
            self._reload_tree()

    def _on_item_double_clicked(self, item: QTreeWidgetItem) -> None:
        path = item.data(0, _PATH_ROLE)
        if path:
            self.path_activated.emit(Path(path))
            self.accept()
