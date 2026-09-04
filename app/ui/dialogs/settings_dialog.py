"""Pandora® Commander – Einstellungsdialog.

Bietet eine Oberfläche für alle in core.config.Settings hinterlegten
Einstellungen: Theme, Sprache, Schriftgröße, Standardpfade,
Tastenkürzel und Löschbestätigung. Änderungen werden erst beim
Bestätigen (OK) über ConfigManager.save() persistiert.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.core.config import ConfigManager, Settings
from app.core.logging_setup import get_logger

logger = get_logger(__name__)

_LANGUAGES = [("Deutsch", "de"), ("English", "en")]
_THEMES = [("Dunkel", "dark"), ("Hell", "light")]


class SettingsDialog(QDialog):
    """Dialog zur Bearbeitung der Anwendungseinstellungen.

    Args:
        config_manager: Zentraler ConfigManager der Anwendung.
        parent: Optionales Eltern-Widget.
    """

    def __init__(self, config_manager: ConfigManager, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Einstellungen")
        self.resize(520, 480)
        self._config_manager = config_manager
        self._settings = config_manager.current()

        self._tabs = QTabWidget(self)
        self._tabs.addTab(self._build_general_tab(), "Allgemein")
        self._tabs.addTab(self._build_paths_tab(), "Pfade")
        self._tabs.addTab(self._build_shortcuts_tab(), "Tastenkürzel")

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(self._tabs)
        layout.addWidget(buttons)

    def _build_general_tab(self) -> QWidget:
        widget = QWidget(self)
        form = QFormLayout(widget)

        self._theme_combo = QComboBox(widget)
        for display, value in _THEMES:
            self._theme_combo.addItem(display, value)
        self._theme_combo.setCurrentIndex(
            next((i for i, (_, v) in enumerate(_THEMES) if v == self._settings.theme), 0)
        )
        form.addRow("Theme:", self._theme_combo)

        self._language_combo = QComboBox(widget)
        for display, value in _LANGUAGES:
            self._language_combo.addItem(display, value)
        self._language_combo.setCurrentIndex(
            next((i for i, (_, v) in enumerate(_LANGUAGES) if v == self._settings.language), 0)
        )
        form.addRow("Sprache:", self._language_combo)

        self._font_size_spin = QSpinBox(widget)
        self._font_size_spin.setRange(6, 32)
        self._font_size_spin.setValue(self._settings.font_size)
        form.addRow("Schriftgröße:", self._font_size_spin)

        self._confirm_delete_check = QCheckBox("Vor dem Löschen nachfragen", widget)
        self._confirm_delete_check.setChecked(self._settings.confirm_delete)
        form.addRow(self._confirm_delete_check)

        self._debug_mode_check = QCheckBox("Debugmodus aktivieren", widget)
        self._debug_mode_check.setChecked(self._settings.debug_mode)
        form.addRow(self._debug_mode_check)

        self._notifications_check = QCheckBox(
            "Benachrichtigungen nach Hintergrundoperationen anzeigen", widget
        )
        self._notifications_check.setChecked(self._settings.notifications_enabled)
        self._notifications_check.setToolTip(
            "Zeigt nach abgeschlossenem Kopieren, Verschieben oder Löschen\n"
            "eine native Desktop-Benachrichtigung an, auch wenn das\n"
            "Fenster minimiert oder nicht fokussiert ist."
        )
        form.addRow(self._notifications_check)

        self._update_url_edit = QLineEdit(self._settings.update_check_url, widget)
        self._update_url_edit.setPlaceholderText(
            "https://example.com/pandora-commander/update.json"
        )
        form.addRow("Update-URL:", self._update_url_edit)

        self._check_updates_startup_check = QCheckBox(
            "Beim Start automatisch nach Updates suchen", widget
        )
        self._check_updates_startup_check.setChecked(self._settings.check_updates_on_startup)
        form.addRow(self._check_updates_startup_check)

        self._max_concurrent_ops_spin = QSpinBox(widget)
        self._max_concurrent_ops_spin.setRange(1, 8)
        self._max_concurrent_ops_spin.setValue(self._settings.max_concurrent_operations)
        self._max_concurrent_ops_spin.setToolTip(
            "Wie viele Kopier-/Verschiebe-/Löschvorgänge die\n"
            "Operationen-Warteschlange höchstens gleichzeitig ausführt.\n"
            "Weitere Operationen warten, bis ein Platz frei wird."
        )
        form.addRow("Gleichzeitige Dateioperationen:", self._max_concurrent_ops_spin)

        return widget

    def _build_paths_tab(self) -> QWidget:
        widget = QWidget(self)
        form = QFormLayout(widget)

        self._left_path_edit = QLineEdit(self._settings.default_left_path, widget)
        left_browse = QPushButton("…", widget)
        left_browse.setMaximumWidth(32)
        left_browse.clicked.connect(lambda: self._browse_into(self._left_path_edit))
        left_row = QHBoxLayout()
        left_row.addWidget(self._left_path_edit)
        left_row.addWidget(left_browse)
        form.addRow("Linker Startpfad:", left_row)

        self._right_path_edit = QLineEdit(self._settings.default_right_path, widget)
        right_browse = QPushButton("…", widget)
        right_browse.setMaximumWidth(32)
        right_browse.clicked.connect(lambda: self._browse_into(self._right_path_edit))
        right_row = QHBoxLayout()
        right_row.addWidget(self._right_path_edit)
        right_row.addWidget(right_browse)
        form.addRow("Rechter Startpfad:", right_row)

        return widget

    def _browse_into(self, line_edit: QLineEdit) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Ordner wählen", line_edit.text())
        if chosen:
            line_edit.setText(chosen)

    def _build_shortcuts_tab(self) -> QWidget:
        widget = QWidget(self)
        layout = QVBoxLayout(widget)

        self._shortcuts_table = QTableWidget(widget)
        self._shortcuts_table.setColumnCount(2)
        self._shortcuts_table.setHorizontalHeaderLabels(["Aktion", "Tastenkürzel"])
        self._shortcuts_table.horizontalHeader().setStretchLastSection(True)

        actions = list(self._settings.shortcuts.items())
        self._shortcuts_table.setRowCount(len(actions))
        for row, (action, shortcut) in enumerate(actions):
            action_item = QTableWidgetItem(action)
            action_item.setFlags(action_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._shortcuts_table.setItem(row, 0, action_item)
            self._shortcuts_table.setItem(row, 1, QTableWidgetItem(shortcut))

        layout.addWidget(self._shortcuts_table)
        return widget

    def _collect_shortcuts(self) -> dict[str, str]:
        shortcuts: dict[str, str] = {}
        for row in range(self._shortcuts_table.rowCount()):
            action_item = self._shortcuts_table.item(row, 0)
            shortcut_item = self._shortcuts_table.item(row, 1)
            if action_item is not None and shortcut_item is not None:
                shortcuts[action_item.text()] = shortcut_item.text()
        return shortcuts

    def _on_accept(self) -> None:
        new_settings = Settings(
            theme=self._theme_combo.currentData(),
            language=self._language_combo.currentData(),
            font_size=self._font_size_spin.value(),
            icon_theme=self._settings.icon_theme,
            default_left_path=self._left_path_edit.text(),
            default_right_path=self._right_path_edit.text(),
            shortcuts=self._collect_shortcuts(),
            favorites=self._settings.favorites,
            debug_mode=self._debug_mode_check.isChecked(),
            confirm_delete=self._confirm_delete_check.isChecked(),
            notifications_enabled=self._notifications_check.isChecked(),
            update_check_url=self._update_url_edit.text().strip(),
            check_updates_on_startup=self._check_updates_startup_check.isChecked(),
            max_concurrent_operations=self._max_concurrent_ops_spin.value(),
        )
        self._config_manager.save(new_settings)
        logger.info("Einstellungen gespeichert.")
        self.accept()
