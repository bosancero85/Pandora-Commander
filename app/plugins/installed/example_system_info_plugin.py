"""Pandora® Commander – Beispiel-Plugin: Systeminformationen.

Demonstriert PandoraPlugin.register_menu_actions(): Fügt dem
"Plugins"-Menü einen Eintrag "Systeminformationen …" hinzu, der einen
kleinen Dialog mit Plattform-, Python- und Speicherplatzinformationen
zum aktuell im aktiven Panel angezeigten Laufwerk öffnet.

Bewusst ohne Zusatzabhängigkeiten (kein psutil) umgesetzt, damit das
Plugin auf jeder Standardinstallation sofort funktioniert – passend
zum Zielsystem Raspberry Pi 4B / Kali Linux.
"""

from __future__ import annotations

import platform
import shutil
import sys
from typing import Any

from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QCheckBox, QMessageBox, QVBoxLayout, QWidget

from app.core.filesystem.file_model import format_size
from app.core.logging_setup import get_logger
from app.plugins.plugin_manager import PandoraPlugin

logger = get_logger(__name__)


class SystemInfoExamplePlugin(PandoraPlugin):
    """Beispiel-Plugin, das einen Systeminformationen-Dialog im Plugins-Menü bereitstellt.

    Demonstriert zusätzlich zwei weitere Erweiterungspunkte:
      * ``on_panel_directory_changed`` – zeigt beim Navigieren optional
        den freien Speicherplatz des neuen Verzeichnisses in der
        Statusleiste an.
      * ``build_settings_widget`` – stellt dafür eine Ein/Aus-Checkbox
        auf dem eigenen Tab im Plugin-Manager-Dialog bereit.
    """

    name = "Systeminformationen"
    version = "1.1"
    author = "AKI_SystemDown®"
    description = (
        "Zeigt über das Plugins-Menü Plattform-, Python- und "
        "Speicherplatzinformationen zum aktiven Panel an. Kann optional "
        "beim Navigieren den freien Speicherplatz in der Statusleiste "
        "anzeigen (einstellbar auf diesem Tab)."
    )

    def __init__(self) -> None:
        self._context: dict[str, Any] = {}
        self._show_navigation_hints = True

    def on_load(self, context: dict[str, Any]) -> None:
        self._context = context
        logger.info("%s geladen.", self.name)

    def register_menu_actions(self, context: dict[str, Any]) -> list[QAction]:
        main_window = context.get("main_window")
        action = QAction("Systeminformationen …", main_window)
        action.triggered.connect(self._show_system_info)
        return [action]

    def on_panel_directory_changed(self, context: dict[str, Any], panel: Any, path: Path) -> None:
        if not self._show_navigation_hints:
            return
        status_bar = getattr(context.get("main_window"), "statusBar", lambda: None)()
        if status_bar is None:
            return
        try:
            usage = shutil.disk_usage(path)
            status_bar.showMessage(
                f"'{path.name or path}': {format_size(usage.free)} frei "
                f"von {format_size(usage.total)}.",
                4000,
            )
        except OSError:
            pass  # Kein Grund, die Navigation wegen eines Anzeige-Hinweises zu stören.

    def build_settings_widget(self, context: dict[str, Any]) -> QWidget | None:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        checkbox = QCheckBox(
            "Freien Speicherplatz beim Navigieren in der Statusleiste anzeigen", widget
        )
        checkbox.setChecked(self._show_navigation_hints)
        checkbox.toggled.connect(self._on_navigation_hints_toggled)
        layout.addWidget(checkbox)
        layout.addStretch(1)
        return widget

    def _on_navigation_hints_toggled(self, checked: bool) -> None:
        self._show_navigation_hints = checked

    def _show_system_info(self) -> None:
        main_window = self._context.get("main_window")
        active_panel = getattr(main_window, "active_panel", None)
        current_directory = getattr(active_panel, "current_directory", None)

        lines = [
            f"Betriebssystem: {platform.system()} {platform.release()}",
            f"Architektur: {platform.machine()}",
            f"Python: {sys.version.split()[0]}",
        ]

        if current_directory is not None:
            try:
                usage = shutil.disk_usage(current_directory)
                lines.append("")
                lines.append(f"Laufwerk von: {current_directory}")
                lines.append(f"Belegt: {format_size(usage.used)} von {format_size(usage.total)}")
                lines.append(f"Frei: {format_size(usage.free)}")
            except OSError as error:
                lines.append(f"Speicherplatz konnte nicht ermittelt werden: {error}")

        QMessageBox.information(
            main_window if isinstance(main_window, QWidget) else None,
            "Systeminformationen",
            "\n".join(lines),
        )
