"""Pandora® Commander – integriertes Terminal.

Stellt ein einfaches, eingebettetes Terminal auf Basis von QProcess
bereit. Statt einer vollen Terminalemulation (VT100 etc., die eine
schwergewichtige externe Bibliothek wie QTermWidget erfordern würde)
wird eine Shell als Kindprozess gestartet, dessen stdin/stdout/stderr
an ein Textfeld gekoppelt sind – ausreichend für typische
Commander-Aufgaben (schnelle Befehle im aktuellen Verzeichnis).

Die verwendete Shell wird plattformabhängig gewählt:
    * Windows: cmd.exe
    * Linux/macOS: $SHELL, sonst /bin/bash
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PyQt6.QtCore import QProcess, Qt
from PyQt6.QtGui import QFont, QTextCursor
from PyQt6.QtWidgets import QLineEdit, QPlainTextEdit, QVBoxLayout, QWidget

from app.core.logging_setup import get_logger
from app.themes.dark_theme import PALETTE

logger = get_logger(__name__)


def _default_shell() -> tuple[str, list[str]]:
    """Ermittelt Programm und Argumente der plattformabhängigen Standard-Shell."""
    if sys.platform.startswith("win"):
        return "cmd.exe", []
    shell = os.environ.get("SHELL", "/bin/bash")
    return shell, []


class TerminalWidget(QWidget):
    """Eingebettetes Terminal mit Ausgabefenster und Eingabezeile.

    Args:
        working_directory: Startverzeichnis der Shell.
        parent: Optionales Eltern-Widget.
    """

    def __init__(self, working_directory: Path | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._working_directory = working_directory or Path.home()
        self._process: QProcess | None = None

        self._output_view = QPlainTextEdit(self)
        self._output_view.setReadOnly(True)
        self._output_view.setFont(QFont("Consolas, 'Cascadia Mono', monospace", 10))
        self._output_view.setStyleSheet(
            f"QPlainTextEdit {{ background-color: {PALETTE.background}; "
            f"color: {PALETTE.text_primary}; border: none; }}"
        )

        self._input_line = QLineEdit(self)
        self._input_line.setPlaceholderText("Befehl eingeben und Enter drücken …")
        self._input_line.returnPressed.connect(self._on_command_entered)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(self._output_view)
        layout.addWidget(self._input_line)

        self._start_shell()

    def set_working_directory(self, path: Path) -> None:
        """Ändert das Arbeitsverzeichnis und startet die Shell neu.

        Args:
            path: Neues Arbeitsverzeichnis für die nächste Terminalsitzung.
        """
        self._working_directory = path
        self._restart_shell()

    def _start_shell(self) -> None:
        program, arguments = _default_shell()
        process = QProcess(self)
        process.setWorkingDirectory(str(self._working_directory))
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.readyReadStandardOutput.connect(self._on_output_ready)
        process.finished.connect(self._on_process_finished)
        process.start(program, arguments)
        self._process = process
        self._append_output(f"--- Terminal gestartet in {self._working_directory} ---\n")

    def _restart_shell(self) -> None:
        if self._process is not None:
            self._process.kill()
            self._process.waitForFinished(1000)
        self._output_view.clear()
        self._start_shell()

    def _on_output_ready(self) -> None:
        if self._process is None:
            return
        raw_bytes = self._process.readAllStandardOutput()
        text = bytes(raw_bytes).decode("utf-8", errors="replace")
        self._append_output(text)

    def _on_process_finished(self, exit_code: int, exit_status) -> None:  # noqa: ARG002
        self._append_output(f"\n--- Shell beendet (Exit-Code {exit_code}) ---\n")

    def _append_output(self, text: str) -> None:
        self._output_view.moveCursor(QTextCursor.MoveOperation.End)
        self._output_view.insertPlainText(text)
        self._output_view.moveCursor(QTextCursor.MoveOperation.End)

    def _on_command_entered(self) -> None:
        command = self._input_line.text()
        self._input_line.clear()
        if self._process is None:
            return
        self._append_output(f"\n$ {command}\n")
        self._process.write(f"{command}\n".encode("utf-8"))

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt-Override
        if self._process is not None:
            self._process.kill()
            self._process.waitForFinished(500)
        super().closeEvent(event)
