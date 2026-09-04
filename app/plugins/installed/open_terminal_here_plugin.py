"""Pandora® Commander – Plugin: Terminal-hier-öffnen.

Fügt einen Toolbar-Button sowie einen Kontextmenü-Eintrag
"Terminal hier öffnen" hinzu, der einen Terminal-Emulator direkt im
aktuellen Verzeichnis des aktiven Panels startet (bzw. im
übergeordneten Ordner, falls eine Datei statt eines Ordners markiert
ist).

Terminal-Erkennung (in dieser Reihenfolge, erster Treffer gewinnt):
    1. ``x-terminal-emulator`` – Debian-/Kali-Alternative, verweist
       auf das systemweit konfigurierte Standard-Terminal.
    2. ``qterminal`` – Standard-Terminal von Xfce (Kali-Standard-
       Desktop).
    3. ``gnome-terminal``, ``konsole``, ``xfce4-terminal``, ``xterm``
       – gängige Alternativen auf anderen Desktop-Umgebungen.

Die Erkennung erfolgt einmalig beim Laden des Plugins über
``shutil.which``; ist kein unterstützter Terminal-Emulator
auffindbar, meldet das Plugin dies im Log und blendet Toolbar-Button
sowie Kontextmenü-Eintrag aus, statt einen Fehler beim Klicken zu
provozieren.

Der Prozess wird bewusst "fire and forget" gestartet (``Popen`` ohne
Warten) – Pandora Commander muss nicht blockieren oder den
Terminal-Prozess verwalten, ähnlich wie ein Dateimanager unter Linux
üblicherweise auch keine gestarteten externen Anwendungen überwacht.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QMessageBox

from app.core.logging_setup import get_logger
from app.plugins.plugin_manager import PandoraPlugin

logger = get_logger(__name__)

# Reihenfolge = Präferenz. Jeweils (Programmname, Argumente für "im Verzeichnis X starten").
_TERMINAL_CANDIDATES: list[tuple[str, list[str]]] = [
    ("x-terminal-emulator", []),
    ("qterminal", []),
    ("gnome-terminal", []),
    ("konsole", []),
    ("xfce4-terminal", []),
    ("xterm", []),
]


def _detect_terminal() -> tuple[str, list[str]] | None:
    for program_name, extra_args in _TERMINAL_CANDIDATES:
        resolved = shutil.which(program_name)
        if resolved is not None:
            return resolved, extra_args
    return None


class OpenTerminalHerePlugin(PandoraPlugin):
    """Plugin, das einen Terminal-Emulator im aktuellen Panel-Verzeichnis öffnet."""

    name = "Terminal hier öffnen"
    version = "1.0"
    author = "AKI_SystemDown®"
    description = (
        "Fügt einen Toolbar-Button und einen Kontextmenü-Eintrag hinzu, um einen "
        "Terminal-Emulator direkt im aktuellen Verzeichnis des aktiven Panels zu "
        "öffnen. Erkennt automatisch x-terminal-emulator, qterminal, gnome-terminal, "
        "konsole, xfce4-terminal oder xterm."
    )

    def __init__(self) -> None:
        self._context: dict[str, Any] = {}
        self._terminal: tuple[str, list[str]] | None = None

    def on_load(self, context: dict[str, Any]) -> None:
        self._context = context
        self._terminal = _detect_terminal()
        if self._terminal is None:
            logger.warning(
                "%s: Kein unterstützter Terminal-Emulator gefunden – Plugin bleibt inaktiv.", self.name
            )
        else:
            logger.info("%s geladen (verwendet: %s).", self.name, self._terminal[0])

    def register_toolbar_actions(self, context: dict[str, Any]) -> list[QAction]:
        if self._terminal is None:
            return []

        main_window = context.get("main_window")
        action = QAction("🖳 Terminal hier", main_window)
        action.setToolTip("Terminal im aktuellen Verzeichnis des aktiven Panels öffnen")
        action.triggered.connect(self._open_in_active_panel)
        return [action]

    def build_context_menu_entries(
        self, context: dict[str, Any], selected_paths: list[Path]
    ) -> list[QAction]:
        if self._terminal is None:
            return []

        main_window = context.get("main_window")
        active_panel = context.get("active_panel")

        target_directory = self._resolve_target_directory(selected_paths, active_panel)
        if target_directory is None:
            return []

        action = QAction("Terminal hier öffnen", main_window)
        action.triggered.connect(
            lambda checked=False, directory=target_directory: self._open_terminal(directory)
        )
        return [action]

    @staticmethod
    def _resolve_target_directory(selected_paths: list[Path], active_panel: Any) -> Path | None:
        if len(selected_paths) == 1:
            candidate = selected_paths[0]
            return candidate if candidate.is_dir() else candidate.parent
        current_directory = getattr(active_panel, "current_directory", None)
        return current_directory if isinstance(current_directory, Path) else None

    def _open_in_active_panel(self) -> None:
        active_panel = self._context.get("left_panel")
        current_directory = getattr(active_panel, "current_directory", None)
        directory = current_directory if isinstance(current_directory, Path) else Path.home()
        self._open_terminal(directory)

    def _open_terminal(self, directory: Path) -> None:
        if self._terminal is None:
            return

        program_path, extra_args = self._terminal
        main_window = self._context.get("main_window")
        try:
            subprocess.Popen(
                [program_path, *extra_args],
                cwd=str(directory),
                start_new_session=True,
            )
        except OSError as error:
            QMessageBox.critical(
                main_window, "Terminal konnte nicht gestartet werden", f"{program_path}: {error}"
            )
