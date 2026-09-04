"""Pandora® Commander – Plugin: Ordner-Tags/Favoriten-Farben.

Der eingebaute Favoriten-Mechanismus (siehe
app/core/filesystem/favorites.py) verwaltet benannte Favoriten in
Gruppen, kennt aber keine Farbzuordnung. Dieses Plugin ergänzt eine
eigenständige, farbige Ordner-Markierung ("Tag"), unabhängig davon,
ob ein Ordner zusätzlich als Favorit gespeichert ist:

    * Kontextmenü-Eintrag "Ordner-Tag setzen …" (bei markierten
      Ordnern) öffnet einen Dialog mit Farbauswahl und optionalem
      Freitext-Label (z. B. "🔴 Aktuelles Projekt", "🟢 Archiviert").
    * Beim Betreten eines getaggten Ordners erscheint unterhalb der
      Panel-Statuszeile ein farbiges Banner mit dem Label – so ist
      auf einen Blick erkennbar, dass man sich in einem markierten
      Ordner befindet.
    * Ein eigenes Einstellungen-Tab im Plugin-Manager-Dialog listet
      alle getaggten Ordner mit Farb-Vorschau und erlaubt das
      Entfernen einzelner Tags.

Die Zuordnung wird als eigene, vom Core unabhängige JSON-Datei unter
``~/.pandora_commander/plugins/folder_tags.json`` gespeichert
(Schlüssel: absoluter, aufgelöster Pfad).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PyQt6.QtGui import QAction, QColor
from PyQt6.QtWidgets import (
    QColorDialog,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.core.logging_setup import get_logger
from app.plugins.plugin_manager import PandoraPlugin
from app.ui.widgets.file_panel import FilePanel

logger = get_logger(__name__)

_STORAGE_PATH = Path.home() / ".pandora_commander" / "plugins" / "folder_tags.json"
_DEFAULT_TAG_COLOR = "#e74c3c"


@dataclass
class FolderTag:
    """Eine Farbmarkierung für einen Ordner."""

    color_hex: str
    label: str


def _load_tags() -> dict[str, FolderTag]:
    if not _STORAGE_PATH.is_file():
        return {}
    try:
        raw = json.loads(_STORAGE_PATH.read_text(encoding="utf-8"))
        return {
            path_str: FolderTag(color_hex=entry.get("color", _DEFAULT_TAG_COLOR), label=entry.get("label", ""))
            for path_str, entry in raw.items()
        }
    except (OSError, json.JSONDecodeError) as error:
        logger.warning("Ordner-Tags konnten nicht geladen werden: %s", error)
        return {}


def _save_tags(tags: dict[str, FolderTag]) -> None:
    try:
        _STORAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
        serializable = {
            path_str: {"color": tag.color_hex, "label": tag.label} for path_str, tag in tags.items()
        }
        _STORAGE_PATH.write_text(json.dumps(serializable, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError as error:
        logger.warning("Ordner-Tags konnten nicht gespeichert werden: %s", error)


class TagEditDialog(QDialog):
    """Dialog zur Auswahl von Farbe und Label für einen Ordner-Tag."""

    def __init__(self, folder_name: str, existing: FolderTag | None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Ordner-Tag: {folder_name}")

        self._selected_color = QColor(existing.color_hex if existing else _DEFAULT_TAG_COLOR)

        self._color_preview = QLabel()
        self._color_preview.setFixedSize(28, 20)
        self._update_color_preview()

        color_button = QPushButton("Farbe wählen …")
        color_button.clicked.connect(self._on_choose_color)

        color_row = QHBoxLayout()
        color_row.addWidget(self._color_preview)
        color_row.addWidget(color_button)
        color_row.addStretch(1)

        self._label_edit = QLineEdit(existing.label if existing else "")
        self._label_edit.setPlaceholderText("z. B. Aktuelles Projekt")

        form = QFormLayout()
        form.addRow("Farbe:", color_row)
        form.addRow("Label:", self._label_edit)

        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(button_box)

    def _on_choose_color(self) -> None:
        color = QColorDialog.getColor(self._selected_color, self, "Tag-Farbe wählen")
        if color.isValid():
            self._selected_color = color
            self._update_color_preview()

    def _update_color_preview(self) -> None:
        self._color_preview.setStyleSheet(
            f"background-color: {self._selected_color.name()}; border: 1px solid #555;"
        )

    @property
    def result_tag(self) -> FolderTag:
        return FolderTag(color_hex=self._selected_color.name(), label=self._label_edit.text().strip())


class FolderTagsSettingsWidget(QWidget):
    """Zeigt alle gespeicherten Ordner-Tags im Plugin-Manager-Dialog an."""

    def __init__(self, plugin: "FolderTagsPlugin", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._plugin = plugin

        self._list_widget = QListWidget()
        self._remove_button = QPushButton("Ausgewählten Tag entfernen")
        self._remove_button.clicked.connect(self._on_remove_clicked)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Getaggte Ordner:"))
        layout.addWidget(self._list_widget, stretch=1)
        layout.addWidget(self._remove_button)

        self._reload()

    def _reload(self) -> None:
        self._list_widget.clear()
        for path_str, tag in self._plugin.tags.items():
            display_text = f"{tag.label or '(ohne Label)'}  —  {path_str}"
            item = QListWidgetItem(display_text)
            item.setForeground(QColor(tag.color_hex))
            item.setData(1000, path_str)
            self._list_widget.addItem(item)

    def _on_remove_clicked(self) -> None:
        item = self._list_widget.currentItem()
        if item is None:
            return
        path_str = item.data(1000)
        self._plugin.remove_tag(path_str)
        self._reload()


class FolderTagsPlugin(PandoraPlugin):
    """Plugin für farbige Ordner-Tags mit Panel-Banner und Einstellungen-Tab."""

    name = "Ordner-Tags"
    version = "1.0"
    author = "AKI_SystemDown®"
    description = (
        "Fügt dem Kontextmenü 'Ordner-Tag setzen …' hinzu: farbige Markierung für "
        "Ordner mit optionalem Label, sichtbar als Banner beim Betreten des Ordners. "
        "Eigenes Einstellungen-Tab zur Übersicht und zum Entfernen von Tags."
    )

    def __init__(self) -> None:
        self._context: dict[str, Any] = {}
        self.tags: dict[str, FolderTag] = _load_tags()
        self._banners: dict[int, QLabel] = {}

    def on_load(self, context: dict[str, Any]) -> None:
        self._context = context
        logger.info("%s geladen (%d gespeicherte Tag(s)).", self.name, len(self.tags))

    def on_unload(self) -> None:
        for banner in self._banners.values():
            banner.deleteLater()
        self._banners.clear()

    def build_context_menu_entries(
        self, context: dict[str, Any], selected_paths: list[Path]
    ) -> list[QAction]:
        directories = [path for path in selected_paths if path.is_dir()]
        if len(directories) != 1:
            return []

        main_window = context.get("main_window")
        directory = directories[0]
        action = QAction("Ordner-Tag setzen …", main_window)
        action.triggered.connect(lambda checked=False, path=directory: self._open_tag_dialog(path))
        return [action]

    def build_settings_widget(self, context: dict[str, Any]) -> QWidget | None:
        return FolderTagsSettingsWidget(self)

    def on_panel_directory_changed(self, context: dict[str, Any], panel: FilePanel, path: Path) -> None:
        panel_id = id(panel)
        banner = self._banners.get(panel_id)
        if banner is None:
            banner = QLabel()
            banner.setVisible(False)
            layout = panel.layout()
            if layout is not None:
                layout.addWidget(banner)
            self._banners[panel_id] = banner

        tag = self.tags.get(str(path.resolve()))
        if tag is None:
            banner.setVisible(False)
            return

        banner.setText(f"🏷 {tag.label or 'Markierter Ordner'}")
        banner.setStyleSheet(
            f"background-color: {tag.color_hex}; color: white; padding: 3px 6px; font-size: 11px;"
        )
        banner.setVisible(True)

    def _open_tag_dialog(self, directory: Path) -> None:
        main_window = self._context.get("main_window")
        resolved_key = str(directory.resolve())
        existing = self.tags.get(resolved_key)

        dialog = TagEditDialog(directory.name, existing, parent=main_window)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        self.tags[resolved_key] = dialog.result_tag
        _save_tags(self.tags)

        left_panel = self._context.get("left_panel")
        right_panel = self._context.get("right_panel")
        for panel in (left_panel, right_panel):
            current_directory = getattr(panel, "current_directory", None)
            if panel is not None and current_directory == directory:
                self.on_panel_directory_changed(self._context, panel, directory)

        QMessageBox.information(main_window, "Tag gespeichert", f"Ordner '{directory.name}' wurde markiert.")

    def remove_tag(self, path_str: str) -> None:
        if path_str in self.tags:
            del self.tags[path_str]
            _save_tags(self.tags)
            for panel_id, banner in list(self._banners.items()):
                banner.setVisible(False)
