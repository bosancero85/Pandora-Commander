"""Pandora® Commander – Einstellungsverwaltung.

Lädt und speichert die Anwendungskonfiguration als JSON-Datei im
Nutzer-Konfigurationsverzeichnis. Stellt eine typisierte,
dataclass-basierte Sicht auf die Einstellungen bereit, damit der
restliche Code nicht direkt mit rohen dict-Strukturen arbeiten muss.

Verwendung:
    from app.core.config import ConfigManager

    config_manager = ConfigManager()
    settings = config_manager.load()
    settings.theme = "dark"
    config_manager.save(settings)
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from app.core.logging_setup import get_logger

logger = get_logger(__name__)

# Konfigurationsverzeichnis und -datei im Home-Verzeichnis des
# Nutzers, konsistent mit dem Logging-Verzeichnis in logging_setup.py.
CONFIG_DIR: Path = Path.home() / ".pandora_commander"
CONFIG_FILE: Path = CONFIG_DIR / "settings.json"

DEFAULT_THEME: str = "dark"
DEFAULT_LANGUAGE: str = "de"
DEFAULT_FONT_SIZE: int = 10
DEFAULT_ICON_THEME: str = "fluent"

#: Standard-Tastenkürzel gemäß Projektvorgabe (Commander-typisch).
DEFAULT_SHORTCUTS: dict[str, str] = {
    "preview": "F3",
    "edit": "F4",
    "copy": "F5",
    "move": "F6",
    "new_folder": "F7",
    "delete": "F8",
    "menu": "F9",
    "quit": "F10",
    "switch_pane": "Tab",
    "copy_clipboard": "Ctrl+C",
    "paste_clipboard": "Ctrl+V",
    "cut_clipboard": "Ctrl+X",
    "delete_key": "Del",
    "activate": "Return",
}


@dataclass
class Settings:
    """Typisierte Repräsentation der Anwendungseinstellungen.

    Attributes:
        theme: Name des aktiven Themes (z. B. "dark", "light").
        language: Sprachcode der Oberfläche ("de" oder "en").
        font_size: Basis-Schriftgröße der Oberfläche in Punkt.
        icon_theme: Name des aktiven Icon-Sets.
        default_left_path: Startpfad des linken Panels.
        default_right_path: Startpfad des rechten Panels.
        shortcuts: Zuordnung von Aktionsnamen zu Tastenkürzeln.
        favorites: Liste gespeicherter Favoritenordner (als Strings).
        debug_mode: Ob die Anwendung im Debug-Modus laufen soll.
        confirm_delete: Ob vor dem Löschen nachgefragt werden soll.
        disabled_plugins: Dateinamen von Plugins, die der Nutzer im
            Plugin-Manager deaktiviert hat und die beim nächsten
            Programmstart übersprungen werden sollen.
        plugin_hot_reload: Ob Änderungen an Plugin-Dateien automatisch
            erkannt und die Plugins ohne Neustart neu geladen werden
            sollen (siehe app.plugins.hot_reload).
        notifications_enabled: Ob nach abgeschlossenen Hintergrund-
            operationen (Kopieren, Verschieben, Löschen, ...) eine
            native System-Benachrichtigung angezeigt werden soll
            (siehe app.core.notifications).
        update_check_url: URL eines JSON-Update-Manifests, gegen das
            beim Start (falls aktiviert) sowie manuell über Hilfe ->
            Nach Updates suchen geprüft wird (siehe
            app.core.update_checker). Leer = Update-Prüfung deaktiviert.
        check_updates_on_startup: Ob beim Programmstart automatisch
            (leise, ohne Meldung bei "kein Update") im Hintergrund auf
            eine neuere Version geprüft werden soll.
        max_concurrent_operations: Wie viele Kopier-/Verschiebe-/
            Löschvorgänge die Operations-Warteschlange höchstens
            gleichzeitig laufen lässt (siehe
            app.core.filesystem.operation_queue.OperationQueueManager).
            Weitere Operationen warten, bis ein Platz frei wird.
    """

    theme: str = DEFAULT_THEME
    language: str = DEFAULT_LANGUAGE
    font_size: int = DEFAULT_FONT_SIZE
    icon_theme: str = DEFAULT_ICON_THEME
    default_left_path: str = str(Path.home())
    default_right_path: str = str(Path.home())
    shortcuts: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_SHORTCUTS))
    disabled_plugins: list[str] = field(default_factory=list)
    plugin_hot_reload: bool = True
    favorites: list[str] = field(default_factory=list)
    debug_mode: bool = False
    confirm_delete: bool = True
    notifications_enabled: bool = True
    update_check_url: str = ""
    check_updates_on_startup: bool = True
    max_concurrent_operations: int = 2

    def to_dict(self) -> dict[str, Any]:
        """Wandelt die Einstellungen in ein serialisierbares dict um."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Settings:
        """Erstellt Settings aus einem dict, unbekannte Schlüssel ignorierend.

        Fehlende Schlüssel werden mit den Standardwerten aufgefüllt,
        sodass ältere oder unvollständige Konfigurationsdateien nicht
        zum Absturz führen.

        Args:
            data: Rohdaten, typischerweise aus einer JSON-Datei.

        Returns:
            Eine neue Settings-Instanz.
        """
        valid_fields = {f for f in cls.__dataclass_fields__}
        filtered = {key: value for key, value in data.items() if key in valid_fields}
        return cls(**filtered)


class ConfigManager:
    """Lädt und speichert Settings-Instanzen als JSON-Datei.

    Args:
        config_path: Optionaler abweichender Pfad zur Konfigurations-
            datei, primär für Tests gedacht. Standardmäßig wird
            CONFIG_FILE verwendet.
    """

    def __init__(self, config_path: Path | None = None) -> None:
        self._config_path: Path = config_path or CONFIG_FILE
        self._settings: Settings | None = None

    @property
    def config_path(self) -> Path:
        """Pfad zur verwendeten Konfigurationsdatei."""
        return self._config_path

    def load(self) -> Settings:
        """Lädt die Einstellungen von der Festplatte.

        Existiert noch keine Konfigurationsdatei, werden Standard-
        einstellungen erzeugt und sofort gespeichert. Ist die Datei
        beschädigt (ungültiges JSON), wird ebenfalls auf die
        Standardeinstellungen zurückgefallen, ohne die Anwendung
        abstürzen zu lassen.

        Returns:
            Die geladenen (oder neu erzeugten) Settings.
        """
        if not self._config_path.exists():
            logger.info(
                "Keine Konfigurationsdatei gefunden, erstelle Standardwerte: %s",
                self._config_path,
            )
            self._settings = Settings()
            self.save(self._settings)
            return self._settings

        try:
            raw_text = self._config_path.read_text(encoding="utf-8")
            raw_data = json.loads(raw_text)
            self._settings = Settings.from_dict(raw_data)
            logger.info("Konfiguration geladen von %s", self._config_path)
        except (json.JSONDecodeError, OSError) as error:
            logger.error(
                "Konfigurationsdatei konnte nicht gelesen werden (%s), "
                "verwende Standardwerte.",
                error,
            )
            self._settings = Settings()

        return self._settings

    def save(self, settings: Settings) -> None:
        """Speichert die übergebenen Einstellungen als JSON-Datei.

        Args:
            settings: Die zu speichernden Einstellungen.

        Raises:
            OSError: Wenn die Datei nicht geschrieben werden kann
                (z. B. fehlende Schreibrechte).
        """
        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        self._settings = settings

        try:
            serialized = json.dumps(settings.to_dict(), indent=2, ensure_ascii=False)
            self._config_path.write_text(serialized, encoding="utf-8")
            logger.debug("Konfiguration gespeichert unter %s", self._config_path)
        except OSError as error:
            logger.error("Konfiguration konnte nicht gespeichert werden: %s", error)
            raise

    def current(self) -> Settings:
        """Liefert die aktuell geladenen Settings, lädt bei Bedarf nach.

        Returns:
            Die aktuelle Settings-Instanz.
        """
        if self._settings is None:
            return self.load()
        return self._settings
