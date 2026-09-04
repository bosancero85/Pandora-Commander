"""Pandora® Commander – Plugin: Medien-Konverter-Anbindung.

Fügt dem Rechtsklick-Kontextmenü der Dateipanels den Eintrag
"In MP3 umwandeln …" hinzu, sobald mindestens eine unterstützte
Videodatei markiert ist. Setzt dieselbe Kernidee wie das eigenständige
Projekt "Pandora® Converter" (MP4-zu-MP3 per Drag & Drop, ffmpeg im
Hintergrund-Thread) direkt als On-Demand-Kontextmenü-Aktion im
Dateimanager um, ohne die separate App starten zu müssen:

    * Unterstützte Quellformate: MP4, MKV, AVI, MOV, WEBM, FLV.
    * Die Audiospur wird per ffmpeg mit ``libmp3lame`` extrahiert und
      kodiert (``-vn`` verwirft den Videostream vollständig), die
      Zielbitrate ist über einen kleinen Auswahldialog wählbar
      (128 / 192 / 320 kbit/s).
    * Ergebnisdateien landen mit gleichem Namen und der Endung
      ``.mp3`` im selben Verzeichnis wie das Original; existiert
      bereits eine gleichnamige Datei, wird automatisch durchnummeriert
      (``Video (1).mp3`` usw.), um keine Datei versehentlich zu
      überschreiben.

Jede Konvertierung läuft als externer ffmpeg-Prozess in einem
eigenen Hintergrund-Thread, mehrere markierte Dateien werden
nacheinander verarbeitet; die Oberfläche bleibt währenddessen
reaktionsfähig und zeigt Fortschritt sowie Ergebnis pro Datei an.

Abhängigkeit: ``ffmpeg`` muss im PATH verfügbar sein.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.core.logging_setup import get_logger
from app.plugins.plugin_manager import PandoraPlugin

logger = get_logger(__name__)

_VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv"}
_BITRATE_OPTIONS = ["128 kbit/s", "192 kbit/s", "320 kbit/s"]
_BITRATE_VALUES = {"128 kbit/s": "128k", "192 kbit/s": "192k", "320 kbit/s": "320k"}

_FFMPEG_PATH = shutil.which("ffmpeg")


def _next_free_path(path: Path) -> Path:
    if not path.exists():
        return path
    counter = 1
    while True:
        candidate = path.with_name(f"{path.stem} ({counter}){path.suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


def _convert_to_mp3(source: Path, target: Path, bitrate: str) -> None:
    assert _FFMPEG_PATH is not None
    command = [
        _FFMPEG_PATH,
        "-y",
        "-i",
        str(source),
        "-vn",
        "-acodec",
        "libmp3lame",
        "-b:a",
        bitrate,
        str(target),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=1800, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip()[-500:] or "ffmpeg meldete einen Fehler.")


class BitrateSelectionDialog(QDialog):
    """Fragt die gewünschte MP3-Zielbitrate ab."""

    def __init__(self, file_count: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("In MP3 umwandeln")

        self._bitrate_combo = QComboBox()
        self._bitrate_combo.addItems(_BITRATE_OPTIONS)
        self._bitrate_combo.setCurrentText("192 kbit/s")

        form = QFormLayout()
        form.addRow("Zielbitrate:", self._bitrate_combo)

        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.button(QDialogButtonBox.StandardButton.Ok).setText("Umwandeln")
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"{file_count} Videodatei(en) werden zu MP3 umgewandelt."))
        layout.addLayout(form)
        layout.addWidget(button_box)

    @property
    def bitrate_value(self) -> str:
        return _BITRATE_VALUES[self._bitrate_combo.currentText()]


class _ConversionWorker(QThread):
    """Wandelt eine Liste von Videodateien nacheinander in MP3 um."""

    file_finished = pyqtSignal(Path, bool, str)  # Quelle, Erfolg, Zielpfad oder Fehlermeldung
    all_finished = pyqtSignal(int, int)

    def __init__(self, paths: list[Path], bitrate: str) -> None:
        super().__init__()
        self._paths = paths
        self._bitrate = bitrate

    def run(self) -> None:  # noqa: D102 - QThread-Standardmethode
        success_count = 0
        for source in self._paths:
            target = _next_free_path(source.with_suffix(".mp3"))
            try:
                _convert_to_mp3(source, target, self._bitrate)
            except (RuntimeError, OSError, subprocess.TimeoutExpired) as error:
                self.file_finished.emit(source, False, str(error))
                continue
            success_count += 1
            self.file_finished.emit(source, True, str(target))
        self.all_finished.emit(success_count, len(self._paths))


class ConversionProgressDialog(QDialog):
    """Zeigt Fortschritt und Ergebnis der laufenden Konvertierung(en) an."""

    def __init__(self, paths: list[Path], bitrate: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Pandora® Commander – Konvertierung läuft …")
        self.resize(560, 360)

        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, len(paths))
        self._result_list = QListWidget()
        self._close_button = QPushButton("Schließen")
        self._close_button.setEnabled(False)
        self._close_button.clicked.connect(self.close)

        layout = QVBoxLayout(self)
        layout.addWidget(self._progress_bar)
        layout.addWidget(self._result_list, stretch=1)
        layout.addWidget(self._close_button)

        self.on_all_finished_callback = None  # wird vom Plugin gesetzt

        self._worker = _ConversionWorker(paths, bitrate)
        self._worker.file_finished.connect(self._on_file_finished)
        self._worker.all_finished.connect(self._on_all_finished)
        self._worker.start()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt-Überschreibung
        if self._worker.isRunning():
            self._worker.wait(50)
        super().closeEvent(event)

    def _on_file_finished(self, source: Path, success: bool, message: str) -> None:
        self._progress_bar.setValue(self._progress_bar.value() + 1)
        if success:
            self._result_list.addItem(QListWidgetItem(f"✅ {source.name} → {Path(message).name}"))
        else:
            self._result_list.addItem(QListWidgetItem(f"❌ {source.name}: {message}"))
            logger.error("Konvertierung fehlgeschlagen (%s): %s", source, message)

    def _on_all_finished(self, success_count: int, total: int) -> None:
        self._close_button.setEnabled(True)
        self.setWindowTitle(f"Pandora® Commander – {success_count}/{total} erfolgreich umgewandelt")
        if self.on_all_finished_callback is not None:
            self.on_all_finished_callback()


class MediaConverterPlugin(PandoraPlugin):
    """Plugin zur direkten MP4→MP3-Umwandlung markierter Videodateien im Kontextmenü."""

    name = "Medien-Konverter"
    version = "1.0"
    author = "AKI_SystemDown®"
    description = (
        "Fügt dem Kontextmenü 'In MP3 umwandeln …' hinzu: extrahiert per ffmpeg die "
        "Audiospur markierter Videodateien (MP4/MKV/AVI/MOV/WEBM/FLV) als MP3 in "
        "wählbarer Bitrate – dieselbe Kernidee wie die eigenständige App Pandora® Converter."
    )

    def __init__(self) -> None:
        self._context: dict[str, Any] = {}
        self._open_dialogs: list[ConversionProgressDialog] = []

    def on_load(self, context: dict[str, Any]) -> None:
        self._context = context
        if _FFMPEG_PATH is None:
            logger.warning("%s: ffmpeg nicht gefunden – Plugin bleibt inaktiv.", self.name)
        logger.info("%s geladen.", self.name)

    def on_unload(self) -> None:
        for dialog in self._open_dialogs:
            dialog.close()
        self._open_dialogs.clear()

    def build_context_menu_entries(
        self, context: dict[str, Any], selected_paths: list[Path]
    ) -> list[QAction]:
        if _FFMPEG_PATH is None:
            return []

        video_paths = [
            path for path in selected_paths if path.is_file() and path.suffix.lower() in _VIDEO_EXTENSIONS
        ]
        if not video_paths:
            return []

        main_window = context.get("main_window")
        active_panel = context.get("active_panel")

        action = QAction("In MP3 umwandeln …", main_window)
        action.triggered.connect(
            lambda checked=False, paths=video_paths, panel=active_panel: self._start(paths, panel)
        )
        return [action]

    def _start(self, paths: list[Path], panel: Any) -> None:
        main_window = self._context.get("main_window")
        selection_dialog = BitrateSelectionDialog(len(paths), parent=main_window)
        if selection_dialog.exec() != QDialog.DialogCode.Accepted:
            return

        progress_dialog = ConversionProgressDialog(paths, selection_dialog.bitrate_value, parent=main_window)
        progress_dialog.on_all_finished_callback = lambda panel=panel: self._on_finished(panel)
        progress_dialog.destroyed.connect(
            lambda: self._open_dialogs.remove(progress_dialog) if progress_dialog in self._open_dialogs else None
        )
        self._open_dialogs.append(progress_dialog)
        progress_dialog.show()

    def _on_finished(self, panel: Any) -> None:
        if panel is not None and hasattr(panel, "refresh"):
            panel.refresh()
