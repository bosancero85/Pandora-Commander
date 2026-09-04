"""Pandora® Commander – Plugin-System.

Definiert die Basisklasse ``PandoraPlugin``, von der alle Plugins
erben müssen, sowie den ``PluginManager``, der beim Programmstart
automatisch alle Python-Module im Plugin-Verzeichnis lädt, darin nach
PandoraPlugin-Unterklassen sucht und diese instanziiert.

Ein Plugin ist eine einzelne .py-Datei im Ordner app/plugins/installed/
(wird bei Bedarf angelegt), die genau eine Unterklasse von
PandoraPlugin definiert, z. B.:

    from PyQt6.QtGui import QAction
    from app.plugins.plugin_manager import PandoraPlugin

    class MeinPlugin(PandoraPlugin):
        name = "Mein Plugin"
        version = "1.0"
        author = "Max Mustermann"
        description = "Kurze Beschreibung, was das Plugin tut."

        def on_load(self, context: dict) -> None:
            print("Plugin geladen!")

        def register_menu_actions(self, context: dict) -> list[QAction]:
            action = QAction("Mein Menüeintrag", context["main_window"])
            action.triggered.connect(lambda: print("Ausgeführt!"))
            return [action]

Erweiterungspunkte (alle optional, Standardimplementierung tut nichts):
    on_load(context)
        Wird beim (Neu-)Laden aufgerufen. Zugriff auf zentrale
        Anwendungsobjekte über ``context``.
    on_unload()
        Wird vor dem Entladen/Deaktivieren aufgerufen.
    register_menu_actions(context) -> list[QAction]
        Aktionen, die im Menü "Plugins" der Menüleiste erscheinen.
    register_toolbar_actions(context) -> list[QAction]
        Aktionen, die zusätzlich in der Symbolleiste erscheinen.
    build_context_menu_entries(context, selected_paths) -> list[QAction]
        Wird bei jedem Rechtsklick in einem Dateipanel aufgerufen, um
        dynamisch zusätzliche Kontextmenü-Einträge bereitzustellen
        (z. B. abhängig von der aktuellen Auswahl).
    on_panel_directory_changed(context, panel, path)
        Wird aufgerufen, sobald in einem der beiden Panels erfolgreich
        in ein anderes Verzeichnis navigiert wurde (z. B. für
        Ordner-Tagging oder automatische Vorschau-Logik).
    build_settings_widget(context) -> QWidget | None
        Liefert ein eigenes Konfigurations-Widget, das im
        Plugin-Manager-Dialog auf dem eigenen Tab dieses Plugins
        angezeigt wird. None (Standard) bedeutet: kein eigenes
        Einstellungs-Widget, der Tab zeigt dann nur die Metadaten.

Abhängigkeiten zwischen Plugins:
    Ein Plugin kann über das Klassenattribut ``requires`` die
    Anzeigenamen (``name``) anderer Plugins auflisten, von denen es
    abhängt, z. B. ``requires = ["Systeminformationen"]``. Der
    PluginManager sortiert die Ladereihenfolge topologisch, sodass
    Abhängigkeiten stets vor den Plugins geladen werden, die sie
    benötigen. Fehlende Abhängigkeiten oder zyklische Abhängigkeiten
    führen zum kontrollierten Überspringen der betroffenen Plugins
    (mit Fehlermeldung im Plugin-Manager-Dialog), niemals zum Absturz.
"""

from __future__ import annotations

import importlib.util
import sys
import traceback
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.core.logging_setup import get_logger

if TYPE_CHECKING:
    from PyQt6.QtGui import QAction
    from PyQt6.QtWidgets import QWidget

    from app.ui.widgets.file_panel import FilePanel

logger = get_logger(__name__)

#: Unterverzeichnis, in dem installierte Plugin-Dateien liegen.
PLUGIN_INSTALL_DIR: Path = Path(__file__).parent / "installed"


class PandoraPlugin(ABC):
    """Basisklasse, von der alle Pandora Commander-Plugins erben müssen.

    Attributes:
        name: Anzeigename des Plugins.
        version: Versionsangabe des Plugins.
        author: Name des Plugin-Autors (optional, für den Plugin-Manager).
        description: Kurzbeschreibung, die im Plugin-Manager angezeigt wird.
        enabled_by_default: Ob das Plugin bei der ersten Erkennung
            automatisch aktiviert sein soll. Der Nutzer kann dies im
            Plugin-Manager jederzeit ändern; die Wahl wird persistiert.
        requires: Anzeigenamen (``name``) anderer Plugins, die vor
            diesem Plugin geladen sein müssen. Leer = keine
            Abhängigkeiten (Standard).
    """

    name: str = "Unbenanntes Plugin"
    version: str = "0.0"
    author: str = ""
    description: str = ""
    enabled_by_default: bool = True
    requires: list[str] = []

    @abstractmethod
    def on_load(self, context: dict[str, Any]) -> None:
        """Wird beim Laden des Plugins aufgerufen.

        Args:
            context: Zugriff auf zentrale Anwendungsobjekte, z. B.
                {"main_window": ..., "config_manager": ...}.
        """

    def on_unload(self) -> None:
        """Wird vor dem Entladen des Plugins aufgerufen (optional überschreibbar)."""

    def register_menu_actions(self, context: dict[str, Any]) -> list[QAction]:
        """Liefert QAction-Objekte für das "Plugins"-Menü der Menüleiste.

        Args:
            context: Dieselben Anwendungsobjekte wie bei on_load().

        Returns:
            Liste von QAction-Objekten (leer, wenn das Plugin keine
            eigenen Menüeinträge benötigt).
        """
        return []

    def register_toolbar_actions(self, context: dict[str, Any]) -> list[QAction]:
        """Liefert QAction-Objekte, die zusätzlich in der Symbolleiste erscheinen.

        Args:
            context: Dieselben Anwendungsobjekte wie bei on_load().

        Returns:
            Liste von QAction-Objekten (leer als Standard).
        """
        return []

    def build_context_menu_entries(
        self, context: dict[str, Any], selected_paths: list[Path]
    ) -> list[QAction]:
        """Liefert zusätzliche Einträge für das Dateipanel-Kontextmenü.

        Wird bei jedem Rechtsklick in einem Panel neu aufgerufen, damit
        Plugins ihre Einträge dynamisch an die aktuelle Auswahl
        anpassen können (z. B. nur anzeigen, wenn genau eine Datei
        markiert ist).

        Args:
            context: Dieselben Anwendungsobjekte wie bei on_load(),
                ergänzt um "active_panel" (das Panel, in dem gerade
                rechtsgeklickt wurde).
            selected_paths: Aktuell im betroffenen Panel markierte Pfade.

        Returns:
            Liste zusätzlicher QAction-Objekte (leer als Standard).
        """
        return []

    def on_panel_directory_changed(
        self, context: dict[str, Any], panel: FilePanel, path: Path
    ) -> None:
        """Wird aufgerufen, wenn in einem Panel erfolgreich navigiert wurde.

        Optionaler Erweiterungspunkt für Plugins, die auf Navigation
        reagieren wollen (z. B. Ordner-Tagging, Auto-Vorschau,
        kontextabhängige Statusleisten-Hinweise).

        Args:
            context: Dieselben Anwendungsobjekte wie bei on_load().
            panel: Das FilePanel, in dem navigiert wurde (links oder
                rechts) – vergleichbar mit context["left_panel"] bzw.
                context["right_panel"].
            path: Das neue aktuelle Verzeichnis dieses Panels.
        """

    def build_settings_widget(self, context: dict[str, Any]) -> QWidget | None:
        """Liefert ein eigenes Konfigurations-Widget für den Plugin-Manager-Dialog.

        Wird pro Plugin auf dessen eigenem Tab im Plugin-Manager-Dialog
        eingebettet. Plugins ohne eigene Einstellungen müssen diese
        Methode nicht überschreiben – der Tab zeigt dann automatisch
        nur Name, Version, Autor und Beschreibung.

        Args:
            context: Dieselben Anwendungsobjekte wie bei on_load().

        Returns:
            Ein eigenständiges QWidget, oder None (Standard).
        """
        return None


@dataclass
class PluginInfo:
    """Metadaten zu einem entdeckten Plugin, für den Plugin-Manager-Dialog.

    Attributes:
        file_path: Pfad der Plugin-Datei.
        name: Anzeigename (aus der Plugin-Klasse, sonst Dateiname).
        version: Versionsangabe des Plugins.
        author: Autor des Plugins.
        description: Kurzbeschreibung des Plugins.
        requires: Anzeigenamen der Plugins, von denen dieses abhängt.
        enabled: Ob das Plugin aktuell aktiviert ist.
        loaded: Ob das Plugin aktuell erfolgreich geladen ist.
        error: Fehlermeldung, falls das Laden fehlgeschlagen ist.
        instance: Die geladene Plugin-Instanz, falls vorhanden.
    """

    file_path: Path
    name: str
    version: str = "0.0"
    author: str = ""
    description: str = ""
    requires: list[str] = field(default_factory=list)
    enabled: bool = True
    loaded: bool = False
    error: str | None = None
    instance: PandoraPlugin | None = field(default=None, repr=False)

    @property
    def file_name(self) -> str:
        """Dateiname des Plugins (ohne Verzeichnisanteil)."""
        return self.file_path.name


class PluginManager:
    """Findet, lädt und verwaltet alle installierten Plugins.

    Args:
        plugin_dir: Optionales abweichendes Plugin-Verzeichnis
            (primär für Tests). Standardmäßig PLUGIN_INSTALL_DIR.
        disabled_plugins: Dateinamen von Plugins, die beim Laden
            übersprungen werden sollen (persistiert in den Settings).
    """

    def __init__(
        self,
        plugin_dir: Path | None = None,
        disabled_plugins: set[str] | None = None,
    ) -> None:
        self._plugin_dir = plugin_dir or PLUGIN_INSTALL_DIR
        self._plugin_dir.mkdir(parents=True, exist_ok=True)
        self._disabled_plugins: set[str] = set(disabled_plugins or set())
        self._loaded_plugins: list[PandoraPlugin] = []
        self._plugin_infos: list[PluginInfo] = []
        self._context: dict[str, Any] = {}

    @property
    def plugin_dir(self) -> Path:
        """Verzeichnis, in dem nach Plugin-Dateien gesucht wird."""
        return self._plugin_dir

    @property
    def loaded_plugins(self) -> list[PandoraPlugin]:
        """Alle aktuell geladenen Plugin-Instanzen."""
        return self._loaded_plugins

    @property
    def plugin_infos(self) -> list[PluginInfo]:
        """Metadaten zu allen entdeckten Plugins (geladen oder nicht), für den Dialog."""
        return self._plugin_infos

    @property
    def disabled_plugins(self) -> set[str]:
        """Dateinamen aller aktuell deaktivierten Plugins."""
        return set(self._disabled_plugins)

    def discover_plugin_files(self) -> list[Path]:
        """Sucht alle .py-Dateien im Plugin-Verzeichnis (nicht rekursiv)."""
        return sorted(p for p in self._plugin_dir.glob("*.py") if not p.name.startswith("_"))

    def _import_plugin_class(self, plugin_file: Path) -> type[PandoraPlugin] | None:
        """Importiert eine Plugin-Datei und gibt ihre PandoraPlugin-Unterklasse zurück.

        Instanziiert die Klasse bewusst noch nicht – die Metadaten
        (``name``, ``requires`` usw.) sind Klassenattribute und daher
        bereits ohne Instanz auslesbar. Das erlaubt es, den kompletten
        Abhängigkeitsgraphen zu bilden, bevor auch nur ein einziges
        Plugin tatsächlich instanziiert bzw. geladen wird.

        Args:
            plugin_file: Zu importierende .py-Datei.

        Returns:
            Die gefundene Unterklasse, oder None wenn keine gefunden
            wurde oder der Import fehlschlug.
        """
        module_name = f"pandora_plugin_{plugin_file.stem}"
        sys.modules.pop(module_name, None)

        spec = importlib.util.spec_from_file_location(module_name, plugin_file)
        if spec is None or spec.loader is None:
            logger.warning("Plugin-Modul konnte nicht geladen werden: %s", plugin_file)
            return None

        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)

        for attribute_name in dir(module):
            attribute = getattr(module, attribute_name)
            if (
                isinstance(attribute, type)
                and issubclass(attribute, PandoraPlugin)
                and attribute is not PandoraPlugin
            ):
                return attribute

        return None

    def _resolve_load_order(
        self, discovered: list[tuple[Path, type[PandoraPlugin]]]
    ) -> tuple[list[tuple[Path, type[PandoraPlugin]]], dict[str, str]]:
        """Sortiert entdeckte Plugins topologisch nach ihren ``requires``-Angaben.

        Verwendet Kahns Algorithmus. Plugins, deren Abhängigkeiten
        fehlen oder die Teil eines Abhängigkeitszyklus sind, werden
        nicht in die Ladereihenfolge aufgenommen; für sie wird
        stattdessen eine erklärende Fehlermeldung zurückgegeben, damit
        der Plugin-Manager-Dialog den Grund anzeigen kann.

        Args:
            discovered: Alle erfolgreich importierten (Datei, Klasse)-Paare.

        Returns:
            Tupel aus (topologisch sortierte Liste ladbarer Plugins,
            dict von Dateiname -> Fehlermeldung für nicht ladbare Plugins).
        """
        by_name: dict[str, tuple[Path, type[PandoraPlugin]]] = {
            plugin_class.name: (path, plugin_class) for path, plugin_class in discovered
        }
        errors: dict[str, str] = {}

        # Kanten: Abhängigkeit -> abhängiges Plugin (für Kahns Algorithmus).
        in_degree: dict[str, int] = {name: 0 for name in by_name}
        dependents: dict[str, list[str]] = {name: [] for name in by_name}

        for path, plugin_class in discovered:
            for dependency_name in plugin_class.requires:
                if dependency_name not in by_name:
                    errors[path.name] = (
                        f"Fehlende Abhängigkeit: Plugin '{plugin_class.name}' benötigt "
                        f"'{dependency_name}', das nicht gefunden wurde."
                    )
                    continue
                dependents[dependency_name].append(plugin_class.name)
                in_degree[plugin_class.name] += 1

        # Plugins mit fehlender Abhängigkeit werden aus dem Graphen entfernt,
        # damit sie nicht fälschlich als ladbar sortiert werden.
        broken_names = set(errors.keys())
        for path, plugin_class in discovered:
            if path.name in broken_names:
                in_degree.pop(plugin_class.name, None)

        queue = sorted(name for name, degree in in_degree.items() if degree == 0)
        ordered_names: list[str] = []
        while queue:
            queue.sort()
            current = queue.pop(0)
            ordered_names.append(current)
            for dependent_name in dependents.get(current, []):
                if dependent_name not in in_degree:
                    continue
                in_degree[dependent_name] -= 1
                if in_degree[dependent_name] == 0:
                    queue.append(dependent_name)

        loadable_names = set(ordered_names)
        for name in in_degree:
            if name not in loadable_names:
                path = by_name[name][0]
                errors[path.name] = (
                    f"Zyklische Abhängigkeit erkannt: Plugin '{name}' konnte nicht "
                    "in eine gültige Ladereihenfolge einsortiert werden."
                )

        ordered = [by_name[name] for name in ordered_names]
        return ordered, errors

    def load_all(self, context: dict[str, Any]) -> list[PandoraPlugin]:
        """Lädt automatisch alle gefundenen, aktivierten Plugins.

        Fehlerhafte Plugins werden übersprungen und geloggt, ohne den
        Start der restlichen Anwendung zu gefährden. Für jede
        entdeckte Datei (auch fehlerhafte oder deaktivierte) wird ein
        PluginInfo-Eintrag angelegt, damit der Plugin-Manager-Dialog
        einen vollständigen Überblick bieten kann.

        Die Ladereihenfolge berücksichtigt ``PandoraPlugin.requires``:
        Abhängigkeiten werden stets vor den Plugins geladen, die sie
        benötigen (topologische Sortierung, siehe ``_resolve_load_order``).

        Args:
            context: Wird an on_load() jedes Plugins weitergereicht.

        Returns:
            Liste aller erfolgreich geladenen Plugin-Instanzen.
        """
        self._context = context
        self._loaded_plugins = []
        self._plugin_infos = []

        plugin_files = self.discover_plugin_files()
        discovered: list[tuple[Path, type[PandoraPlugin]]] = []
        disabled_infos: list[PluginInfo] = []

        for plugin_file in plugin_files:
            is_enabled = plugin_file.name not in self._disabled_plugins
            if not is_enabled:
                disabled_infos.append(
                    PluginInfo(file_path=plugin_file, name=plugin_file.stem, enabled=False)
                )
                continue
            try:
                plugin_class = self._import_plugin_class(plugin_file)
            except Exception as error:  # Plugins dürfen die App niemals crashen
                logger.error("Plugin konnte nicht geladen werden (%s): %s", plugin_file.name, error)
                info = PluginInfo(file_path=plugin_file, name=plugin_file.stem)
                info.error = f"{error}\n{traceback.format_exc(limit=3)}"
                self._plugin_infos.append(info)
                continue

            if plugin_class is None:
                info = PluginInfo(file_path=plugin_file, name=plugin_file.stem)
                info.error = "Keine PandoraPlugin-Unterklasse in der Datei gefunden."
                self._plugin_infos.append(info)
                continue

            discovered.append((plugin_file, plugin_class))

        ordered, dependency_errors = self._resolve_load_order(discovered)

        for plugin_file, plugin_class in discovered:
            if plugin_file.name in dependency_errors:
                info = PluginInfo(
                    file_path=plugin_file,
                    name=plugin_class.name,
                    version=plugin_class.version,
                    author=plugin_class.author,
                    description=plugin_class.description,
                    requires=list(plugin_class.requires),
                )
                info.error = dependency_errors[plugin_file.name]
                self._plugin_infos.append(info)
                logger.error(info.error)

        for plugin_file, plugin_class in ordered:
            info = PluginInfo(
                file_path=plugin_file,
                name=plugin_class.name,
                version=plugin_class.version,
                author=plugin_class.author,
                description=plugin_class.description,
                requires=list(plugin_class.requires),
            )
            try:
                plugin_instance = plugin_class()
                plugin_instance.on_load(context)
            except Exception as error:
                logger.error("Fehler beim Laden von Plugin %s: %s", plugin_class.name, error)
                info.error = f"{error}\n{traceback.format_exc(limit=3)}"
                self._plugin_infos.append(info)
                continue

            info.instance = plugin_instance
            info.loaded = True
            self._plugin_infos.append(info)
            self._loaded_plugins.append(plugin_instance)
            logger.info("Plugin geladen: %s (%s)", plugin_instance.name, plugin_file.name)

        self._plugin_infos.extend(disabled_infos)
        return self._loaded_plugins

    def collect_menu_actions(self) -> list[tuple[PandoraPlugin, list[QAction]]]:
        """Sammelt die Menüeinträge aller geladenen Plugins.

        Returns:
            Liste aus (Plugin-Instanz, Liste seiner QAction-Objekte),
            nur für Plugins, die mindestens eine Aktion liefern.
        """
        results: list[tuple[PandoraPlugin, list[QAction]]] = []
        for plugin in self._loaded_plugins:
            try:
                actions = plugin.register_menu_actions(self._context)
            except Exception as error:
                logger.error("Fehler in register_menu_actions() von %s: %s", plugin.name, error)
                continue
            if actions:
                results.append((plugin, actions))
        return results

    def collect_toolbar_actions(self) -> list[QAction]:
        """Sammelt die Symbolleisten-Aktionen aller geladenen Plugins."""
        collected: list[QAction] = []
        for plugin in self._loaded_plugins:
            try:
                actions = plugin.register_toolbar_actions(self._context)
            except Exception as error:
                logger.error("Fehler in register_toolbar_actions() von %s: %s", plugin.name, error)
                continue
            collected.extend(actions)
        return collected

    def collect_context_menu_entries(
        self, active_panel: Any, selected_paths: list[Path]
    ) -> list[QAction]:
        """Sammelt zusätzliche Kontextmenü-Einträge aller geladenen Plugins.

        Args:
            active_panel: Das FilePanel, in dem rechtsgeklickt wurde.
            selected_paths: Aktuell in diesem Panel markierte Pfade.

        Returns:
            Liste zusätzlicher QAction-Objekte, über alle Plugins hinweg.
        """
        collected: list[QAction] = []
        panel_context = {**self._context, "active_panel": active_panel}
        for plugin in self._loaded_plugins:
            try:
                actions = plugin.build_context_menu_entries(panel_context, selected_paths)
            except Exception as error:
                logger.error(
                    "Fehler in build_context_menu_entries() von %s: %s", plugin.name, error
                )
                continue
            collected.extend(actions)
        return collected

    def notify_panel_directory_changed(self, panel: FilePanel, path: Path) -> None:
        """Benachrichtigt alle geladenen Plugins über eine Panel-Navigation.

        Args:
            panel: Das FilePanel, in dem navigiert wurde.
            path: Das neue aktuelle Verzeichnis dieses Panels.
        """
        for plugin in self._loaded_plugins:
            try:
                plugin.on_panel_directory_changed(self._context, panel, path)
            except Exception as error:
                logger.error(
                    "Fehler in on_panel_directory_changed() von %s: %s", plugin.name, error
                )

    def set_enabled(self, file_name: str, enabled: bool) -> None:
        """Aktiviert oder deaktiviert ein Plugin anhand seines Dateinamens.

        Wirkt erst nach dem nächsten load_all()/reload_all() vollständig
        (bereits geladene Plugins werden beim Deaktivieren sofort über
        on_unload() entladen).

        Args:
            file_name: Dateiname der Plugin-Datei (z. B. "mein_plugin.py").
            enabled: True zum Aktivieren, False zum Deaktivieren.
        """
        if enabled:
            self._disabled_plugins.discard(file_name)
        else:
            self._disabled_plugins.add(file_name)
            for plugin in list(self._loaded_plugins):
                info = next((i for i in self._plugin_infos if i.instance is plugin), None)
                if info is not None and info.file_name == file_name:
                    self._unload_single(plugin)

    def reload_all(self, context: dict[str, Any] | None = None) -> list[PandoraPlugin]:
        """Entlädt alle Plugins und lädt sie anschließend neu.

        Nützlich nach Änderungen an Plugin-Dateien oder am
        Aktivierungsstatus, ohne die Anwendung neu starten zu müssen.

        Args:
            context: Neuer Kontext, oder None um den zuletzt
                verwendeten Kontext wiederzuverwenden.

        Returns:
            Liste aller erfolgreich (neu) geladenen Plugin-Instanzen.
        """
        self.unload_all()
        return self.load_all(context or self._context)

    def _unload_single(self, plugin: PandoraPlugin) -> None:
        try:
            plugin.on_unload()
        except Exception as error:
            logger.error("Fehler beim Entladen von Plugin %s: %s", plugin.name, error)
        if plugin in self._loaded_plugins:
            self._loaded_plugins.remove(plugin)
        for info in self._plugin_infos:
            if info.instance is plugin:
                info.loaded = False
                info.instance = None

    def unload_all(self) -> None:
        """Ruft on_unload() für alle geladenen Plugins auf und leert die Liste."""
        for plugin in list(self._loaded_plugins):
            try:
                plugin.on_unload()
            except Exception as error:
                logger.error("Fehler beim Entladen von Plugin %s: %s", plugin.name, error)
        self._loaded_plugins = []
