"""Pandora® Commander – Beispiel-Plugin: Kontextmenü-Erweiterung.

Demonstriert PandoraPlugin.build_context_menu_entries(): Fügt dem
Rechtsklick-Kontextmenü der Dateipanels zwei zusätzliche, von der
aktuellen Auswahl abhängige Einträge hinzu:

    * "Pfad in Zwischenablage kopieren" – immer verfügbar, sobald
      mindestens ein Eintrag markiert ist.
    * "Ordnergröße berechnen" – nur sichtbar, wenn genau ein Ordner
      markiert ist; berechnet die Größe rekursiv in einem QThread,
      damit die Oberfläche währenddessen nicht einfriert, und zeigt
      das Ergebnis in der Statusleiste an.

Dieses Plugin dient als Vorlage für eigene Kontextmenü-Erweiterungen
und wird automatisch beim Programmstart geladen (deaktivierbar über
den Plugin-Manager unter Werkzeuge → Plugins).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QApplication

from app.core.filesystem.file_model import format_size
from app.core.logging_setup import get_logger
from app.plugins.plugin_manager import PandoraPlugin

logger = get_logger(__name__)


class _FolderSizeWorker(QThread):
    """Berechnet die Gesamtgröße eines Ordners rekursiv, ohne die UI zu blockieren."""

    finished_with_result = pyqtSignal(Path, int, int)  # Pfad, Bytes, Dateianzahl
    failed = pyqtSignal(Path, str)

    def __init__(self, folder: Path) -> None:
        super().__init__()
        self._folder = folder

    def run(self) -> None:  # noqa: D102 - QThread-Standardmethode
        total_bytes = 0
        file_count = 0
        try:
            for entry in self._folder.rglob("*"):
                try:
                    if entry.is_file() and not entry.is_symlink():
                        total_bytes += entry.stat().st_size
                        file_count += 1
                except OSError:
                    continue  # Einzelne unlesbare Einträge überspringen, nicht abbrechen.
        except OSError as error:
            self.failed.emit(self._folder, str(error))
            return
        self.finished_with_result.emit(self._folder, total_bytes, file_count)


class ContextMenuExamplePlugin(PandoraPlugin):
    """Beispiel-Plugin, das zusätzliche Kontextmenü-Einträge bereitstellt.

    Deklariert bewusst eine Abhängigkeit von "Systeminformationen" –
    rein zu Demonstrationszwecken der Ladereihenfolge (dieses Plugin
    benötigt jenes technisch nicht, aber der PluginManager garantiert
    dank ``requires``, dass Systeminformationen stets zuerst geladen
    wird).
    """

    name = "Kontextmenü-Erweiterungen"
    version = "1.0"
    author = "AKI_SystemDown®"
    requires = ["Systeminformationen"]
    description = (
        "Fügt dem Rechtsklick-Kontextmenü 'Pfad in Zwischenablage kopieren' und "
        "'Ordnergröße berechnen' hinzu. Dient als Vorlagen-Plugin und demonstriert "
        "zugleich eine deklarierte Plugin-Abhängigkeit (requires)."
    )

    def __init__(self) -> None:
        self._status_bar: Any | None = None
        self._active_workers: list[_FolderSizeWorker] = []

    def on_load(self, context: dict[str, Any]) -> None:
        main_window = context.get("main_window")
        self._status_bar = getattr(main_window, "statusBar", lambda: None)()
        logger.info("%s geladen.", self.name)

    def on_unload(self) -> None:
        for worker in self._active_workers:
            worker.wait(50)
        self._active_workers.clear()

    def build_context_menu_entries(
        self, context: dict[str, Any], selected_paths: list[Path]
    ) -> list[QAction]:
        if not selected_paths:
            return []

        main_window = context.get("main_window")
        actions: list[QAction] = []

        copy_path_action = QAction("Pfad in Zwischenablage kopieren", main_window)
        copy_path_action.triggered.connect(
            lambda checked=False, paths=selected_paths: self._copy_paths_to_clipboard(paths)
        )
        actions.append(copy_path_action)

        only_one_folder = len(selected_paths) == 1 and selected_paths[0].is_dir()
        if only_one_folder:
            size_action = QAction("Ordnergröße berechnen", main_window)
            size_action.triggered.connect(
                lambda checked=False, folder=selected_paths[0]: self._calculate_folder_size(folder)
            )
            actions.append(size_action)

        return actions

    def _copy_paths_to_clipboard(self, paths: list[Path]) -> None:
        text = "\n".join(str(path) for path in paths)
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(text)
        if self._status_bar is not None:
            self._status_bar.showMessage(f"{len(paths)} Pfad(e) in Zwischenablage kopiert.", 3000)

    def _calculate_folder_size(self, folder: Path) -> None:
        if self._status_bar is not None:
            self._status_bar.showMessage(f"Berechne Größe von '{folder.name}' …")

        worker = _FolderSizeWorker(folder)
        worker.finished_with_result.connect(self._on_folder_size_result)
        worker.failed.connect(self._on_folder_size_failed)
        worker.finished.connect(lambda w=worker: self._active_workers.remove(w) if w in self._active_workers else None)
        self._active_workers.append(worker)
        worker.start()

    def _on_folder_size_result(self, folder: Path, total_bytes: int, file_count: int) -> None:
        if self._status_bar is not None:
            self._status_bar.showMessage(
                f"'{folder.name}': {format_size(total_bytes)} in {file_count} Datei(en).",
                6000,
            )

    def _on_folder_size_failed(self, folder: Path, error_message: str) -> None:
        if self._status_bar is not None:
            self._status_bar.showMessage(
                f"Ordnergröße von '{folder.name}' konnte nicht berechnet werden: {error_message}",
                6000,
            )
