"""Pandora® Commander – Plugin: Ordnergröße-Anzeige.

Ergänzt zwei Wege, die Größe von Ordnern (die das Dateisystem selbst
nicht rekursiv ausweist) sichtbar zu machen:

    1. Banner im Panel (analog zum Git-Status-Plugin): beim Betreten
       eines Ordners wird dessen Gesamtgröße samt Dateianzahl im
       Hintergrund berechnet und unterhalb der Statuszeile als
       dezente Zeile angezeigt, z. B. "📁 1.24 GB · 8.431 Dateien".
    2. Kontextmenü-Eintrag "Ordnergröße berechnen …" für eine oder
       mehrere markierte Ordner: öffnet einen Dialog mit einer nach
       Größe sortierten Tabelle (Ordnername, Größe, Dateianzahl) –
       praktisch, um schnell zu sehen, welcher von mehreren Ordnern
       am meisten Platz belegt (ähnlich einem Mini-WinDirStat).

Beide Berechnungen laufen in einem Hintergrund-Thread
(``os.walk`` + ``stat`` je Datei), damit große Ordnerbäume die
Oberfläche nicht blockieren. Für den Panel-Banner verwirft ein
Generationszähler pro Panel veraltete Ergebnisse, falls währenddessen
bereits weiternavigiert wurde – dieselbe Absicherung wie beim
Git-Status-Plugin.

Symbolische Links werden beim Durchlaufen nicht verfolgt (um
Endlosschleifen bei zirkulären Links zu vermeiden); nicht lesbare
Unterordner/Dateien werden übersprungen, statt die gesamte Berechnung
abzubrechen.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QDialog,
    QLabel,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.core.filesystem.file_model import format_size
from app.core.logging_setup import get_logger
from app.plugins.plugin_manager import PandoraPlugin
from app.ui.widgets.file_panel import FilePanel

logger = get_logger(__name__)

_COLUMN_NAME = 0
_COLUMN_SIZE = 1
_COLUMN_FILE_COUNT = 2


def _calculate_directory_size(directory: Path) -> tuple[int, int]:
    """Ermittelt Gesamtgröße (Bytes) und Dateianzahl eines Ordners rekursiv."""
    total_bytes = 0
    file_count = 0
    for root, directories, files in os.walk(directory, followlinks=False, onerror=lambda error: None):
        for file_name in files:
            file_path = Path(root) / file_name
            try:
                if not file_path.is_symlink():
                    total_bytes += file_path.stat().st_size
                    file_count += 1
            except OSError:
                continue
    return total_bytes, file_count


class _BannerSizeWorker(QThread):
    """Berechnet die Größe eines einzelnen Ordners für den Panel-Banner."""

    finished_with_result = pyqtSignal(int, int)  # Bytes, Dateianzahl

    def __init__(self, directory: Path) -> None:
        super().__init__()
        self._directory = directory

    def run(self) -> None:  # noqa: D102 - QThread-Standardmethode
        total_bytes, file_count = _calculate_directory_size(self._directory)
        self.finished_with_result.emit(total_bytes, file_count)


class _MultiFolderSizeWorker(QThread):
    """Berechnet die Größen mehrerer markierter Ordner nacheinander."""

    folder_finished = pyqtSignal(Path, int, int)  # Pfad, Bytes, Dateianzahl
    all_finished = pyqtSignal()

    def __init__(self, directories: list[Path]) -> None:
        super().__init__()
        self._directories = directories

    def run(self) -> None:  # noqa: D102 - QThread-Standardmethode
        for directory in self._directories:
            total_bytes, file_count = _calculate_directory_size(directory)
            self.folder_finished.emit(directory, total_bytes, file_count)
        self.all_finished.emit()


class FolderSizeDialog(QDialog):
    """Zeigt die berechneten Größen mehrerer markierter Ordner, sortiert nach Größe."""

    def __init__(self, directories: list[Path], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Pandora® Commander – Ordnergröße")
        self.resize(560, 400)

        self._results: list[tuple[Path, int, int]] = []

        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, len(directories))

        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(["Ordner", "Größe", "Dateien"])
        self._table.setColumnWidth(0, 300)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

        close_button = QPushButton("Schließen")
        close_button.clicked.connect(self.close)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"Berechne Größe von {len(directories)} Ordner(n) …"))
        layout.addWidget(self._progress_bar)
        layout.addWidget(self._table, stretch=1)
        layout.addWidget(close_button)

        self._worker = _MultiFolderSizeWorker(directories)
        self._worker.folder_finished.connect(self._on_folder_finished)
        self._worker.all_finished.connect(self._on_all_finished)
        self._worker.start()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt-Überschreibung
        if self._worker.isRunning():
            self._worker.wait(50)
        super().closeEvent(event)

    def _on_folder_finished(self, directory: Path, total_bytes: int, file_count: int) -> None:
        self._results.append((directory, total_bytes, file_count))
        self._progress_bar.setValue(self._progress_bar.value() + 1)
        self._populate_table()

    def _populate_table(self) -> None:
        sorted_results = sorted(self._results, key=lambda item: item[1], reverse=True)
        self._table.setRowCount(len(sorted_results))
        for row, (directory, total_bytes, file_count) in enumerate(sorted_results):
            self._table.setItem(row, _COLUMN_NAME, QTableWidgetItem(directory.name))
            self._table.setItem(row, _COLUMN_SIZE, QTableWidgetItem(format_size(total_bytes)))
            self._table.setItem(row, _COLUMN_FILE_COUNT, QTableWidgetItem(str(file_count)))

    def _on_all_finished(self) -> None:
        self.setWindowTitle("Pandora® Commander – Ordnergröße (fertig)")


class FolderSizePlugin(PandoraPlugin):
    """Plugin für rekursive Ordnergrößen-Anzeige im Panel-Banner und per Kontextmenü."""

    name = "Ordnergröße-Anzeige"
    version = "1.0"
    author = "AKI_SystemDown®"
    description = (
        "Zeigt die rekursive Größe des aktuellen Ordners als Panel-Banner an und fügt "
        "dem Kontextmenü 'Ordnergröße berechnen …' für markierte Ordner hinzu (Ergebnis "
        "als nach Größe sortierte Tabelle)."
    )

    def __init__(self) -> None:
        self._context: dict[str, Any] = {}
        self._banners: dict[int, QLabel] = {}
        self._generation: dict[int, int] = {}
        self._active_workers: list[QThread] = []
        self._open_dialogs: list[FolderSizeDialog] = []

    def on_load(self, context: dict[str, Any]) -> None:
        self._context = context
        logger.info("%s geladen.", self.name)

    def on_unload(self) -> None:
        for banner in self._banners.values():
            banner.deleteLater()
        self._banners.clear()
        self._generation.clear()
        for worker in self._active_workers:
            worker.wait(50)
        self._active_workers.clear()
        for dialog in self._open_dialogs:
            dialog.close()
        self._open_dialogs.clear()

    def on_panel_directory_changed(self, context: dict[str, Any], panel: FilePanel, path: Path) -> None:
        panel_id = id(panel)
        banner = self._banners.get(panel_id)
        if banner is None:
            banner = QLabel()
            banner.setStyleSheet("color: #9fb8d9; font-size: 11px; padding: 1px 4px;")
            layout = panel.layout()
            if layout is not None:
                layout.addWidget(banner)
            self._banners[panel_id] = banner

        banner.setText("📁 Berechne Ordnergröße …")
        banner.setVisible(True)

        generation = self._generation.get(panel_id, 0) + 1
        self._generation[panel_id] = generation

        worker = _BannerSizeWorker(path)
        worker.finished_with_result.connect(
            lambda total_bytes, file_count, panel_id=panel_id, generation=generation: self._on_banner_result(
                panel_id, generation, total_bytes, file_count
            )
        )
        worker.finished.connect(
            lambda w=worker: self._active_workers.remove(w) if w in self._active_workers else None
        )
        self._active_workers.append(worker)
        worker.start()

    def _on_banner_result(self, panel_id: int, generation: int, total_bytes: int, file_count: int) -> None:
        if self._generation.get(panel_id) != generation:
            return  # Zwischenzeitlich weiternavigiert – veraltetes Ergebnis verwerfen.

        banner = self._banners.get(panel_id)
        if banner is None:
            return
        banner.setText(f"📁 {format_size(total_bytes)} · {file_count} Datei(en)")

    def build_context_menu_entries(
        self, context: dict[str, Any], selected_paths: list[Path]
    ) -> list[QAction]:
        directories = [path for path in selected_paths if path.is_dir()]
        if not directories:
            return []

        main_window = context.get("main_window")
        action = QAction("Ordnergröße berechnen …", main_window)
        action.triggered.connect(
            lambda checked=False, paths=directories: self._open_size_dialog(paths)
        )
        return [action]

    def _open_size_dialog(self, directories: list[Path]) -> None:
        main_window = self._context.get("main_window")
        dialog = FolderSizeDialog(directories, parent=main_window)
        dialog.destroyed.connect(
            lambda: self._open_dialogs.remove(dialog) if dialog in self._open_dialogs else None
        )
        self._open_dialogs.append(dialog)
        dialog.show()
