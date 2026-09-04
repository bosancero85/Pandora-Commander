"""Pandora® Commander – Plugin-Hot-Reload.

Beobachtet das Plugin-Verzeichnis mittels ``watchdog`` und löst nach
Änderungen an .py-Dateien (Erstellen, Bearbeiten, Löschen, Umbenennen)
automatisch ein Neuladen aller Plugins aus, ohne dass die Anwendung
neu gestartet werden muss – praktisch während der Plugin-Entwicklung.

Watchdog beobachtet in einem eigenen Hintergrund-Thread. Da PyQt6-
Widgets ausschließlich aus dem GUI-Thread heraus verändert werden
dürfen, kommuniziert der Beobachtungs-Thread ausschließlich über
Qt-Signale mit dem Rest der Anwendung: Qt stellt automatisch eine
thread-sichere, gequeuete Zustellung sicher, sobald Sender und
Empfänger unterschiedlichen Threads angehören.

Mehrere schnell aufeinanderfolgende Änderungen (z. B. weil ein Editor
beim Speichern mehrere Dateisystemereignisse auslöst) werden über
einen kurzen Entprell-Timer zu einem einzigen Neuladen zusammengefasst.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from app.core.logging_setup import get_logger

logger = get_logger(__name__)

#: Wartezeit nach der letzten erkannten Dateiänderung, bevor tatsächlich
#: neu geladen wird. Fasst mehrere schnell aufeinanderfolgende
#: Dateisystemereignisse zu einem einzigen Reload zusammen.
_DEBOUNCE_INTERVAL_MS = 500


class _PluginDirectoryEventHandler(FileSystemEventHandler):
    """Watchdog-Handler, der relevante .py-Änderungen an ein Qt-Signal weiterreicht.

    Läuft im Beobachtungs-Thread von watchdog, nicht im Qt-Main-Thread.
    Berührt daher niemals GUI-Objekte direkt, sondern ausschließlich
    über die Signal/Slot-Verbindung des zugehörigen Watchers.
    """

    def __init__(self, watcher: PluginHotReloadWatcher) -> None:
        super().__init__()
        self._watcher = watcher

    def _handle(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        path = Path(str(event.src_path))
        # Nur echte Plugin-Quelldateien lösen ein Neuladen aus – Cache-
        # Dateien (__pycache__/*.pyc) und mit "_" beginnende Hilfsdateien
        # werden ignoriert, analog zu PluginManager.discover_plugin_files().
        if path.suffix != ".py" or path.name.startswith("_"):
            return
        self._watcher.raw_change_detected.emit()

    def on_created(self, event: FileSystemEvent) -> None:
        self._handle(event)

    def on_modified(self, event: FileSystemEvent) -> None:
        self._handle(event)

    def on_deleted(self, event: FileSystemEvent) -> None:
        self._handle(event)

    def on_moved(self, event: FileSystemEvent) -> None:
        self._handle(event)


class PluginHotReloadWatcher(QObject):
    """Überwacht ein Plugin-Verzeichnis und meldet Änderungen entprellt.

    Signals:
        reload_requested: Wird im Qt-Main-Thread ausgelöst, nachdem für
            ``_DEBOUNCE_INTERVAL_MS`` keine weitere Dateiänderung mehr
            eingetroffen ist. Der Empfänger sollte darauf mit
            ``PluginManager.reload_all()`` reagieren.

    Args:
        parent: Optionales Eltern-QObject (üblicherweise das
            Hauptfenster), damit der interne Entprell-Timer sicher im
            GUI-Thread lebt.
    """

    reload_requested = pyqtSignal()
    raw_change_detected = pyqtSignal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._observer: Observer | None = None
        self._watched_dir: Path | None = None

        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(_DEBOUNCE_INTERVAL_MS)
        self._debounce_timer.timeout.connect(self.reload_requested.emit)

        self.raw_change_detected.connect(self._on_raw_change)

    @property
    def is_active(self) -> bool:
        """Ob die Überwachung aktuell läuft."""
        return self._observer is not None

    @property
    def watched_directory(self) -> Path | None:
        """Das aktuell überwachte Verzeichnis, oder None wenn inaktiv."""
        return self._watched_dir

    def start(self, plugin_dir: Path) -> None:
        """Startet die Überwachung des angegebenen Plugin-Verzeichnisses.

        Ruft zuvor stop() auf, falls bereits eine Überwachung läuft,
        damit start() gefahrlos mehrfach aufgerufen werden kann (z. B.
        nach dem Umschalten der Einstellung im Plugin-Manager-Dialog).

        Args:
            plugin_dir: Zu überwachendes Verzeichnis. Nicht rekursiv,
                analog zu PluginManager.discover_plugin_files().
        """
        self.stop()

        try:
            handler = _PluginDirectoryEventHandler(self)
            observer = Observer()
            observer.schedule(handler, str(plugin_dir), recursive=False)
            observer.start()
        except OSError as error:
            logger.error("Plugin-Hot-Reload konnte nicht gestartet werden: %s", error)
            return

        self._observer = observer
        self._watched_dir = plugin_dir
        logger.info("Plugin-Hot-Reload aktiv für: %s", plugin_dir)

    def stop(self) -> None:
        """Beendet die Überwachung, falls aktiv. Gefahrlos mehrfach aufrufbar."""
        self._debounce_timer.stop()
        if self._observer is None:
            return
        try:
            self._observer.stop()
            self._observer.join(timeout=2)
        except Exception as error:  # Beenden darf die App niemals zum Absturz bringen.
            logger.warning("Fehler beim Beenden des Plugin-Hot-Reload-Beobachters: %s", error)
        finally:
            self._observer = None
            self._watched_dir = None
            logger.info("Plugin-Hot-Reload gestoppt.")

    def _on_raw_change(self) -> None:
        """Startet bzw. verlängert den Entprell-Timer bei jeder erkannten Dateiänderung."""
        self._debounce_timer.start()
