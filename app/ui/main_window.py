"""Pandora® Commander – Hauptfenster.

Setzt die Anwendung sichtbar zusammen: Menüleiste, Symbolleiste, zwei
nebeneinanderliegende FilePanel-Instanzen (links/rechts) in einem
QSplitter sowie eine Statusleiste.

Verdrahtet alle Menü-/Symbolleisten-Aktionen, deren Fachlogik bereits
existiert: Beenden, Aktualisieren, Versteckte Dateien umschalten,
Panel wechseln, Kopieren, Verschieben, Löschen, Umbenennen, Neuer
Ordner, Eigenschaften, Editor, Vorschau, Suche, Favoriten,
Massenumbenennung, Hash-Werkzeuge, Dateivergleich, Archivverwaltung,
Netzwerkverbindungen, Einstellungen, Terminal, Über-Dialog.

Commander-typisches Verhalten für Kopieren/Verschieben: Quelle ist
das aktive (zuletzt fokussierte) Panel, Ziel ist automatisch das
jeweils andere Panel – analog zu Total Commander & Co.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from PyQt6.QtCore import QEvent, QSize, Qt, QTimer
from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtWidgets import (
    QDialog,
    QFileDialog,
    QInputDialog,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QSplitter,
    QStatusBar,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from app.core.archive.archive_handler import (
    ArchiveError,
    create_archive,
    extract_archive,
    is_archive,
)
from app.core.config import CONFIG_DIR, ConfigManager, Settings
from app.core.filesystem.favorites import FavoritesManager
from app.core.filesystem.file_operations import (
    CollisionPolicy,
    FileOperationWorker,
    OperationResult,
    OperationType,
    find_existing_collisions,
)
from app.core.filesystem.file_tags import LABEL_COLORS, SUGGESTED_TAGS, TagsManager
from app.core.filesystem.operation_queue import OperationQueueManager, QueueItemState
from app.core.filesystem.undo_manager import (
    CopyAction,
    DeleteAction,
    MoveAction,
    NewFolderAction,
    RenameAction,
    UndoManager,
)
from app.core.logging_setup import get_logger
from app.core.network.connection_manager import ConnectionManager, ConnectionProfile
from app.core.notifications import NotificationManager
from app.core.update_checker import UpdateCheckWorker, UpdateInfo
from app.plugins.hot_reload import PluginHotReloadWatcher
from app.plugins.plugin_manager import PluginManager
from app.ui.dialogs.archive_browser_dialog import ArchiveBrowserDialog
from app.ui.dialogs.bulk_rename_dialog import BulkRenameDialog
from app.ui.dialogs.compare_dialog import CompareDialog
from app.ui.dialogs.connection_dialog import ConnectionDialog
from app.ui.dialogs.dashboard_dialog import DashboardDialog
from app.ui.dialogs.editor_window import EditorWindow
from app.ui.dialogs.favorites_dialog import FavoritesDialog
from app.ui.dialogs.hash_dialog import HashDialog
from app.ui.dialogs.operations_queue_dialog import OperationsQueueDialog
from app.ui.dialogs.plugin_manager_dialog import PluginManagerDialog
from app.ui.dialogs.properties_dialog import PropertiesDialog
from app.ui.dialogs.search_dialog import SearchDialog
from app.ui.dialogs.settings_dialog import SettingsDialog
from app.ui.dialogs.update_dialog import UpdateAvailableDialog
from app.ui.widgets.file_panel import FilePanel
from app.ui.widgets.preview_widget import PreviewWidget
from app.ui.widgets.terminal_widget import TerminalWidget
from app.utils.icon_provider import get_icon
from app.utils.thumbnail_provider import ThumbnailProvider
from app.utils.translator import get_translator, tr
from app.main import APP_NAME, APP_VERSION

logger = get_logger(__name__)

#: Kantenlänge der Symbolleisten-Icons in Pixeln ("große Icons" laut Lastenheft).
_TOOLBAR_ICON_SIZE = 28




class MainWindow(QMainWindow):
    """Hauptfenster von Pandora® Commander.

    Args:
        config_manager: Zentrale Einstellungsverwaltung. Wird das
            Fenster geschlossen, werden die Startpfade beider Panels
            als default_left_path/default_right_path gespeichert.
        parent: Optionales Eltern-Widget.
    """

    def __init__(
        self,
        config_manager: ConfigManager | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        self._start_time = time.monotonic()

        self._config_manager = config_manager or ConfigManager()
        self._settings: Settings = self._config_manager.current()
        get_translator().set_language(self._settings.language)

        self.setWindowTitle(f"{APP_NAME} {APP_VERSION}")
        self.setWindowIcon(get_icon("app_icon"))
        self.resize(1280, 800)

        self._tags_manager = TagsManager()
        self._thumbnail_provider = ThumbnailProvider(parent=self)

        self._left_panel = FilePanel(
            initial_directory=Path(self._settings.default_left_path),
            tags_manager=self._tags_manager,
            thumbnail_provider=self._thumbnail_provider,
        )
        self._right_panel = FilePanel(
            initial_directory=Path(self._settings.default_right_path),
            tags_manager=self._tags_manager,
            thumbnail_provider=self._thumbnail_provider,
        )
        self._active_panel: FilePanel = self._left_panel
        self._open_editor_windows: list[EditorWindow] = []
        self._open_terminal_windows: list[QDialog] = []
        self._open_dashboard_dialogs: list[DashboardDialog] = []

        self._favorites_manager = FavoritesManager()
        self._connection_manager = ConnectionManager()
        self._undo_manager = UndoManager(trash_root=CONFIG_DIR / "trash", parent=self)
        self._undo_manager.stack_changed.connect(self._update_undo_redo_actions)
        self._plugin_manager = PluginManager(
            disabled_plugins=set(self._settings.disabled_plugins)
        )
        self._plugin_hot_reloader = PluginHotReloadWatcher(self)
        self._plugin_hot_reloader.reload_requested.connect(self._on_plugin_hot_reload_triggered)

        self._notifications = NotificationManager(get_icon("app_icon"), parent=self)
        self._notifications.set_enabled(self._settings.notifications_enabled)
        self._update_worker: UpdateCheckWorker | None = None

        self._operation_queue = OperationQueueManager(
            max_concurrent=self._settings.max_concurrent_operations, parent=self
        )
        self._operation_queue.job_finished.connect(self._on_queue_job_finished)
        self._job_completion_callbacks: dict[int, Callable[[OperationResult], None]] = {}
        self._operations_queue_dialog: OperationsQueueDialog | None = None

        self._status_bar = QStatusBar()
        self._path_status_label = self._make_status_label()

        self._setup_central_widget()
        self._setup_status_bar()
        self._setup_actions()
        self._setup_menu_bar()
        self._setup_toolbar()
        self._connect_panel_signals()

        self._install_focus_tracking()
        self._load_plugins()

        if self._settings.check_updates_on_startup:
            # Verzögert starten, damit der Update-Check den sichtbaren
            # Programmstart nicht verlangsamt oder blockiert.
            QTimer.singleShot(2000, lambda: self._start_update_check(silent=True))

    # ------------------------------------------------------------------
    # Aufbau: Plugins
    # ------------------------------------------------------------------

    def _load_plugins(self) -> None:
        """Lädt automatisch alle installierten Plugins aus dem Plugin-Verzeichnis.

        Fehlerhafte Plugins werden übersprungen und geloggt (siehe
        ``PluginManager.load_all``), ohne den Programmstart zu
        gefährden. Jedes Plugin erhält über ``context`` Zugriff auf
        zentrale Anwendungsobjekte.
        """
        loaded = self._plugin_manager.load_all(self._build_plugin_context())
        if loaded:
            names = ", ".join(plugin.name for plugin in loaded)
            self._status_bar.showMessage(f"{len(loaded)} Plugin(s) geladen: {names}", 4000)
            logger.info("Plugins geladen: %s", names)
        self._populate_plugins_menu()
        self._populate_plugin_toolbar_actions()
        self._apply_hot_reload_setting()

    def _build_plugin_context(self) -> dict[str, object]:
        """Baut das an Plugins übergebene Kontext-dict mit zentralen Anwendungsobjekten."""
        return {
            "main_window": self,
            "config_manager": self._config_manager,
            "favorites_manager": self._favorites_manager,
            "connection_manager": self._connection_manager,
            "left_panel": self._left_panel,
            "right_panel": self._right_panel,
        }

    def _populate_plugins_menu(self) -> None:
        """Baut den plugin-spezifischen Teil des "Plugins"-Menüs neu auf.

        Alles nach dem festen Trenner (Plugin-Manager-Eintrag) wird
        entfernt und aus den aktuell geladenen Plugins neu erzeugt,
        damit ein Neu-Laden im Plugin-Manager-Dialog sich sofort in
        der Menüleiste widerspiegelt.
        """
        if not hasattr(self, "_plugins_menu"):
            return

        for action in list(self._plugins_menu.actions()):
            if action is self.action_plugin_manager or action is self._plugins_menu_separator:
                continue
            self._plugins_menu.removeAction(action)

        has_entries = False
        for plugin, actions in self._plugin_manager.collect_menu_actions():
            for action in actions:
                self._plugins_menu.addAction(action)
                has_entries = True

        if not has_entries:
            placeholder = self._plugins_menu.addAction("(keine Plugin-Menüeinträge)")
            placeholder.setEnabled(False)

    def _populate_plugin_toolbar_actions(self) -> None:
        """Fügt von Plugins registrierte Symbolleisten-Aktionen nach dem festen Trenner ein."""
        if not hasattr(self, "_toolbar"):
            return

        for action in list(self._toolbar.actions()):
            if action.data() == "plugin_action":
                self._toolbar.removeAction(action)

        for action in self._plugin_manager.collect_toolbar_actions():
            action.setData("plugin_action")
            self._toolbar.addAction(action)

    def _on_open_plugin_manager(self) -> None:
        """Öffnet den Plugin-Manager-Dialog und aktualisiert Menü/Symbolleiste danach."""
        dialog = PluginManagerDialog(
            self._plugin_manager,
            self._config_manager,
            self._build_plugin_context(),
            self,
        )
        dialog.exec()
        self._populate_plugins_menu()
        self._populate_plugin_toolbar_actions()

    def _on_plugin_hot_reload_triggered(self) -> None:
        """Reagiert auf vom PluginHotReloadWatcher gemeldete Dateiänderungen.

        Lädt alle Plugins neu und aktualisiert Menü und Symbolleiste,
        ohne dass der Nutzer die Anwendung neu starten muss.
        """
        logger.info("Änderung im Plugin-Verzeichnis erkannt – lade Plugins neu …")
        self._plugin_manager.reload_all(self._build_plugin_context())
        self._populate_plugins_menu()
        self._populate_plugin_toolbar_actions()
        self._status_bar.showMessage("Plugins automatisch neu geladen (Hot-Reload).", 4000)

    def _apply_hot_reload_setting(self) -> None:
        """Startet oder stoppt den Plugin-Hot-Reload-Beobachter gemäß aktueller Einstellung."""
        if self._settings.plugin_hot_reload:
            self._plugin_hot_reloader.start(self._plugin_manager.plugin_dir)
        else:
            self._plugin_hot_reloader.stop()

    @property
    def plugin_hot_reload_enabled(self) -> bool:
        """Ob die automatische Plugin-Neuladung bei Dateiänderungen aktiv ist."""
        return self._settings.plugin_hot_reload

    def set_plugin_hot_reload_enabled(self, enabled: bool) -> None:
        """Schaltet den Plugin-Hot-Reload um und persistiert die Wahl sofort.

        Öffentliche Schnittstelle, primär für die Checkbox im
        Plugin-Manager-Dialog gedacht.

        Args:
            enabled: True zum Aktivieren, False zum Deaktivieren.
        """
        self._settings.plugin_hot_reload = enabled
        try:
            self._config_manager.save(self._settings)
        except OSError:
            logger.warning("Hot-Reload-Einstellung konnte nicht gespeichert werden.")
        self._apply_hot_reload_setting()

    def _on_panel_context_menu_requested(self, panel: FilePanel, global_pos) -> None:  # noqa: ANN001
        """Baut und zeigt das Kontextmenü eines Dateipanels, inkl. Plugin-Einträgen.

        Die Standardaktionen (Kopieren, Verschieben, Löschen, …)
        werden aus den bereits vorhandenen QAction-Objekten
        wiederverwendet. Anschließend fragt der PluginManager alle
        geladenen Plugins nach zusätzlichen, auswahlabhängigen
        Einträgen (siehe PandoraPlugin.build_context_menu_entries).

        Args:
            panel: Das Panel, in dem rechtsgeklickt wurde.
            global_pos: Globale Bildschirmposition für die Anzeige.
        """
        self._set_active_panel(panel)
        menu = QMenu(self)
        menu.addAction(self.action_copy)
        menu.addAction(self.action_move)
        menu.addAction(self.action_delete)
        menu.addAction(self.action_rename)
        menu.addSeparator()
        menu.addAction(self.action_new_folder)
        menu.addAction(self.action_properties)
        menu.addSeparator()
        menu.addAction(self.action_edit)
        menu.addAction(self.action_preview)
        menu.addAction(self.action_create_archive)
        menu.addAction(self.action_extract_archive)

        selected_paths = panel.selected_paths()
        if selected_paths:
            menu.addSeparator()
            self._add_tags_submenu(menu, selected_paths)

        plugin_actions = self._plugin_manager.collect_context_menu_entries(
            panel, selected_paths
        )
        if plugin_actions:
            menu.addSeparator()
            for action in plugin_actions:
                menu.addAction(action)

        menu.exec(global_pos)

    def _add_tags_submenu(self, menu: QMenu, paths: list[Path]) -> None:
        """Fügt die Untermenüs "Farbmarkierung" und "Tags" für die Auswahl ein.

        Bei mehreren markierten Elementen gilt jede Aktion für alle
        gemeinsam (z. B. dieselbe Farbe für die gesamte Auswahl
        setzen, oder einen Tag für alle gemeinsam umschalten).

        Args:
            menu: Das Kontextmenü, dem die Untermenüs hinzugefügt werden.
            paths: Die betroffenen, aktuell markierten Pfade.
        """
        color_menu = menu.addMenu("Farbmarkierung")
        color_menu.addAction("Keine", lambda: self._apply_color_label(paths, None))
        color_menu.addSeparator()
        for label_name in LABEL_COLORS:
            color_menu.addAction(
                label_name,
                lambda checked=False, name=label_name: self._apply_color_label(paths, name),
            )

        tags_menu = menu.addMenu("Tags")
        known_tags = sorted({*SUGGESTED_TAGS, *self._tags_manager.all_known_tags()})
        if len(paths) == 1:
            current_tags = set(self._tags_manager.get(paths[0]).tags)
            for tag_name in known_tags:
                tag_action = tags_menu.addAction(tag_name)
                tag_action.setCheckable(True)
                tag_action.setChecked(tag_name in current_tags)
                tag_action.triggered.connect(
                    lambda checked=False, name=tag_name: self._toggle_tag(paths, name)
                )
        else:
            for tag_name in known_tags:
                tags_menu.addAction(
                    tag_name,
                    lambda checked=False, name=tag_name: self._toggle_tag(paths, name),
                )
        tags_menu.addSeparator()
        tags_menu.addAction("Tags bearbeiten …", lambda: self._edit_tags_dialog(paths))

    def _apply_color_label(self, paths: list[Path], color: str | None) -> None:
        """Setzt (oder entfernt) die Farbmarkierung für alle übergebenen Pfade."""
        for path in paths:
            self._tags_manager.set_color(path, color)
        self._left_panel.refresh_decorations()
        self._right_panel.refresh_decorations()

    def _toggle_tag(self, paths: list[Path], tag: str) -> None:
        """Schaltet einen Tag für alle übergebenen Pfade gemeinsam um.

        Ist der Tag bei mindestens einem der Pfade noch nicht gesetzt,
        wird er bei allen gesetzt; ist er bereits bei allen gesetzt,
        wird er bei allen entfernt (Verhalten wie bei Checkbox-Listen
        mit gemischtem Zustand).
        """
        all_have_tag = all(tag in self._tags_manager.get(path).tags for path in paths)
        for path in paths:
            has_tag = tag in self._tags_manager.get(path).tags
            if all_have_tag and has_tag:
                self._tags_manager.toggle_tag(path, tag)
            elif not all_have_tag and not has_tag:
                self._tags_manager.toggle_tag(path, tag)
        self._left_panel.refresh_decorations()
        self._right_panel.refresh_decorations()

    def _edit_tags_dialog(self, paths: list[Path]) -> None:
        """Öffnet einen Texteingabe-Dialog zum freien Bearbeiten der Tags.

        Bei einem einzelnen markierten Element wird die vorhandene
        Tag-Liste vorbefüllt; bei mehreren wird mit einer leeren
        Eingabe gestartet und die eingegebene Liste allen gemeinsam
        zugewiesen.
        """
        prefill = ""
        if len(paths) == 1:
            prefill = ", ".join(self._tags_manager.get(paths[0]).tags)

        text, confirmed = QInputDialog.getText(
            self,
            "Tags bearbeiten",
            "Tags (kommagetrennt):",
            QLineEdit.EchoMode.Normal,
            prefill,
        )
        if not confirmed:
            return

        tags = [part.strip() for part in text.split(",") if part.strip()]
        for path in paths:
            self._tags_manager.set_tags(path, tags)
        self._left_panel.refresh_decorations()
        self._right_panel.refresh_decorations()

    # ------------------------------------------------------------------
    # Aufbau: zentrales Widget (Splitter mit zwei Panels)
    # ------------------------------------------------------------------

    def _setup_central_widget(self) -> None:
        """Erstellt den Splitter mit linkem und rechtem Panel."""
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._left_panel)
        splitter.addWidget(self._right_panel)
        splitter.setSizes([640, 640])
        splitter.setChildrenCollapsible(False)
        self.setCentralWidget(splitter)

    # ------------------------------------------------------------------
    # Aufbau: Statusleiste
    # ------------------------------------------------------------------

    def _make_status_label(self) -> QWidget:
        from PyQt6.QtWidgets import QLabel

        label = QLabel("")
        return label

    def _setup_status_bar(self) -> None:
        """Richtet die Statusleiste ein und zeigt sie initial an."""
        self._status_bar.addPermanentWidget(self._path_status_label)
        self.setStatusBar(self._status_bar)
        self._update_path_status_label()

    # ------------------------------------------------------------------
    # Aufbau: Aktionen (wiederverwendbar für Menü und Symbolleiste)
    # ------------------------------------------------------------------

    def _setup_actions(self) -> None:
        """Erstellt alle QAction-Objekte mit echter Funktion, Icon und Übersetzung.

        Jede Aktion wird zusätzlich in ``self._translatable_actions``
        zusammen mit ihrem Übersetzungsschlüssel und dem deutschen
        Fallback-Text vermerkt, damit ``_retranslate_ui()`` nach einem
        Sprachwechsel im Einstellungsdialog alle Texte live aktualisieren
        kann, ohne die Anwendung neu starten zu müssen.
        """
        self._translatable_actions: list[tuple[QAction, str, str]] = []

        def _make_action(icon_name: str, key: str, fallback: str) -> QAction:
            action = QAction(get_icon(icon_name), tr(key, fallback), self)
            self._translatable_actions.append((action, key, fallback))
            return action

        self.action_quit = _make_action("quit", "action.quit", "Beenden")
        self.action_quit.setShortcut(QKeySequence("F10"))
        self.action_quit.triggered.connect(self.close)

        self.action_refresh = _make_action("refresh", "toolbar.refresh", "Aktualisieren")
        self.action_refresh.setShortcut(QKeySequence("Ctrl+R"))
        self.action_refresh.triggered.connect(self._on_refresh_active_panel)

        self.action_switch_panel = _make_action(
            "switch_panel", "action.switch_panel", "Panel wechseln"
        )
        self.action_switch_panel.setShortcut(QKeySequence("Tab"))
        self.action_switch_panel.triggered.connect(self._on_switch_active_panel)

        self.action_toggle_hidden = _make_action(
            "toggle_hidden", "action.toggle_hidden", "Versteckte Dateien anzeigen"
        )
        self.action_toggle_hidden.setCheckable(True)
        self.action_toggle_hidden.setChecked(False)
        self.action_toggle_hidden.toggled.connect(self._on_toggle_hidden_files)

        self.action_undo = _make_action("undo", "action.undo", "Rückgängig")
        self.action_undo.setShortcut(QKeySequence("Ctrl+Z"))
        self.action_undo.setEnabled(False)
        self.action_undo.triggered.connect(self._on_undo)

        self.action_redo = _make_action("redo", "action.redo", "Wiederholen")
        self.action_redo.setShortcut(QKeySequence("Ctrl+Y"))
        self.action_redo.setEnabled(False)
        self.action_redo.triggered.connect(self._on_redo)

        self.action_new_folder = _make_action(
            "new_folder", "toolbar.new_folder", "Neuer Ordner …"
        )
        self.action_new_folder.setShortcut(QKeySequence("F7"))
        self.action_new_folder.triggered.connect(self._on_new_folder)

        self.action_copy = _make_action("copy", "toolbar.copy", "Kopieren …")
        self.action_copy.setShortcut(QKeySequence("F5"))
        self.action_copy.triggered.connect(self._on_copy_selected)

        self.action_move = _make_action("move", "toolbar.move", "Verschieben …")
        self.action_move.setShortcut(QKeySequence("F6"))
        self.action_move.triggered.connect(self._on_move_selected)

        self.action_delete = _make_action("delete", "toolbar.delete", "Löschen …")
        self.action_delete.setShortcut(QKeySequence("F8"))
        self.action_delete.triggered.connect(self._on_delete_selected)

        self.action_edit = _make_action("editor", "action.editor", "Editor")
        self.action_edit.setShortcut(QKeySequence("F4"))
        self.action_edit.triggered.connect(self._on_edit_selected)

        self.action_rename = _make_action("rename", "toolbar.rename", "Umbenennen …")
        self.action_rename.setShortcut(QKeySequence("F2"))
        self.action_rename.triggered.connect(self._on_rename_selected)

        self.action_properties = _make_action(
            "properties", "toolbar.properties", "Eigenschaften …"
        )
        self.action_properties.setShortcut(QKeySequence("Alt+Return"))
        self.action_properties.triggered.connect(self._on_show_properties)

        self.action_preview = _make_action("preview", "action.preview", "Vorschau")
        self.action_preview.setShortcut(QKeySequence("F3"))
        self.action_preview.triggered.connect(self._on_show_preview)

        self.action_search = _make_action("search", "action.search", "Suchen …")
        self.action_search.setShortcut(QKeySequence("Ctrl+F"))
        self.action_search.triggered.connect(self._on_open_search)

        self.action_favorites = _make_action("favorites", "action.favorites", "Favoriten …")
        self.action_favorites.setShortcut(QKeySequence("Ctrl+D"))
        self.action_favorites.triggered.connect(self._on_open_favorites)

        self.action_bulk_rename = _make_action(
            "rename", "dialog.rename.title", "Massenumbenennung …"
        )
        self.action_bulk_rename.triggered.connect(self._on_open_bulk_rename)

        self.action_hash_tools = _make_action("hash", "dialog.hash.title", "Hash-Werkzeuge …")
        self.action_hash_tools.triggered.connect(self._on_open_hash_tools)

        self.action_compare = _make_action("compare", "action.compare", "Ordner vergleichen …")
        self.action_compare.triggered.connect(self._on_open_compare)

        self.action_create_archive = _make_action(
            "archive_create", "action.archive_create", "Archiv erstellen …"
        )
        self.action_create_archive.triggered.connect(self._on_create_archive)

        self.action_extract_archive = _make_action(
            "archive_extract", "action.archive_extract", "Archiv entpacken …"
        )
        self.action_extract_archive.triggered.connect(self._on_extract_archive)

        self.action_connections = _make_action(
            "network", "dialog.connections.title", "Verbindungsmanager …"
        )
        self.action_connections.triggered.connect(self._on_open_connections)

        self.action_terminal = _make_action("terminal", "toolbar.terminal", "Terminal")
        self.action_terminal.setShortcut(QKeySequence("Ctrl+T"))
        self.action_terminal.triggered.connect(self._on_open_terminal)

        self.action_operations_queue = _make_action(
            "queue", "action.operations_queue", "Warteschlange …"
        )
        self.action_operations_queue.triggered.connect(self._on_toggle_operations_queue)

        self.action_dashboard = _make_action(
            "dashboard", "action.dashboard", "Dashboard …"
        )
        self.action_dashboard.triggered.connect(self._on_open_dashboard)

        self.action_settings = _make_action(
            "settings", "dialog.settings.title", "Einstellungen …"
        )
        self.action_settings.triggered.connect(self._on_open_settings)

        self.action_about = _make_action(
            "app_icon", "action.about", "Über Pandora® Commander"
        )
        self.action_about.triggered.connect(self._on_show_about_dialog)

        self.action_check_updates = _make_action(
            "update", "action.check_updates", "Nach Updates suchen …"
        )
        self.action_check_updates.triggered.connect(self._on_check_for_updates_manually)

        self.action_plugin_manager = _make_action(
            "plugin", "action.plugin_manager", "Plugin-Manager …"
        )
        self.action_plugin_manager.triggered.connect(self._on_open_plugin_manager)

    def _setup_menu_bar(self) -> None:
        """Baut die Menüleiste gemäß Vorgabe (Datei, Bearbeiten, Ansicht,
        Extras, Netzwerk, Werkzeuge, Einstellungen, Hilfe).

        Die Menütitel werden ebenfalls über ``self._translatable_menus``
        vermerkt, damit ``_retranslate_ui()`` sie nach einem
        Sprachwechsel aktualisieren kann.
        """
        menu_bar = self.menuBar()
        self._translatable_menus: list[tuple[QAction, str, str]] = []

        def _add_menu(key: str, fallback: str):
            menu = menu_bar.addMenu(fallback)
            self._translatable_menus.append((menu.menuAction(), key, fallback))
            return menu

        file_menu = _add_menu("menu.file", "&Datei")
        file_menu.addAction(self.action_new_folder)
        file_menu.addAction(self.action_properties)
        file_menu.addSeparator()
        file_menu.addAction(self.action_create_archive)
        file_menu.addAction(self.action_extract_archive)
        file_menu.addSeparator()
        file_menu.addAction(self.action_quit)

        edit_menu = _add_menu("menu.edit", "&Bearbeiten")
        edit_menu.addAction(self.action_undo)
        edit_menu.addAction(self.action_redo)
        edit_menu.addSeparator()
        edit_menu.addAction(self.action_copy)
        edit_menu.addAction(self.action_move)
        edit_menu.addAction(self.action_delete)
        edit_menu.addAction(self.action_rename)
        edit_menu.addSeparator()
        edit_menu.addAction(self.action_edit)
        edit_menu.addAction(self.action_bulk_rename)

        view_menu = _add_menu("menu.view", "&Ansicht")
        view_menu.addAction(self.action_refresh)
        view_menu.addAction(self.action_toggle_hidden)
        view_menu.addAction(self.action_switch_panel)
        view_menu.addAction(self.action_preview)

        extras_menu = _add_menu("menu.tools", "Ex&tras")
        extras_menu.addAction(self.action_search)
        extras_menu.addAction(self.action_favorites)
        extras_menu.addAction(self.action_terminal)
        extras_menu.addAction(self.action_operations_queue)
        extras_menu.addSeparator()
        extras_menu.addAction(self.action_dashboard)

        network_menu = _add_menu("menu.network", "&Netzwerk")
        network_menu.addAction(self.action_connections)

        tools_menu = _add_menu("menu.utilities", "&Werkzeuge")
        tools_menu.addAction(self.action_hash_tools)
        tools_menu.addAction(self.action_compare)

        self._plugins_menu = _add_menu("menu.plugins", "&Plugins")
        self._plugins_menu.addAction(self.action_plugin_manager)
        self._plugins_menu_separator = self._plugins_menu.addSeparator()
        self._populate_plugins_menu()

        settings_menu = _add_menu("menu.settings", "&Einstellungen")
        settings_menu.addAction(self.action_settings)

        help_menu = _add_menu("menu.help", "&Hilfe")
        help_menu.addAction(self.action_check_updates)
        help_menu.addSeparator()
        help_menu.addAction(self.action_about)

    def _setup_toolbar(self) -> None:
        """Baut die Symbolleiste mit den aktuell funktionsfähigen Aktionen."""
        toolbar = QToolBar("Hauptsymbolleiste", self)
        toolbar.setMovable(False)
        toolbar.setIconSize(QSize(_TOOLBAR_ICON_SIZE, _TOOLBAR_ICON_SIZE))
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        toolbar.addAction(self.action_refresh)
        toolbar.addAction(self.action_toggle_hidden)
        toolbar.addSeparator()
        toolbar.addAction(self.action_undo)
        toolbar.addAction(self.action_redo)
        toolbar.addSeparator()
        toolbar.addAction(self.action_new_folder)
        toolbar.addAction(self.action_copy)
        toolbar.addAction(self.action_move)
        toolbar.addAction(self.action_delete)
        toolbar.addAction(self.action_rename)
        toolbar.addAction(self.action_properties)
        toolbar.addSeparator()
        toolbar.addAction(self.action_edit)
        toolbar.addAction(self.action_preview)
        toolbar.addSeparator()
        toolbar.addAction(self.action_search)
        toolbar.addAction(self.action_favorites)
        toolbar.addAction(self.action_terminal)
        toolbar.addAction(self.action_operations_queue)
        toolbar.addSeparator()
        toolbar.addAction(self.action_switch_panel)
        self._plugin_toolbar_separator = toolbar.addSeparator()
        self.addToolBar(toolbar)
        self._toolbar = toolbar

    # ------------------------------------------------------------------
    # Panel-Signale verbinden
    # ------------------------------------------------------------------

    def _connect_panel_signals(self) -> None:
        """Verbindet beide Panels mit der Statusleiste und dem Plugin-System."""
        for panel in (self._left_panel, self._right_panel):
            panel.status_message.connect(self._status_bar.showMessage)
            panel.path_activated.connect(lambda _path: self._update_path_status_label())
            panel.path_activated.connect(
                lambda path, bound_panel=panel: self._plugin_manager.notify_panel_directory_changed(
                    bound_panel, path
                )
            )
            panel.file_activated.connect(self._on_file_activated)
            panel.context_menu_requested.connect(
                lambda pos, bound_panel=panel: self._on_panel_context_menu_requested(
                    bound_panel, pos
                )
            )
            panel.drop_requested.connect(self._on_panel_drop)

    # ------------------------------------------------------------------
    # Aktives Panel (Fokus-Tracking)
    # ------------------------------------------------------------------

    def _install_focus_tracking(self) -> None:
        """Installiert Event-Filter, um das zuletzt fokussierte Panel zu erkennen."""
        self._left_panel.installEventFilter(self)
        self._right_panel.installEventFilter(self)
        for child in self._left_panel.findChildren(QWidget):
            child.installEventFilter(self)
        for child in self._right_panel.findChildren(QWidget):
            child.installEventFilter(self)

    def eventFilter(self, watched: QWidget, event: QEvent) -> bool:  # noqa: N802
        """Erkennt Fokuswechsel, um das aktive Panel zu bestimmen."""
        if event.type() == QEvent.Type.FocusIn:
            if self._is_descendant_of(watched, self._left_panel):
                self._set_active_panel(self._left_panel)
            elif self._is_descendant_of(watched, self._right_panel):
                self._set_active_panel(self._right_panel)
        return super().eventFilter(watched, event)

    @staticmethod
    def _is_descendant_of(widget: QWidget, ancestor: QWidget) -> bool:
        """Prüft, ob widget dasselbe Objekt wie ancestor ist oder darunter liegt."""
        current: QWidget | None = widget
        while current is not None:
            if current is ancestor:
                return True
            current = current.parent()
        return False

    def _set_active_panel(self, panel: FilePanel) -> None:
        """Setzt das aktive Panel und aktualisiert die Statusleiste."""
        if self._active_panel is panel:
            return
        self._active_panel = panel
        self._update_path_status_label()

    @property
    def active_panel(self) -> FilePanel:
        """Das Panel, das zuletzt den Fokus hatte (Standard: links)."""
        return self._active_panel

    @property
    def inactive_panel(self) -> FilePanel:
        """Das jeweils andere Panel – Standardziel für Kopieren/Verschieben."""
        return self._right_panel if self._active_panel is self._left_panel else self._left_panel

    def _on_switch_active_panel(self) -> None:
        """Wechselt den Eingabefokus zwischen linkem und rechtem Panel."""
        target = self._right_panel if self._active_panel is self._left_panel else self._left_panel
        target.setFocus()
        self._set_active_panel(target)

    # ------------------------------------------------------------------
    # Aktion-Handler: Ansicht
    # ------------------------------------------------------------------

    def _on_refresh_active_panel(self) -> None:
        """Lädt das aktive Panel neu ein."""
        self._active_panel.refresh()
        self._status_bar.showMessage("Aktualisiert.", 2000)

    def _on_toggle_hidden_files(self, checked: bool) -> None:
        """Schaltet die Anzeige versteckter Dateien für beide Panels um."""
        self._left_panel.model.set_show_hidden(checked)
        self._right_panel.model.set_show_hidden(checked)

    def _on_show_about_dialog(self) -> None:
        """Zeigt einen einfachen Über-Dialog an."""
        QMessageBox.about(
            self,
            f"Über {APP_NAME}",
            f"{APP_NAME} {APP_VERSION}\n\n"
            "Ein moderner, zweispaltiger Dateimanager im Stil von\n"
            "Total Commander, Double Commander und Krusader.",
        )

    # ------------------------------------------------------------------
    # Aktion-Handler: Update-Prüfung
    # ------------------------------------------------------------------

    def _on_check_for_updates_manually(self) -> None:
        """Startet eine manuelle Update-Prüfung (mit sichtbarer Rückmeldung)."""
        self._start_update_check(silent=False)

    def _start_update_check(self, silent: bool) -> None:
        """Startet die Update-Prüfung im Hintergrund-Thread.

        Args:
            silent: Bei True (automatischer Start-Check) wird nur bei
                einem tatsächlich gefundenen Update etwas angezeigt.
                Bei False (manuell über das Hilfe-Menü ausgelöst) wird
                zusätzlich "kein Update verfügbar" bzw. ein
                Fehlerhinweis angezeigt, damit der Nutzer eine
                Rückmeldung auf seinen Klick erhält.
        """
        if self._update_worker is not None and self._update_worker.isRunning():
            return

        if not self._settings.update_check_url:
            if not silent:
                QMessageBox.information(
                    self,
                    "Nach Updates suchen",
                    "Es ist keine Update-URL konfiguriert.\n\n"
                    "Trage sie unter Einstellungen -> Allgemein -> "
                    "Update-URL ein, um automatisch auf neue Versionen "
                    "prüfen zu können.",
                )
            return

        worker = UpdateCheckWorker(
            current_version=APP_VERSION,
            manifest_url=self._settings.update_check_url,
            parent=self,
        )
        worker.update_available.connect(
            lambda info: self._on_update_available(info, silent=silent)
        )
        worker.no_update_found.connect(lambda: self._on_no_update_found(silent=silent))
        worker.check_failed.connect(lambda msg: self._on_update_check_failed(msg, silent=silent))
        self._update_worker = worker
        worker.start()

    def _on_update_available(self, info: UpdateInfo, silent: bool) -> None:
        """Reagiert auf ein gefundenes Update: Dialog + Benachrichtigung."""
        self._notifications.notify_success(
            "Update verfügbar", f"Version {info.version} steht zum Download bereit."
        )
        dialog = UpdateAvailableDialog(
            current_version=APP_VERSION, update_info=info, parent=self
        )
        dialog.exec()

    def _on_no_update_found(self, silent: bool) -> None:
        """Reagiert darauf, dass bereits die neueste Version installiert ist."""
        if silent:
            return
        QMessageBox.information(
            self,
            "Nach Updates suchen",
            f"{APP_NAME} ist aktuell (Version {APP_VERSION}).",
        )

    def _on_update_check_failed(self, message: str, silent: bool) -> None:
        """Reagiert auf eine fehlgeschlagene Update-Prüfung."""
        logger.warning("Update-Prüfung fehlgeschlagen: %s", message)
        if silent:
            return
        QMessageBox.warning(self, "Nach Updates suchen", message)

    def _update_path_status_label(self) -> None:
        """Zeigt den Pfad des aktiven Panels dauerhaft in der Statusleiste an."""
        self._path_status_label.setText(f"Aktiv: {self._active_panel.current_directory}")

    # ------------------------------------------------------------------
    # Aktion-Handler: Neuer Ordner
    # ------------------------------------------------------------------

    def _on_new_folder(self) -> None:
        """Fragt einen Ordnernamen ab und legt ihn im aktiven Panel an."""
        name, confirmed = QInputDialog.getText(
            self,
            "Neuer Ordner",
            "Name des neuen Ordners:",
            QLineEdit.EchoMode.Normal,
            "",
        )
        if not confirmed or not name.strip():
            return

        target = self._active_panel.current_directory / name.strip()
        try:
            target.mkdir(parents=False, exist_ok=False)
        except FileExistsError:
            QMessageBox.warning(
                self, "Neuer Ordner", f'Ein Eintrag namens "{name.strip()}" existiert bereits.'
            )
            return
        except OSError as error:
            QMessageBox.critical(
                self, "Neuer Ordner", f"Der Ordner konnte nicht angelegt werden:\n{error}"
            )
            return

        self._active_panel.refresh()
        self._undo_manager.push(NewFolderAction(target))
        self._status_bar.showMessage(f'Ordner "{name.strip()}" angelegt.', 3000)

    # ------------------------------------------------------------------
    # Aktion-Handler: Editor
    # ------------------------------------------------------------------

    def _on_edit_selected(self) -> None:
        """Öffnet die erste markierte Datei des aktiven Panels im Editor."""
        entries = self._active_panel.selected_entries()
        files = [entry for entry in entries if not entry.is_directory]

        if not files:
            self._status_bar.showMessage("Keine Datei zum Bearbeiten ausgewählt.", 3000)
            return
        if len(files) > 1:
            self._status_bar.showMessage(
                "Bitte nur eine Datei zum Bearbeiten auswählen.", 3000
            )
            return

        self._open_in_editor(files[0].path)

    def _on_file_activated(self, path: Path) -> None:
        """Reagiert auf Doppelklick einer Datei im Panel.

        Archive werden – wie im Lastenheft gefordert ("Archive sollen
        wie Ordner geöffnet werden können") – im ArchiveBrowserDialog
        navigierbar geöffnet statt im Texteditor; alle übrigen
        Dateien landen weiterhin im eingebauten Editor.

        Args:
            path: Per Doppelklick aktivierte Datei.
        """
        if is_archive(path):
            dialog = ArchiveBrowserDialog(archive_path=path, parent=self)
            dialog.exec()
            return
        self._open_in_editor(path)

    def _open_in_editor(self, path: Path) -> None:
        """Öffnet eine Datei in einem neuen, eigenständigen Editor-Fenster.

        Args:
            path: Zu öffnende Datei.
        """
        editor_window = EditorWindow(path=path, parent=None)
        editor_window.destroyed.connect(lambda: self._forget_editor_window(editor_window))
        self._open_editor_windows.append(editor_window)
        editor_window.show()
        editor_window.raise_()
        editor_window.activateWindow()

    def _forget_editor_window(self, editor_window: EditorWindow) -> None:
        """Entfernt ein geschlossenes Editor-Fenster aus der internen Liste."""
        if editor_window in self._open_editor_windows:
            self._open_editor_windows.remove(editor_window)

    # ------------------------------------------------------------------
    # Aktion-Handler: Kopieren / Verschieben
    # ------------------------------------------------------------------

    def _on_copy_selected(self) -> None:
        """Kopiert die Auswahl des aktiven Panels in das jeweils andere Panel."""
        self._run_copy_or_move(OperationType.COPY)

    def _on_move_selected(self) -> None:
        """Verschiebt die Auswahl des aktiven Panels in das jeweils andere Panel."""
        self._run_copy_or_move(OperationType.MOVE)

    def _run_copy_or_move(self, operation: OperationType) -> None:
        """Gemeinsame Logik für Kopieren und Verschieben.

        Quelle ist stets das aktive Panel, Ziel das jeweils andere.
        Bei Namenskollisionen im Ziel wird vorab interaktiv nachgefragt,
        wie verfahren werden soll (Überschreiben/Überspringen/
        Umbenennen), statt den Hintergrund-Worker mit einer festen
        Standard-Policy blind laufen zu lassen.

        Args:
            operation: OperationType.COPY oder OperationType.MOVE.
        """
        source_panel = self._active_panel
        destination_panel = self.inactive_panel
        sources = source_panel.selected_paths()

        if not sources:
            self._status_bar.showMessage("Keine Auswahl zum Kopieren/Verschieben.", 3000)
            return

        destination = destination_panel.current_directory
        if destination == source_panel.current_directory:
            QMessageBox.information(
                self,
                "Gleiches Verzeichnis",
                "Quelle und Ziel sind identisch. Bitte im jeweils anderen "
                "Panel in ein anderes Verzeichnis wechseln.",
            )
            return

        self._execute_copy_or_move(operation, sources, destination)
        source_panel.clear_selection()

    def _on_panel_drop(self, sources: list[Path], destination: Path, move: bool) -> None:
        """Reagiert auf per Drag&Drop auf einem Panel abgelegte Dateien.

        Args:
            sources: Quellpfade der gezogenen Dateien/Ordner.
            destination: Zielverzeichnis (das Panel bzw. der
                Unterordner, auf dem abgelegt wurde).
            move: True, wenn verschoben statt kopiert werden soll
                (siehe _FilePanelTableView für die Regeln dazu).
        """
        if not sources:
            return

        operation = OperationType.MOVE if move else OperationType.COPY
        self._execute_copy_or_move(operation, sources, destination)

    def _execute_copy_or_move(
        self, operation: OperationType, sources: list[Path], destination: Path
    ) -> None:
        """Reiht Kopieren/Verschieben in die Operations-Warteschlange ein.

        Gemeinsam genutzt von der menü-/tastaturgesteuerten Kopieren-
        /Verschieben-Aktion (Quelle/Ziel = aktives/inaktives Panel)
        und vom Drag&Drop-Handler (Quelle/Ziel aus dem Drop-Ereignis).

        Die Namenskollisionsabfrage läuft weiterhin synchron auf dem
        UI-Thread (sie ist kurz und muss dem Nutzer VOR dem Start
        vorgelegt werden). Die eigentliche Datei-Ein-/Ausgabe läuft
        über den OperationQueueManager im Hintergrund und kann sich
        mit weiteren, parallel gestarteten Operationen überschneiden
        (siehe app.core.filesystem.operation_queue). Panel-Aktualisierung,
        Benachrichtigung und Undo-Eintrag erfolgen erst, sobald die
        Operation tatsächlich abgeschlossen ist (siehe
        _on_queue_job_finished).

        Args:
            operation: OperationType.COPY oder OperationType.MOVE.
            sources: Zu kopierende/verschiebende Pfade.
            destination: Zielverzeichnis.
        """
        collision_policy = CollisionPolicy.RENAME
        colliding = find_existing_collisions(sources, destination)
        if colliding:
            names = ", ".join(path.name for path in colliding[:5])
            if len(colliding) > 5:
                names += f" … (+{len(colliding) - 5} weitere)"
            policy = self._ask_collision_policy(names)
            if policy is None:
                return
            collision_policy = policy

        verb = "Kopiere" if operation == OperationType.COPY else "Verschiebe"
        worker = FileOperationWorker(
            operation=operation,
            sources=sources,
            destination=destination,
            collision_policy=collision_policy,
        )

        def _on_finished(result: OperationResult) -> None:
            self._left_panel.refresh()
            self._right_panel.refresh()
            self._status_bar.showMessage(result.summary_text().capitalize(), 5000)
            self._notify_operation_result(f"{verb} abgeschlossen", result)
            if result.succeeded_pairs:
                action_cls = CopyAction if operation == OperationType.COPY else MoveAction
                self._undo_manager.push(action_cls(result.succeeded_pairs))
                if operation == OperationType.MOVE:
                    self._tags_manager.rename_many(result.succeeded_pairs)
                    self._left_panel.refresh_decorations()
                    self._right_panel.refresh_decorations()

        title = f"{verb} {len(sources)} Element(e)"
        job_id = self._operation_queue.enqueue(title=title, worker=worker)
        self._job_completion_callbacks[job_id] = _on_finished
        self._status_bar.showMessage(f"{title} zur Warteschlange hinzugefügt.", 3000)
        self._show_operations_queue_dialog()

    def _notify_operation_result(self, title: str, result: OperationResult) -> None:
        """Zeigt nach einer abgeschlossenen Hintergrundoperation eine
        native Desktop-Benachrichtigung an (sofern in den Einstellungen
        aktiviert).

        Die Benachrichtigungsart (Erfolg/Warnung/Fehler) richtet sich
        danach, ob die Operation fehlerfrei, mit Fehlern oder durch
        den Nutzer abgebrochen abgeschlossen wurde.

        Args:
            title: Kurzer Titel der Benachrichtigung (z. B. "Kopieren
                abgeschlossen").
            result: Das Ergebnis der abgeschlossenen Operation.
        """
        message = result.summary_text().capitalize()
        if result.cancelled:
            self._notifications.notify_warning(title, message)
        elif result.has_errors:
            self._notifications.notify_error(title, message)
        else:
            self._notifications.notify_success(title, message)

    def _ask_collision_policy(self, colliding_names: str) -> CollisionPolicy | None:
        """Fragt den Nutzer, wie mit bereits vorhandenen Zieleinträgen verfahren wird.

        Args:
            colliding_names: Für die Anzeige aufbereitete Liste
                kollidierender Namen.

        Returns:
            Die gewählte CollisionPolicy, oder None, wenn der Nutzer
            die gesamte Operation abgebrochen hat.
        """
        box = QMessageBox(self)
        box.setWindowTitle("Name bereits vorhanden")
        box.setIcon(QMessageBox.Icon.Question)
        box.setText(
            f"Folgende Einträge existieren im Ziel bereits:\n{colliding_names}\n\n"
            "Wie soll verfahren werden?"
        )
        overwrite_button = box.addButton("Überschreiben", QMessageBox.ButtonRole.YesRole)
        rename_button = box.addButton("Umbenennen", QMessageBox.ButtonRole.YesRole)
        skip_button = box.addButton("Überspringen", QMessageBox.ButtonRole.NoRole)
        box.addButton("Abbrechen", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(rename_button)
        box.exec()

        clicked = box.clickedButton()
        if clicked is overwrite_button:
            return CollisionPolicy.OVERWRITE
        if clicked is rename_button:
            return CollisionPolicy.RENAME
        if clicked is skip_button:
            return CollisionPolicy.SKIP
        return None

    # ------------------------------------------------------------------
    # Aktion-Handler: Löschen
    # ------------------------------------------------------------------

    def _on_delete_selected(self) -> None:
        """Löscht die Auswahl des aktiven Panels, ggf. nach Rückfrage."""
        panel = self._active_panel
        sources = panel.selected_paths()

        if not sources:
            self._status_bar.showMessage("Keine Auswahl zum Löschen.", 3000)
            return

        if self._settings.confirm_delete:
            names = ", ".join(path.name for path in sources[:5])
            if len(sources) > 5:
                names += f" … (+{len(sources) - 5} weitere)"
            answer = QMessageBox.question(
                self,
                "Löschen bestätigen",
                f"{len(sources)} Element(e) löschen?\n\n{names}\n\n"
                "(kann anschließend mit Strg+Z rückgängig gemacht werden)",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

        worker = FileOperationWorker(
            operation=OperationType.DELETE_TO_TRASH,
            sources=sources,
            destination=self._undo_manager.trash_root,
        )

        def _on_finished(result: OperationResult) -> None:
            panel.refresh()
            self._status_bar.showMessage(result.summary_text().capitalize(), 5000)
            self._notify_operation_result("Löschen abgeschlossen", result)
            if result.succeeded_pairs:
                self._undo_manager.push(
                    DeleteAction(result.succeeded_pairs, self._undo_manager.trash_root)
                )

        title = f"Lösche {len(sources)} Element(e)"
        job_id = self._operation_queue.enqueue(title=title, worker=worker)
        self._job_completion_callbacks[job_id] = _on_finished
        self._status_bar.showMessage(f"{title} zur Warteschlange hinzugefügt.", 3000)
        self._show_operations_queue_dialog()
        panel.clear_selection()

    # ------------------------------------------------------------------
    # Aktion-Handler: Umbenennen
    # ------------------------------------------------------------------

    def _on_rename_selected(self) -> None:
        """Benennt den einzelnen markierten Eintrag des aktiven Panels um."""
        entries = self._active_panel.selected_entries()
        if not entries:
            self._status_bar.showMessage("Kein Eintrag zum Umbenennen ausgewählt.", 3000)
            return
        if len(entries) > 1:
            self._status_bar.showMessage("Bitte nur einen Eintrag zum Umbenennen auswählen.", 3000)
            return

        entry = entries[0]
        new_name, confirmed = QInputDialog.getText(
            self, "Umbenennen", "Neuer Name:", QLineEdit.EchoMode.Normal, entry.path.name
        )
        if not confirmed or not new_name.strip() or new_name.strip() == entry.path.name:
            return

        target = entry.path.with_name(new_name.strip())
        if target.exists():
            QMessageBox.warning(
                self, "Umbenennen", f'Ein Eintrag namens "{new_name.strip()}" existiert bereits.'
            )
            return
        try:
            entry.path.rename(target)
        except OSError as error:
            QMessageBox.critical(self, "Umbenennen", f"Umbenennen fehlgeschlagen:\n{error}")
            return

        self._active_panel.refresh()
        self._undo_manager.push(RenameAction(entry.path, target))
        self._tags_manager.rename(entry.path, target)
        self._left_panel.refresh_decorations()
        self._right_panel.refresh_decorations()
        self._status_bar.showMessage(f'Umbenannt in "{new_name.strip()}".', 3000)

    # ------------------------------------------------------------------
    # Aktion-Handler: Rückgängig / Wiederholen
    # ------------------------------------------------------------------

    def _on_undo(self) -> None:
        """Macht die zuletzt ausgeführte Datei-Aktion rückgängig."""
        try:
            description = self._undo_manager.undo()
        except IndexError:
            return
        except OSError as error:
            QMessageBox.critical(
                self, "Rückgängig machen", f"Rückgängig machen fehlgeschlagen:\n{error}"
            )
            return

        self._left_panel.refresh()
        self._right_panel.refresh()
        self._status_bar.showMessage(f"Rückgängig gemacht: {description}", 4000)

    def _on_redo(self) -> None:
        """Wiederholt die zuletzt rückgängig gemachte Datei-Aktion."""
        try:
            description = self._undo_manager.redo()
        except IndexError:
            return
        except OSError as error:
            QMessageBox.critical(self, "Wiederholen", f"Wiederholen fehlgeschlagen:\n{error}")
            return

        self._left_panel.refresh()
        self._right_panel.refresh()
        self._status_bar.showMessage(f"Wiederholt: {description}", 4000)

    def _update_undo_redo_actions(self) -> None:
        """Aktualisiert aktivierten Zustand und Tooltip von Rückgängig/Wiederholen."""
        self.action_undo.setEnabled(self._undo_manager.can_undo)
        self.action_redo.setEnabled(self._undo_manager.can_redo)

        undo_description = self._undo_manager.undo_description
        self.action_undo.setToolTip(
            f"Rückgängig: {undo_description} (Strg+Z)" if undo_description else "Rückgängig (Strg+Z)"
        )
        redo_description = self._undo_manager.redo_description
        self.action_redo.setToolTip(
            f"Wiederholen: {redo_description} (Strg+Y)" if redo_description else "Wiederholen (Strg+Y)"
        )

    # ------------------------------------------------------------------
    # Aktion-Handler: Eigenschaften
    # ------------------------------------------------------------------

    def _on_show_properties(self) -> None:
        """Öffnet den Eigenschaften-Dialog für die Auswahl des aktiven Panels."""
        entries = self._active_panel.selected_entries()
        if not entries:
            self._status_bar.showMessage("Keine Auswahl für Eigenschaften.", 3000)
            return

        paths = [entry.path for entry in entries]
        dialog = PropertiesDialog(paths=paths, parent=self)
        dialog.exec()

    # ------------------------------------------------------------------
    # Aktion-Handler: Vorschau
    # ------------------------------------------------------------------

    def _on_show_preview(self) -> None:
        """Zeigt eine Vorschau des ersten markierten Eintrags im aktiven Panel."""
        entries = self._active_panel.selected_entries()
        if not entries:
            self._status_bar.showMessage("Keine Auswahl für die Vorschau.", 3000)
            return

        path = entries[0].path
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Vorschau – {path.name}")
        dialog.resize(720, 640)
        layout = QVBoxLayout(dialog)
        preview = PreviewWidget(dialog)
        preview.show_path(path)
        layout.addWidget(preview)
        dialog.exec()

    # ------------------------------------------------------------------
    # Aktion-Handler: Suche
    # ------------------------------------------------------------------

    def _on_open_search(self) -> None:
        """Öffnet den Suchdialog, gestartet im Verzeichnis des aktiven Panels."""
        dialog = SearchDialog(start_path=self._active_panel.current_directory, parent=self)
        dialog.path_activated.connect(self._navigate_active_panel_to)
        dialog.show()
        self._open_terminal_windows.append(dialog)
        dialog.destroyed.connect(lambda: self._forget_window(dialog))

    def _navigate_active_panel_to(self, path: Path) -> None:
        """Navigiert das aktive Panel zu einem Pfad (Datei: übergeordneter Ordner)."""
        target = path if path.is_dir() else path.parent
        self._active_panel.navigate_to(target)

    # ------------------------------------------------------------------
    # Aktion-Handler: Favoriten
    # ------------------------------------------------------------------

    def _on_open_favorites(self) -> None:
        """Öffnet die Favoritenverwaltung für das aktive Panel."""
        dialog = FavoritesDialog(
            favorites_manager=self._favorites_manager,
            current_path=self._active_panel.current_directory,
            parent=self,
        )
        dialog.path_activated.connect(self._navigate_active_panel_to)
        dialog.exec()

    # ------------------------------------------------------------------
    # Aktion-Handler: Massenumbenennung, Hash-Werkzeuge, Vergleich
    # ------------------------------------------------------------------

    def _on_open_bulk_rename(self) -> None:
        """Öffnet die Massenumbenennung für die Auswahl des aktiven Panels."""
        paths = self._active_panel.selected_paths()
        if not paths:
            self._status_bar.showMessage("Keine Auswahl für Massenumbenennung.", 3000)
            return
        dialog = BulkRenameDialog(paths=paths, parent=self)
        dialog.exec()
        self._active_panel.refresh()

    def _on_open_hash_tools(self) -> None:
        """Öffnet die Hash-Werkzeuge für die Auswahl des aktiven Panels."""
        paths = self._active_panel.selected_paths()
        if not paths:
            self._status_bar.showMessage("Keine Auswahl für Hash-Werkzeuge.", 3000)
            return
        dialog = HashDialog(paths=paths, parent=self)
        dialog.exec()

    def _on_open_compare(self) -> None:
        """Öffnet den Ordnervergleich zwischen linkem und rechtem Panel."""
        dialog = CompareDialog(
            left_root=self._left_panel.current_directory,
            right_root=self._right_panel.current_directory,
            parent=self,
        )
        dialog.exec()

    # ------------------------------------------------------------------
    # Aktion-Handler: Archivverwaltung
    # ------------------------------------------------------------------

    def _on_create_archive(self) -> None:
        """Erstellt ein Archiv aus der Auswahl des aktiven Panels."""
        sources = self._active_panel.selected_paths()
        if not sources:
            self._status_bar.showMessage("Keine Auswahl zum Archivieren.", 3000)
            return

        format_choices = {
            "ZIP (*.zip)": ("zip", ".zip"),
            "TAR (*.tar)": ("tar", ".tar"),
            "TAR.GZ (*.tar.gz)": ("tar.gz", ".tar.gz"),
            "TAR.BZ2 (*.tar.bz2)": ("tar.bz2", ".tar.bz2"),
            "7Z (*.7z)": ("7z", ".7z"),
        }
        default_name = str(
            self._active_panel.current_directory / f"{sources[0].stem or sources[0].name}.zip"
        )
        target, chosen_filter = QFileDialog.getSaveFileName(
            self, "Archiv erstellen", default_name, ";;".join(format_choices)
        )
        if not target:
            return

        archive_format, suffix = format_choices.get(chosen_filter, ("zip", ".zip"))
        destination = Path(target)
        if destination.suffix == "" and not target.endswith(suffix):
            destination = Path(target + suffix)

        try:
            create_archive(sources=sources, destination=destination, archive_format=archive_format)
        except ArchiveError as error:
            QMessageBox.critical(self, "Archiv erstellen", str(error))
            return

        self._active_panel.refresh()
        self._status_bar.showMessage(f'Archiv "{destination.name}" erstellt.', 4000)

    def _on_extract_archive(self) -> None:
        """Entpackt das markierte Archiv des aktiven Panels in einen Zielordner."""
        entries = self._active_panel.selected_entries()
        archives = [entry.path for entry in entries if is_archive(entry.path)]

        if not archives:
            self._status_bar.showMessage("Kein Archiv ausgewählt.", 3000)
            return

        for archive_path in archives:
            destination = QFileDialog.getExistingDirectory(
                self,
                f'Ziel für "{archive_path.name}" wählen',
                str(self._active_panel.current_directory),
            )
            if not destination:
                continue
            try:
                extract_archive(path=archive_path, destination=Path(destination))
            except ArchiveError as error:
                QMessageBox.critical(
                    self, "Archiv entpacken", f'"{archive_path.name}":\n{error}'
                )
                continue
            self._status_bar.showMessage(
                f'"{archive_path.name}" nach "{destination}" entpackt.', 4000
            )

        self._left_panel.refresh()
        self._right_panel.refresh()

    # ------------------------------------------------------------------
    # Aktion-Handler: Netzwerkverbindungen
    # ------------------------------------------------------------------

    def _on_open_connections(self) -> None:
        """Öffnet den Verbindungsmanager für Netzwerkfreigaben."""
        dialog = ConnectionDialog(connection_manager=self._connection_manager, parent=self)
        dialog.connect_requested.connect(self._on_connect_requested)
        dialog.exec()

    def _on_connect_requested(self, profile: ConnectionProfile) -> None:
        """Testet eine Netzwerkverbindung und meldet das Ergebnis.

        Volle Durchsuchbarkeit von FTP/SFTP/SMB/WebDAV-Freigaben direkt
        in einem FilePanel folgt in einer eigenen Erweiterung; an
        dieser Stelle wird die Verbindung real hergestellt und wieder
        getrennt, um Zugangsdaten und Erreichbarkeit sofort zu prüfen.

        Args:
            profile: Das zu testende Verbindungsprofil.
        """
        client = self._connection_manager.create_client(profile)
        try:
            client.connect()
        except Exception as error:  # noqa: BLE001 - Client-spezifische Exceptions
            QMessageBox.critical(
                self, "Verbindung fehlgeschlagen", f'"{profile.name}":\n{error}'
            )
            return
        finally:
            try:
                client.disconnect()
            except Exception:  # noqa: BLE001
                pass

        QMessageBox.information(
            self, "Verbindung erfolgreich", f'Verbindung zu "{profile.name}" wurde erfolgreich getestet.'
        )
        self._status_bar.showMessage(f'Verbindung zu "{profile.name}" erfolgreich getestet.', 4000)

    # ------------------------------------------------------------------
    # Aktion-Handler: Terminal
    # ------------------------------------------------------------------

    def _on_open_terminal(self) -> None:
        """Öffnet ein Terminal-Fenster im Verzeichnis des aktiven Panels."""
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Terminal – {self._active_panel.current_directory}")
        dialog.resize(900, 560)
        layout = QVBoxLayout(dialog)
        terminal = TerminalWidget(
            working_directory=self._active_panel.current_directory, parent=dialog
        )
        layout.addWidget(terminal)
        dialog.destroyed.connect(lambda: self._forget_window(dialog))
        self._open_terminal_windows.append(dialog)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _forget_window(self, dialog: QDialog) -> None:
        """Entfernt ein geschlossenes, nicht-modales Fenster aus der internen Liste."""
        if dialog in self._open_terminal_windows:
            self._open_terminal_windows.remove(dialog)

    # ------------------------------------------------------------------
    # Aktion-Handler: Operations-Warteschlange
    # ------------------------------------------------------------------

    def _on_queue_job_finished(self, job_id: int, result: object) -> None:
        """Ruft den zu einem abgeschlossenen Warteschlangeneintrag hinterlegten
        Callback auf (Panel-Aktualisierung, Benachrichtigung, Undo-Eintrag).

        Der Callback wurde beim Einreihen der Operation in
        _execute_copy_or_move bzw. _on_delete_selected hinterlegt, da
        die konkrete Nachbereitung je nach Operationsart (Kopieren,
        Verschieben, Löschen) unterschiedlich ist.

        Args:
            job_id: ID des abgeschlossenen Eintrags.
            result: Das zugehörige OperationResult.
        """
        assert isinstance(result, OperationResult)
        callback = self._job_completion_callbacks.pop(job_id, None)
        if callback is not None:
            callback(result)

    def _on_toggle_operations_queue(self) -> None:
        """Öffnet das Warteschlangenfenster oder holt es in den Vordergrund."""
        self._show_operations_queue_dialog()

    def _show_operations_queue_dialog(self) -> None:
        """Zeigt das (nicht-modale, wiederverwendete) Warteschlangenfenster an."""
        if self._operations_queue_dialog is None:
            self._operations_queue_dialog = OperationsQueueDialog(
                queue_manager=self._operation_queue, parent=self
            )
        self._operations_queue_dialog.show()
        self._operations_queue_dialog.raise_()
        self._operations_queue_dialog.activateWindow()

    # ------------------------------------------------------------------
    # Aktion-Handler: Dashboard
    # ------------------------------------------------------------------

    def _on_open_dashboard(self) -> None:
        """Öffnet das (nicht-modale) Dashboard mit Systeminformationen."""
        dialog = DashboardDialog(
            app_name=APP_NAME,
            app_version=APP_VERSION,
            uptime_provider=self._format_uptime,
            watched_paths_provider=self._watched_panel_paths,
            plugin_count_provider=self._plugin_counts,
            parent=self,
        )
        dialog.destroyed.connect(lambda: self._forget_dashboard(dialog))
        self._open_dashboard_dialogs.append(dialog)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _forget_dashboard(self, dialog: DashboardDialog) -> None:
        """Entfernt ein geschlossenes Dashboard-Fenster aus der internen Liste."""
        if dialog in self._open_dashboard_dialogs:
            self._open_dashboard_dialogs.remove(dialog)

    def _format_uptime(self) -> str:
        """Formatiert die bisherige Laufzeit seit Programmstart lesbar."""
        elapsed_seconds = int(time.monotonic() - self._start_time)
        hours, remainder = divmod(elapsed_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours:
            return f"{hours} Std. {minutes} Min."
        if minutes:
            return f"{minutes} Min. {seconds} Sek."
        return f"{seconds} Sek."

    def _watched_panel_paths(self) -> list[Path]:
        """Liefert die aktuell in beiden Panels angezeigten Verzeichnisse."""
        return [self._left_panel.current_directory, self._right_panel.current_directory]

    def _plugin_counts(self) -> tuple[int, int]:
        """Liefert (Anzahl geladener Plugins, Anzahl entdeckter Plugins)."""
        return len(self._plugin_manager.loaded_plugins), len(self._plugin_manager.plugin_infos)

    # ------------------------------------------------------------------
    # Aktion-Handler: Einstellungen
    # ------------------------------------------------------------------

    def _on_open_settings(self) -> None:
        """Öffnet den Einstellungsdialog und übernimmt Änderungen sofort."""
        dialog = SettingsDialog(config_manager=self._config_manager, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._settings = self._config_manager.current()
            self._left_panel.model.set_show_hidden(self.action_toggle_hidden.isChecked())
            self._notifications.set_enabled(self._settings.notifications_enabled)
            self._operation_queue.set_max_concurrent(self._settings.max_concurrent_operations)
            get_translator().set_language(self._settings.language)
            self._retranslate_ui()
            self._status_bar.showMessage(
                tr("status.settings_applied", "Einstellungen übernommen."), 3000
            )

    def _retranslate_ui(self) -> None:
        """Aktualisiert alle Menü-/Aktionstexte live nach einem Sprachwechsel.

        Icons, Shortcuts und Verknüpfungen bleiben unverändert – es
        wird ausschließlich der sichtbare Text neu gesetzt, sodass ein
        Sprachwechsel im Einstellungsdialog sofort greift, ohne dass
        die Anwendung neu gestartet werden muss.
        """
        for action, key, fallback in self._translatable_actions:
            action.setText(tr(key, fallback))
        for menu_action, key, fallback in self._translatable_menus:
            menu_action.setText(tr(key, fallback))

    # ------------------------------------------------------------------
    # Schließen: aktuelle Pfade in die Einstellungen übernehmen
    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:  # noqa: ANN001, N802
        """Entlädt Plugins, räumt den Undo-Papierkorb auf und speichert die
        aktuellen Panel-Pfade beim Schließen.

        Laufen noch Operationen in der Warteschlange, wird vorher
        nachgefragt, ob wirklich beendet werden soll – ein
        stillschweigender Abbruch mitten in einem Kopier-/
        Verschiebevorgang widerspräche der Sicherheits-Vorgabe
        (robuste, fehlertolerante Dateioperationen).
        """
        active = self._operation_queue.active_count()
        pending = self._operation_queue.pending_count()
        if active or pending:
            answer = QMessageBox.question(
                self,
                "Operationen laufen noch",
                f"Es laufen noch {active} Dateioperation(en) ({pending} warten "
                "in der Warteschlange). Trotzdem beenden und abbrechen?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return

            self._operation_queue.cancel_all_pending()
            for job in self._operation_queue.jobs():
                if job.state == QueueItemState.RUNNING:
                    job.worker.cancel()
                    job.worker.wait(3000)

        self._plugin_hot_reloader.stop()
        self._plugin_manager.unload_all()
        self._undo_manager.purge()
        self._notifications.shutdown()
        if self._update_worker is not None and self._update_worker.isRunning():
            self._update_worker.wait(2000)

        self._settings.default_left_path = str(self._left_panel.current_directory)
        self._settings.default_right_path = str(self._right_panel.current_directory)
        try:
            self._config_manager.save(self._settings)
        except OSError:
            logger.warning("Einstellungen konnten beim Beenden nicht gespeichert werden.")
        super().closeEvent(event)
