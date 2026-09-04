"""Pandora® Commander – Plugin-Manager-Dialog.

Zeigt einen "Übersicht"-Tab mit allen gefundenen Plugins (Aktiv-
Checkbox, Version, Autor, Ladestatus) für globale Aktionen (Neu
laden, Hot-Reload umschalten, Plugin-Ordner öffnen) sowie zusätzlich
für jedes entdeckte Plugin einen eigenen Tab mit dessen Metadaten,
Abhängigkeiten und – falls das Plugin ``build_settings_widget()``
überschreibt – dessen individuellem Einstellungs-Widget.
"""

from __future__ import annotations

from typing import Any

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.core.config import ConfigManager
from app.core.logging_setup import get_logger
from app.plugins.plugin_manager import PluginInfo, PluginManager

logger = get_logger(__name__)

_COLUMN_ENABLED = 0
_COLUMN_NAME = 1
_COLUMN_VERSION = 2
_COLUMN_AUTHOR = 3
_COLUMN_STATUS = 4


class PluginManagerDialog(QDialog):
    """Dialog zur Verwaltung installierter Plugins.

    Args:
        plugin_manager: Zentraler PluginManager der Anwendung.
        config_manager: Zentrale Einstellungsverwaltung, für die
            Persistenz des Aktivierungsstatus.
        reload_context: dict, das beim Neu-Laden erneut an
            PluginManager.reload_all() übergeben wird, und das
            unverändert an build_settings_widget() jedes Plugins
            weitergereicht wird.
        parent: Optionales Eltern-Widget.
    """

    def __init__(
        self,
        plugin_manager: PluginManager,
        config_manager: ConfigManager,
        reload_context: dict[str, Any],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Plugin-Manager")
        self.resize(820, 560)

        self._plugin_manager = plugin_manager
        self._config_manager = config_manager
        self._reload_context = reload_context
        self._main_window = reload_context.get("main_window")

        self._tab_widget = QTabWidget(self)
        self._overview_tab = self._build_overview_tab()
        self._tab_widget.addTab(self._overview_tab, "Übersicht")

        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        button_box.rejected.connect(self.reject)
        button_box.accepted.connect(self.accept)

        layout = QVBoxLayout(self)
        layout.addWidget(self._tab_widget, 1)
        layout.addWidget(button_box)

        self._rebuild_plugin_tabs()

    # ------------------------------------------------------------------
    # Übersicht-Tab (globale Tabelle + Sammel-Aktionen)
    # ------------------------------------------------------------------

    def _build_overview_tab(self) -> QWidget:
        widget = QWidget(self)

        self._table = QTableWidget(0, 5, widget)
        self._table.setHorizontalHeaderLabels(
            ["Aktiv", "Name", "Version", "Autor", "Status"]
        )
        self._table.horizontalHeader().setSectionResizeMode(
            _COLUMN_NAME, QHeaderView.ResizeMode.Stretch
        )
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)

        self._path_label = QLabel(f"Plugin-Verzeichnis: {self._plugin_manager.plugin_dir}", widget)
        self._path_label.setWordWrap(True)
        open_folder_button = QPushButton("Ordner öffnen", widget)
        open_folder_button.clicked.connect(self._open_plugin_folder)

        reload_button = QPushButton("Alle neu laden", widget)
        reload_button.clicked.connect(self._reload_all)

        path_row = QHBoxLayout()
        path_row.addWidget(self._path_label, 1)
        path_row.addWidget(open_folder_button)
        path_row.addWidget(reload_button)

        self._hot_reload_checkbox = QCheckBox(
            "Automatisches Neuladen bei Dateiänderungen (Hot-Reload)", widget
        )
        self._hot_reload_checkbox.setToolTip(
            "Überwacht das Plugin-Verzeichnis im Hintergrund und lädt Plugins "
            "automatisch neu, sobald eine .py-Datei erstellt, geändert, "
            "gelöscht oder umbenannt wird (ca. 0,5 s nach der letzten Änderung)."
        )
        self._hot_reload_checkbox.setChecked(
            bool(getattr(self._main_window, "plugin_hot_reload_enabled", True))
        )
        self._hot_reload_checkbox.toggled.connect(self._on_hot_reload_toggled)

        hint_label = QLabel(
            "Jedes Plugin hat zusätzlich einen eigenen Tab mit Details, "
            "Abhängigkeiten und – falls vorhanden – eigenen Einstellungen.",
            widget,
        )
        hint_label.setWordWrap(True)
        hint_label.setStyleSheet("color: #9a9da2;")

        layout = QVBoxLayout(widget)
        layout.addWidget(self._table, 1)
        layout.addLayout(path_row)
        layout.addWidget(self._hot_reload_checkbox)
        layout.addWidget(hint_label)
        return widget

    def _reload_overview_table(self) -> None:
        """Baut die Übersichtstabelle aus den aktuellen PluginInfo-Metadaten neu auf."""
        try:
            self._table.itemChanged.disconnect(self._on_item_changed)
        except TypeError:
            pass  # war noch nicht verbunden (erster Aufbau)

        infos = self._plugin_manager.plugin_infos
        self._table.setRowCount(len(infos))

        for row, info in enumerate(infos):
            checkbox_item = QTableWidgetItem()
            checkbox_item.setFlags(
                Qt.ItemFlag.ItemIsUserCheckable
                | Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
            )
            checkbox_item.setCheckState(
                Qt.CheckState.Checked if info.enabled else Qt.CheckState.Unchecked
            )
            checkbox_item.setData(Qt.ItemDataRole.UserRole, info.file_name)
            self._table.setItem(row, _COLUMN_ENABLED, checkbox_item)

            self._table.setItem(row, _COLUMN_NAME, QTableWidgetItem(info.name))
            self._table.setItem(row, _COLUMN_VERSION, QTableWidgetItem(info.version))
            self._table.setItem(row, _COLUMN_AUTHOR, QTableWidgetItem(info.author))

            status_item = QTableWidgetItem(self._status_text(info))
            if info.error:
                status_item.setForeground(Qt.GlobalColor.red)
            elif info.loaded:
                status_item.setForeground(Qt.GlobalColor.green)
            self._table.setItem(row, _COLUMN_STATUS, status_item)

        self._table.itemChanged.connect(self._on_item_changed)

    @staticmethod
    def _status_text(info: PluginInfo) -> str:
        if not info.enabled:
            return "Deaktiviert"
        if info.error:
            return "Fehler beim Laden"
        if info.loaded:
            return "Geladen"
        return "Unbekannt"

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        """Reagiert auf das Umschalten der Aktiv-Checkbox einer Zeile."""
        if item.column() != _COLUMN_ENABLED:
            return
        file_name = item.data(Qt.ItemDataRole.UserRole)
        if not file_name:
            return

        enabled = item.checkState() == Qt.CheckState.Checked
        self._plugin_manager.set_enabled(file_name, enabled)
        self._persist_disabled_plugins()
        self._plugin_manager.reload_all(self._reload_context)
        self._refresh_everything()

    def _persist_disabled_plugins(self) -> None:
        """Schreibt den aktuellen Aktivierungsstatus in die Settings-Datei."""
        settings = self._config_manager.current()
        settings.disabled_plugins = sorted(self._plugin_manager.disabled_plugins)
        self._config_manager.save(settings)

    def _reload_all(self) -> None:
        """Lädt alle Plugin-Dateien neu ein (z. B. nach Bearbeitung)."""
        self._plugin_manager.reload_all(self._reload_context)
        self._refresh_everything()
        QMessageBox.information(self, "Plugin-Manager", "Alle Plugins wurden neu geladen.")

    def _open_plugin_folder(self) -> None:
        """Öffnet das Plugin-Verzeichnis im Dateimanager des Betriebssystems."""
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._plugin_manager.plugin_dir)))

    def _on_hot_reload_toggled(self, checked: bool) -> None:
        """Reicht das Umschalten der Hot-Reload-Checkbox an das Hauptfenster weiter."""
        if self._main_window is not None and hasattr(
            self._main_window, "set_plugin_hot_reload_enabled"
        ):
            self._main_window.set_plugin_hot_reload_enabled(checked)
        else:
            logger.warning(
                "Hot-Reload-Umschaltung konnte nicht angewendet werden: "
                "Hauptfenster nicht im Kontext verfügbar."
            )

    # ------------------------------------------------------------------
    # Ein Tab pro entdecktem Plugin
    # ------------------------------------------------------------------

    def _refresh_everything(self) -> None:
        """Baut Übersichtstabelle und alle Plugin-Tabs komplett neu auf."""
        self._rebuild_plugin_tabs()

    def _rebuild_plugin_tabs(self) -> None:
        """Entfernt alle bisherigen Plugin-Tabs und baut sie aus den aktuellen Infos neu auf."""
        self._reload_overview_table()

        while self._tab_widget.count() > 1:
            widget = self._tab_widget.widget(1)
            self._tab_widget.removeTab(1)
            if widget is not None:
                widget.deleteLater()

        for info in self._plugin_manager.plugin_infos:
            self._tab_widget.addTab(self._build_plugin_tab(info), info.name)

    def _build_plugin_tab(self, info: PluginInfo) -> QWidget:
        """Baut den individuellen Tab für genau ein Plugin.

        Args:
            info: Metadaten des Plugins, für das der Tab gebaut wird.

        Returns:
            Ein scrollbares Widget mit Metadaten, Aktiv-Checkbox,
            Abhängigkeiten, Beschreibung/Fehlermeldung und – falls
            vorhanden – dem eigenen Einstellungs-Widget des Plugins.
        """
        content = QWidget(self)
        layout = QVBoxLayout(content)

        enabled_checkbox = QCheckBox("Plugin aktiviert", content)
        enabled_checkbox.setChecked(info.enabled)
        enabled_checkbox.toggled.connect(
            lambda checked, file_name=info.file_name: self._on_plugin_tab_enabled_toggled(
                file_name, checked
            )
        )
        layout.addWidget(enabled_checkbox)

        meta_box = QGroupBox("Details", content)
        form = QFormLayout(meta_box)
        form.addRow("Version:", QLabel(info.version or "—", meta_box))
        form.addRow("Autor:", QLabel(info.author or "—", meta_box))
        form.addRow("Datei:", QLabel(info.file_name, meta_box))
        form.addRow("Status:", QLabel(self._status_text(info), meta_box))
        requires_text = ", ".join(info.requires) if info.requires else "Keine"
        form.addRow("Benötigt:", QLabel(requires_text, meta_box))
        layout.addWidget(meta_box)

        description_view = QPlainTextEdit(content)
        description_view.setReadOnly(True)
        description_view.setMaximumHeight(90)
        text = info.description or "(keine Beschreibung)"
        if info.error:
            text += f"\n\nFehler:\n{info.error}"
        description_view.setPlainText(text)
        layout.addWidget(QLabel("Beschreibung:", content))
        layout.addWidget(description_view)

        custom_widget = self._try_build_custom_settings_widget(info)
        if custom_widget is not None:
            settings_box = QGroupBox("Plugin-Einstellungen", content)
            settings_layout = QVBoxLayout(settings_box)
            settings_layout.addWidget(custom_widget)
            layout.addWidget(settings_box)
        else:
            layout.addWidget(
                QLabel("Dieses Plugin bietet keine eigenen Einstellungen an.", content)
            )

        layout.addStretch(1)

        scroll_area = QScrollArea(self)
        scroll_area.setWidgetResizable(True)
        scroll_area.setWidget(content)
        return scroll_area

    def _try_build_custom_settings_widget(self, info: PluginInfo) -> QWidget | None:
        """Ruft build_settings_widget() eines geladenen Plugins sicher auf.

        Args:
            info: Metadaten des Plugins (benötigt die geladene Instanz).

        Returns:
            Das vom Plugin gelieferte Widget, oder None wenn das
            Plugin nicht geladen ist, keine Anpassung vornimmt oder
            der Aufruf fehlschlägt (dann wird der Fehler geloggt).
        """
        if info.instance is None:
            return None
        try:
            return info.instance.build_settings_widget(self._reload_context)
        except Exception as error:
            logger.error(
                "Fehler in build_settings_widget() von Plugin %s: %s", info.name, error
            )
            return None

    def _on_plugin_tab_enabled_toggled(self, file_name: str, checked: bool) -> None:
        """Reagiert auf die Aktiv-Checkbox innerhalb eines einzelnen Plugin-Tabs."""
        self._plugin_manager.set_enabled(file_name, checked)
        self._persist_disabled_plugins()
        self._plugin_manager.reload_all(self._reload_context)
        self._refresh_everything()
