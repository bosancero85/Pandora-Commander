"""Pandora® Commander – Plugin: Metadaten-Entfernen.

Fügt dem Rechtsklick-Kontextmenü der Dateipanels den Eintrag
"Metadaten entfernen …" hinzu, sobald mindestens eine unterstützte
Bild-, Audio- oder Videodatei markiert ist.

Setzt dieselbe Kernidee wie die eigenständige Idee "Pandora®
Metadata-Stripper" (siehe eigenes Projekt: Ordner-Watcher mit
Tray-Icon) direkt als On-Demand-Aktion im Dateimanager um, ohne auf
dessen separaten Dienst angewiesen zu sein:

    * Bilder (JPEG, PNG, TIFF, WebP, BMP): werden über Pillow neu
      gespeichert, dabei werden EXIF-, IPTC- und ICC-Profildaten
      verworfen (nur die reinen Bilddaten bleiben erhalten).
    * Audio/Video (MP3, MP4, MOV, MKV, AVI, FLAC, WAV, M4A): werden
      per ffmpeg verlustfrei remuxt (``-c copy``, kein Neu-Encoding)
      mit ``-map_metadata -1``, wodurch sämtliche Container- und
      Stream-Metadaten (Aufnahmeort, Gerät, Zeitstempel, Autor, etc.)
      entfernt werden, ohne die Bild-/Tonqualität zu verändern.

Der Nutzer wählt vor der Ausführung, ob die Originaldateien
überschrieben oder als Kopie mit dem Suffix "_clean" neben dem
Original abgelegt werden sollen. Die Verarbeitung läuft je Datei in
einem Hintergrund-Thread (bei Video/Audio als externer ffmpeg-
Prozess), damit die Oberfläche nicht einfriert.

Abhängigkeiten:
    * Für Bilder: Paket ``Pillow`` (``pip install Pillow``).
    * Für Audio/Video: ``ffmpeg`` muss im PATH verfügbar sein.
Fehlt eine der beiden Abhängigkeiten, werden die jeweils betroffenen
Dateitypen beim Kontextmenüeintrag automatisch ausgeklammert bzw.
das Plugin meldet dies nachvollziehbar im Log, statt abzustürzen.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QMessageBox,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from app.core.logging_setup import get_logger
from app.plugins.plugin_manager import PandoraPlugin

logger = get_logger(__name__)

_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tiff", ".tif", ".webp", ".bmp"}
_MEDIA_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".mp3", ".wav", ".flac", ".m4a", ".ogg"}
_CLEAN_SUFFIX = "_clean"

try:
    from PIL import Image

    _PILLOW_AVAILABLE = True
except ImportError:  # pragma: no cover - abhängig von der Zielumgebung
    _PILLOW_AVAILABLE = False

_FFMPEG_PATH = shutil.which("ffmpeg")


def _strip_image_metadata(source: Path, target: Path) -> None:
    with Image.open(source) as image:
        data = list(image.getdata())
        cleaned = Image.new(image.mode, image.size)
        cleaned.putdata(data)
        save_kwargs: dict[str, Any] = {}
        if image.format == "JPEG":
            save_kwargs["quality"] = "keep"
        cleaned.save(target, format=image.format, **save_kwargs)


def _strip_media_metadata(source: Path, target: Path) -> None:
    assert _FFMPEG_PATH is not None
    command = [
        _FFMPEG_PATH,
        "-y",
        "-i",
        str(source),
        "-map_metadata",
        "-1",
        "-c",
        "copy",
        str(target),
    ]
    result = subprocess.run(
        command, capture_output=True, text=True, timeout=600, check=False
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip()[-500:] or "ffmpeg meldete einen Fehler.")


class ModeSelectionDialog(QDialog):
    """Fragt ab, ob Originaldateien überschrieben oder Kopien erzeugt werden sollen."""

    def __init__(self, file_count: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Metadaten entfernen")

        self._overwrite_radio = QRadioButton("Originaldateien überschreiben")
        self._copy_radio = QRadioButton(f"Bereinigte Kopien erstellen (Suffix '{_CLEAN_SUFFIX}')")
        self._copy_radio.setChecked(True)

        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"{file_count} Datei(en) ausgewählt. Wie soll verfahren werden?"))
        layout.addWidget(self._copy_radio)
        layout.addWidget(self._overwrite_radio)
        layout.addWidget(button_box)

    @property
    def overwrite_originals(self) -> bool:
        return self._overwrite_radio.isChecked()


class _MetadataStripWorker(QThread):
    """Entfernt Metadaten aus einer Liste von Dateien im Hintergrund."""

    file_finished = pyqtSignal(Path, bool, str)  # Pfad, Erfolg, Ziel-/Fehlermeldung
    all_finished = pyqtSignal(int, int)

    def __init__(self, paths: list[Path], overwrite_originals: bool) -> None:
        super().__init__()
        self._paths = paths
        self._overwrite_originals = overwrite_originals

    def run(self) -> None:  # noqa: D102 - QThread-Standardmethode
        success_count = 0
        for source in self._paths:
            extension = source.suffix.lower()
            target = source if self._overwrite_originals else source.with_name(
                f"{source.stem}{_CLEAN_SUFFIX}{source.suffix}"
            )
            # Beim Überschreiben über eine temporäre Datei gehen, damit im
            # Fehlerfall das Original niemals halb geschrieben zurückbleibt.
            work_target = target.with_name(f".{target.name}.tmp") if self._overwrite_originals else target

            try:
                if extension in _IMAGE_EXTENSIONS:
                    if not _PILLOW_AVAILABLE:
                        raise RuntimeError("Paket 'Pillow' ist nicht installiert.")
                    _strip_image_metadata(source, work_target)
                elif extension in _MEDIA_EXTENSIONS:
                    if _FFMPEG_PATH is None:
                        raise RuntimeError("ffmpeg wurde nicht im PATH gefunden.")
                    _strip_media_metadata(source, work_target)
                else:
                    raise RuntimeError("Nicht unterstützter Dateityp.")

                if self._overwrite_originals:
                    work_target.replace(target)
            except Exception as error:  # Einzelfehler dürfen den Batch nicht abbrechen.
                if work_target != target and work_target.exists():
                    work_target.unlink(missing_ok=True)
                self.file_finished.emit(source, False, str(error))
                continue

            success_count += 1
            self.file_finished.emit(source, True, str(target))

        self.all_finished.emit(success_count, len(self._paths))


class MetadataStripperPlugin(PandoraPlugin):
    """Plugin zum Entfernen von Metadaten aus Bild-, Audio- und Videodateien."""

    name = "Metadaten-Entfernen"
    version = "1.0"
    author = "AKI_SystemDown®"
    description = (
        "Fügt dem Kontextmenü 'Metadaten entfernen …' hinzu: entfernt EXIF/IPTC/ICC aus "
        "Bildern (Pillow) sowie Container-Metadaten aus Audio/Video (ffmpeg-Remux ohne "
        "Neu-Encoding). Wahlweise Original überschreiben oder '_clean'-Kopie erzeugen."
    )

    def __init__(self) -> None:
        self._context: dict[str, Any] = {}
        self._active_workers: list[_MetadataStripWorker] = []

    def on_load(self, context: dict[str, Any]) -> None:
        self._context = context
        if not _PILLOW_AVAILABLE:
            logger.warning("%s: Pillow nicht gefunden – Bild-Unterstützung deaktiviert.", self.name)
        if _FFMPEG_PATH is None:
            logger.warning("%s: ffmpeg nicht gefunden – Audio/Video-Unterstützung deaktiviert.", self.name)
        logger.info("%s geladen.", self.name)

    def on_unload(self) -> None:
        for worker in self._active_workers:
            worker.wait(50)
        self._active_workers.clear()

    def build_context_menu_entries(
        self, context: dict[str, Any], selected_paths: list[Path]
    ) -> list[QAction]:
        supported = self._filter_supported(selected_paths)
        if not supported:
            return []

        main_window = context.get("main_window")
        active_panel = context.get("active_panel")

        action = QAction("Metadaten entfernen …", main_window)
        action.triggered.connect(
            lambda checked=False, paths=supported, panel=active_panel: self._start(paths, panel)
        )
        return [action]

    @staticmethod
    def _filter_supported(paths: list[Path]) -> list[Path]:
        supported: list[Path] = []
        for path in paths:
            if not path.is_file():
                continue
            extension = path.suffix.lower()
            if extension in _IMAGE_EXTENSIONS and _PILLOW_AVAILABLE:
                supported.append(path)
            elif extension in _MEDIA_EXTENSIONS and _FFMPEG_PATH is not None:
                supported.append(path)
        return supported

    def _start(self, paths: list[Path], panel: Any) -> None:
        main_window = self._context.get("main_window")
        dialog = ModeSelectionDialog(len(paths), parent=main_window)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        worker = _MetadataStripWorker(paths, dialog.overwrite_originals)
        self._failures: list[tuple[Path, str]] = []
        worker.file_finished.connect(self._on_file_finished)
        worker.all_finished.connect(
            lambda success, total, panel=panel: self._on_all_finished(success, total, panel)
        )
        worker.finished.connect(
            lambda w=worker: self._active_workers.remove(w) if w in self._active_workers else None
        )
        self._active_workers.append(worker)
        worker.start()

    def _on_file_finished(self, path: Path, success: bool, message: str) -> None:
        if not success:
            logger.error("Metadaten-Entfernung fehlgeschlagen (%s): %s", path, message)
            self._failures.append((path, message))

    def _on_all_finished(self, success_count: int, total: int, panel: Any) -> None:
        main_window = self._context.get("main_window")
        if panel is not None and hasattr(panel, "refresh"):
            panel.refresh()

        if success_count == total:
            QMessageBox.information(
                main_window, "Fertig", f"Metadaten aus {success_count} Datei(en) entfernt."
            )
        else:
            error_text = "\n".join(f"{path.name}: {message}" for path, message in self._failures)
            QMessageBox.warning(
                main_window,
                "Teilweise fehlgeschlagen",
                f"{success_count} von {total} Datei(en) erfolgreich verarbeitet.\n\n{error_text}",
            )
