"""Pandora® Commander – Plugin: Git-Status im Panel.

Blendet unterhalb der bestehenden Statuszeile eines Dateipanels
(``FilePanel``) eine zusätzliche, dezente Zeile ein, sobald das
aktuell angezeigte Verzeichnis Teil eines Git-Repositories ist:

    🌿 main · sauber
    🌿 feature/login · +2 ~3 ?1

    (+= gestagte, ~= geänderte, ?= unversionierte Dateien;
     "sauber" = keine offenen Änderungen laut ``git status``)

Die Zeile wird über den offiziellen Erweiterungspunkt
``on_panel_directory_changed`` aktualisiert und als zusätzliches
``QLabel`` an das Layout des jeweiligen ``FilePanel`` angehängt –
der Core selbst wird dabei nicht verändert. Befindet sich das neue
Verzeichnis nicht in einem Git-Repository, wird die Zeile
ausgeblendet, statt Platz zu verschwenden.

Die eigentliche ``git``-Abfrage (Branch-Name + ``status --porcelain``)
läuft je Navigation in einem eigenen Hintergrund-Thread, damit große
Repositories die Oberfläche nicht verzögern. Navigiert der Nutzer
währenddessen erneut, wird das Ergebnis der veralteten Abfrage anhand
eines Generationszählers pro Panel verworfen, sodass nie eine
überholte Statuszeile für das falsche Verzeichnis angezeigt wird.

Abhängigkeit: ``git`` muss im PATH verfügbar sein.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import QLabel

from app.core.logging_setup import get_logger
from app.plugins.plugin_manager import PandoraPlugin
from app.ui.widgets.file_panel import FilePanel

logger = get_logger(__name__)

_GIT_PATH = shutil.which("git")
_GIT_TIMEOUT_SECONDS = 8


def _format_status(branch_name: str, porcelain_lines: list[str]) -> str:
    if not porcelain_lines:
        return f"🌿 {branch_name} · sauber"

    staged = sum(1 for line in porcelain_lines if len(line) > 0 and line[0] not in (" ", "?"))
    modified = sum(1 for line in porcelain_lines if len(line) > 1 and line[1] == "M")
    untracked = sum(1 for line in porcelain_lines if line.startswith("??"))

    parts = []
    if staged:
        parts.append(f"+{staged}")
    if modified:
        parts.append(f"~{modified}")
    if untracked:
        parts.append(f"?{untracked}")

    summary = " ".join(parts) if parts else f"{len(porcelain_lines)} Änderung(en)"
    return f"🌿 {branch_name} · {summary}"


class _GitStatusWorker(QThread):
    """Ermittelt Branch und Status eines Verzeichnisses im Hintergrund."""

    finished_with_result = pyqtSignal(str)  # Formatierter Text, oder "" wenn kein Git-Repo.

    def __init__(self, path: Path) -> None:
        super().__init__()
        self._path = path

    def run(self) -> None:  # noqa: D102 - QThread-Standardmethode
        if _GIT_PATH is None:
            self.finished_with_result.emit("")
            return

        try:
            check = subprocess.run(
                [_GIT_PATH, "-C", str(self._path), "rev-parse", "--is-inside-work-tree"],
                capture_output=True,
                text=True,
                timeout=_GIT_TIMEOUT_SECONDS,
                check=False,
            )
            if check.returncode != 0 or check.stdout.strip() != "true":
                self.finished_with_result.emit("")
                return

            branch_result = subprocess.run(
                [_GIT_PATH, "-C", str(self._path), "branch", "--show-current"],
                capture_output=True,
                text=True,
                timeout=_GIT_TIMEOUT_SECONDS,
                check=False,
            )
            branch_name = branch_result.stdout.strip() or "(losgelöster HEAD)"

            status_result = subprocess.run(
                [_GIT_PATH, "-C", str(self._path), "status", "--porcelain"],
                capture_output=True,
                text=True,
                timeout=_GIT_TIMEOUT_SECONDS,
                check=False,
            )
            porcelain_lines = [line for line in status_result.stdout.splitlines() if line.strip()]

            self.finished_with_result.emit(_format_status(branch_name, porcelain_lines))
        except (OSError, subprocess.TimeoutExpired) as error:
            logger.debug("Git-Status konnte nicht ermittelt werden (%s): %s", self._path, error)
            self.finished_with_result.emit("")


class GitStatusPanelPlugin(PandoraPlugin):
    """Plugin, das Branch und Status eines Git-Repositories im Panel anzeigt."""

    name = "Git-Status im Panel"
    version = "1.0"
    author = "AKI_SystemDown®"
    description = (
        "Zeigt unterhalb der Statuszeile eines Panels Branch-Name und geänderte/"
        "gestagte/unversionierte Dateianzahl an, sobald das aktuelle Verzeichnis Teil "
        "eines Git-Repositories ist. Läuft im Hintergrund, benötigt 'git' im PATH."
    )

    def __init__(self) -> None:
        self._context: dict[str, Any] = {}
        self._labels: dict[int, QLabel] = {}
        self._generation: dict[int, int] = {}
        self._active_workers: list[_GitStatusWorker] = []

    def on_load(self, context: dict[str, Any]) -> None:
        self._context = context
        if _GIT_PATH is None:
            logger.warning("%s: 'git' nicht im PATH gefunden – Plugin bleibt inaktiv.", self.name)
        logger.info("%s geladen.", self.name)

    def on_unload(self) -> None:
        for label in self._labels.values():
            label.deleteLater()
        self._labels.clear()
        self._generation.clear()
        for worker in self._active_workers:
            worker.wait(50)
        self._active_workers.clear()

    def on_panel_directory_changed(self, context: dict[str, Any], panel: FilePanel, path: Path) -> None:
        if _GIT_PATH is None:
            return

        panel_id = id(panel)
        label = self._labels.get(panel_id)
        if label is None:
            label = QLabel()
            label.setStyleSheet("color: #8fbf8f; font-size: 11px; padding: 1px 4px;")
            label.setVisible(False)
            layout = panel.layout()
            if layout is not None:
                layout.addWidget(label)
            self._labels[panel_id] = label

        generation = self._generation.get(panel_id, 0) + 1
        self._generation[panel_id] = generation

        worker = _GitStatusWorker(path)
        worker.finished_with_result.connect(
            lambda text, panel_id=panel_id, generation=generation: self._on_result(panel_id, generation, text)
        )
        worker.finished.connect(
            lambda w=worker: self._active_workers.remove(w) if w in self._active_workers else None
        )
        self._active_workers.append(worker)
        worker.start()

    def _on_result(self, panel_id: int, generation: int, text: str) -> None:
        if self._generation.get(panel_id) != generation:
            return  # Zwischenzeitlich wurde weiternavigiert – veraltetes Ergebnis verwerfen.

        label = self._labels.get(panel_id)
        if label is None:
            return

        if text:
            label.setText(text)
            label.setVisible(True)
        else:
            label.setVisible(False)
