"""Pandora® Commander – Plugin: EXIF-Viewer.

Fügt dem Rechtsklick-Kontextmenü der Dateipanels den Eintrag
"EXIF-Daten anzeigen …" hinzu, sobald mindestens eine unterstützte
Bilddatei markiert ist. Zeigt pro Datei:

    * Alle vorhandenen EXIF-Tags (Kamera, Belichtungszeit, ISO,
      Blende, Aufnahmedatum, Software, etc.) in einer sortierten
      Tabelle, per Pillows ``ExifTags``-Namensauflösung lesbar
      gemacht statt als rohe Tag-IDs.
    * GPS-Koordinaten (falls vorhanden) zusätzlich in Dezimalgrad
      umgerechnet, mit anklickbarem Link zu OpenStreetMap.
    * Bei mehreren markierten Bildern: Tabs, ein Tab pro Datei.

Bilder ohne auslesbare EXIF-Daten (z. B. viele PNGs, Screenshots
oder bereits per Metadaten-Entfernen bereinigte Dateien) zeigen einen
klaren Hinweis statt einer leeren Tabelle.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PyQt6.QtGui import QAction, QColor
from PyQt6.QtWidgets import (
    QDialog,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.core.logging_setup import get_logger
from app.plugins.plugin_manager import PandoraPlugin

logger = get_logger(__name__)

_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".tiff", ".tif", ".png", ".webp", ".heic", ".bmp"}
_COLUMN_TAG = 0
_COLUMN_VALUE = 1

try:
    from PIL import ExifTags, Image

    _PILLOW_AVAILABLE = True
except ImportError:  # pragma: no cover - abhängig von der Zielumgebung
    _PILLOW_AVAILABLE = False


def _dms_to_decimal(dms: tuple, reference: str) -> float:
    degrees, minutes, seconds = (float(part) for part in dms)
    decimal = degrees + minutes / 60.0 + seconds / 3600.0
    if reference in ("S", "W"):
        decimal = -decimal
    return decimal


def _extract_gps_coordinates(gps_info: dict) -> tuple[float, float] | None:
    try:
        latitude = _dms_to_decimal(gps_info[2], gps_info[1])
        longitude = _dms_to_decimal(gps_info[4], gps_info[3])
        return latitude, longitude
    except (KeyError, ValueError, TypeError, ZeroDivisionError):
        return None


def _read_exif_data(path: Path) -> dict[str, str]:
    """Liest alle auslesbaren EXIF-Tags einer Bilddatei als lesbares dict."""
    result: dict[str, str] = {}
    with Image.open(path) as image:
        exif_data = image.getexif()
        if not exif_data:
            return result

        for tag_id, raw_value in exif_data.items():
            tag_name = ExifTags.TAGS.get(tag_id, str(tag_id))

            if tag_name == "GPSInfo":
                gps_info = {
                    ExifTags.GPSTAGS.get(sub_id, sub_id): sub_value
                    for sub_id, sub_value in raw_value.items()
                }
                coordinates = _extract_gps_coordinates(gps_info)
                if coordinates is not None:
                    latitude, longitude = coordinates
                    result["GPS-Koordinaten"] = f"{latitude:.6f}, {longitude:.6f}"
                    result["GPS-Link"] = (
                        f"https://www.openstreetmap.org/?mlat={latitude:.6f}&mlon={longitude:.6f}#map=16"
                    )
                continue

            if isinstance(raw_value, bytes):
                try:
                    display_value = raw_value.decode("utf-8", errors="replace")
                except Exception:  # noqa: BLE001
                    display_value = repr(raw_value)
            else:
                display_value = str(raw_value)

            result[tag_name] = display_value

    return result


class _ExifTabWidget(QWidget):
    """Zeigt die EXIF-Tabelle für eine einzelne Datei."""

    def __init__(self, path: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)

        try:
            exif_entries = _read_exif_data(path)
        except Exception as error:  # noqa: BLE001 - Pillow kann diverse Fehler werfen
            layout.addWidget(QLabel(f"Fehler beim Lesen der EXIF-Daten: {error}"))
            return

        if not exif_entries:
            layout.addWidget(QLabel("Keine EXIF-Daten in dieser Datei gefunden."))
            return

        table = QTableWidget(len(exif_entries), 2)
        table.setHorizontalHeaderLabels(["Tag", "Wert"])
        table.setColumnWidth(0, 220)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

        for row, (tag_name, value) in enumerate(sorted(exif_entries.items())):
            tag_item = QTableWidgetItem(tag_name)
            value_item = QTableWidgetItem(value)
            if tag_name == "GPS-Link":
                value_item.setForeground(QColor("#3498db"))
            table.setItem(row, _COLUMN_TAG, tag_item)
            table.setItem(row, _COLUMN_VALUE, value_item)

        layout.addWidget(table)


class ExifViewerDialog(QDialog):
    """Zeigt die EXIF-Daten einer oder mehrerer markierter Bilddateien in Tabs."""

    def __init__(self, paths: list[Path], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Pandora® Commander – EXIF-Daten")
        self.resize(640, 480)

        tabs = QTabWidget()
        for path in paths:
            tabs.addTab(_ExifTabWidget(path), path.name)

        close_button = QPushButton("Schließen")
        close_button.clicked.connect(self.close)

        layout = QVBoxLayout(self)
        layout.addWidget(tabs, stretch=1)
        layout.addWidget(close_button)


class ExifViewerPlugin(PandoraPlugin):
    """Plugin zur Anzeige von EXIF-Metadaten markierter Bilddateien im Kontextmenü."""

    name = "EXIF-Viewer"
    version = "1.0"
    author = "AKI_SystemDown®"
    description = (
        "Fügt dem Kontextmenü 'EXIF-Daten anzeigen …' hinzu: zeigt Kamera-, Aufnahme- "
        "und GPS-Metadaten markierter Bilder in einer Tabbed-Ansicht, inkl. Link zu "
        "OpenStreetMap bei vorhandenen GPS-Koordinaten."
    )

    def __init__(self) -> None:
        self._context: dict[str, Any] = {}
        self._open_dialogs: list[ExifViewerDialog] = []

    def on_load(self, context: dict[str, Any]) -> None:
        self._context = context
        if not _PILLOW_AVAILABLE:
            logger.warning("%s: Pillow nicht gefunden – Plugin bleibt inaktiv.", self.name)
        logger.info("%s geladen.", self.name)

    def on_unload(self) -> None:
        for dialog in self._open_dialogs:
            dialog.close()
        self._open_dialogs.clear()

    def build_context_menu_entries(
        self, context: dict[str, Any], selected_paths: list[Path]
    ) -> list[QAction]:
        if not _PILLOW_AVAILABLE:
            return []

        image_paths = [
            path for path in selected_paths if path.is_file() and path.suffix.lower() in _IMAGE_EXTENSIONS
        ]
        if not image_paths:
            return []

        main_window = context.get("main_window")
        action = QAction("EXIF-Daten anzeigen …", main_window)
        action.triggered.connect(
            lambda checked=False, paths=image_paths: self._open_dialog(paths)
        )
        return [action]

    def _open_dialog(self, paths: list[Path]) -> None:
        main_window = self._context.get("main_window")
        dialog = ExifViewerDialog(paths, parent=main_window)
        dialog.destroyed.connect(
            lambda: self._open_dialogs.remove(dialog) if dialog in self._open_dialogs else None
        )
        self._open_dialogs.append(dialog)
        dialog.show()
