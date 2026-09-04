"""Pandora® Commander – Plugin: Checksummen-Export.

Fügt dem Rechtsklick-Kontextmenü der Dateipanels einen Untermenüpunkt
"Prüfsumme erzeugen" hinzu, sobald mindestens eine Datei markiert
ist. Zur Auswahl stehen MD5, SHA-1, SHA-256 und SHA-512. Für jede
markierte Datei wird eine Prüfsummen-Datei nach dem Standardformat
der jeweiligen Kommandozeilenwerkzeuge (``md5sum``, ``sha1sum`` usw.)
im selben Verzeichnis abgelegt, z. B.:

    dokument.pdf          → dokument.pdf.sha256
    Inhalt: "<hash>  dokument.pdf\\n"

Zusätzlich bietet das Kontextmenü bei genau einer markierten
``.md5``/``.sha1``/``.sha256``/``.sha512``-Datei den Eintrag
"Prüfsumme verifizieren", der die referenzierte Datei erneut hasht
und das Ergebnis mit dem gespeicherten Wert vergleicht – nützlich
nach dem Download oder Kopieren großer Dateien.

Große Dateien werden in einem Hintergrund-Thread gehasht, damit die
Oberfläche bei vielen oder großen markierten Dateien nicht
einfriert; der Fortschritt erscheint in der Statusleiste.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Callable

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QMenu, QMessageBox

from app.core.logging_setup import get_logger
from app.plugins.plugin_manager import PandoraPlugin

logger = get_logger(__name__)

_HASH_CHUNK_SIZE = 1024 * 1024
_SUPPORTED_ALGORITHMS: dict[str, str] = {
    "MD5": "md5",
    "SHA-1": "sha1",
    "SHA-256": "sha256",
    "SHA-512": "sha512",
}
_EXTENSION_TO_ALGORITHM: dict[str, str] = {
    ".md5": "md5",
    ".sha1": "sha1",
    ".sha256": "sha256",
    ".sha512": "sha512",
}
_CHECKSUM_LINE_PATTERN = re.compile(r"^([0-9a-fA-F]+)\s+\*?(.+)$")


def _hash_file(path: Path, algorithm: str) -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as handle:
        while chunk := handle.read(_HASH_CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


class _ChecksumWorker(QThread):
    """Erzeugt Prüfsummen-Dateien für eine Liste markierter Dateien im Hintergrund."""

    file_finished = pyqtSignal(Path, bool, str)  # Pfad, Erfolg, Meldung
    all_finished = pyqtSignal(int, int)  # Erfolge, Gesamtanzahl

    def __init__(self, paths: list[Path], algorithm: str) -> None:
        super().__init__()
        self._paths = paths
        self._algorithm = algorithm

    def run(self) -> None:  # noqa: D102 - QThread-Standardmethode
        success_count = 0
        for path in self._paths:
            try:
                digest_hex = _hash_file(path, self._algorithm)
                extension = f".{self._algorithm}"
                checksum_path = path.with_name(path.name + extension)
                checksum_path.write_text(f"{digest_hex}  {path.name}\n", encoding="utf-8")
                success_count += 1
                self.file_finished.emit(path, True, str(checksum_path))
            except OSError as error:
                self.file_finished.emit(path, False, str(error))
        self.all_finished.emit(success_count, len(self._paths))


class ChecksumExportPlugin(PandoraPlugin):
    """Plugin zum Erzeugen und Verifizieren von Prüfsummen-Dateien."""

    name = "Checksummen-Export"
    version = "1.0"
    author = "AKI_SystemDown®"
    description = (
        "Fügt dem Kontextmenü 'Prüfsumme erzeugen' (MD5/SHA-1/SHA-256/SHA-512) sowie "
        "'Prüfsumme verifizieren' hinzu. Erzeugte Dateien folgen dem Format der "
        "üblichen Kommandozeilenwerkzeuge (z. B. sha256sum)."
    )

    def __init__(self) -> None:
        self._context: dict[str, Any] = {}
        self._active_workers: list[_ChecksumWorker] = []

    def on_load(self, context: dict[str, Any]) -> None:
        self._context = context
        logger.info("%s geladen.", self.name)

    def on_unload(self) -> None:
        for worker in self._active_workers:
            worker.wait(50)
        self._active_workers.clear()

    def build_context_menu_entries(
        self, context: dict[str, Any], selected_paths: list[Path]
    ) -> list[QAction]:
        file_paths = [path for path in selected_paths if path.is_file()]
        if not file_paths:
            return []

        main_window = context.get("main_window")
        active_panel = context.get("active_panel")
        actions: list[QAction] = []

        create_menu = QMenu("Prüfsumme erzeugen", main_window)
        for label, algorithm in _SUPPORTED_ALGORITHMS.items():
            sub_action = QAction(label, main_window)
            sub_action.triggered.connect(
                lambda checked=False, algo=algorithm, paths=file_paths, panel=active_panel: (
                    self._create_checksums(paths, algo, panel)
                )
            )
            create_menu.addAction(sub_action)
        actions.append(create_menu.menuAction())

        if len(file_paths) == 1 and file_paths[0].suffix.lower() in _EXTENSION_TO_ALGORITHM:
            verify_action = QAction("Prüfsumme verifizieren", main_window)
            verify_action.triggered.connect(
                lambda checked=False, checksum_file=file_paths[0]: self._verify_checksum(checksum_file)
            )
            actions.append(verify_action)

        return actions

    def _create_checksums(self, paths: list[Path], algorithm: str, panel: Any) -> None:
        status_bar = self._status_bar()
        if status_bar is not None:
            status_bar.showMessage(f"Erzeuge {algorithm.upper()}-Prüfsummen für {len(paths)} Datei(en) …")

        worker = _ChecksumWorker(paths, algorithm)
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
            logger.error("Prüfsumme für %s konnte nicht erzeugt werden: %s", path, message)

    def _on_all_finished(self, success_count: int, total: int, panel: Any) -> None:
        status_bar = self._status_bar()
        if status_bar is not None:
            if success_count == total:
                status_bar.showMessage(f"{success_count} Prüfsummen-Datei(en) erzeugt.", 5000)
            else:
                status_bar.showMessage(
                    f"{success_count} von {total} Prüfsummen-Datei(en) erzeugt – "
                    f"{total - success_count} fehlgeschlagen.",
                    8000,
                )
        if panel is not None and hasattr(panel, "refresh"):
            panel.refresh()

    def _verify_checksum(self, checksum_file: Path) -> None:
        main_window = self._context.get("main_window")
        algorithm = _EXTENSION_TO_ALGORITHM[checksum_file.suffix.lower()]

        try:
            content = checksum_file.read_text(encoding="utf-8").strip()
        except OSError as error:
            QMessageBox.critical(
                main_window, "Fehler", f"Prüfsummen-Datei konnte nicht gelesen werden: {error}"
            )
            return

        match = _CHECKSUM_LINE_PATTERN.match(content.splitlines()[0] if content else "")
        if not match:
            QMessageBox.warning(
                main_window,
                "Ungültiges Format",
                "Die Prüfsummen-Datei entspricht nicht dem erwarteten Format "
                "'<hash>  <dateiname>'.",
            )
            return

        expected_hash, referenced_name = match.group(1).lower(), match.group(2).strip()
        referenced_path = checksum_file.with_name(referenced_name)

        if not referenced_path.is_file():
            QMessageBox.warning(
                main_window,
                "Datei nicht gefunden",
                f"Die referenzierte Datei '{referenced_name}' wurde im selben "
                "Verzeichnis nicht gefunden.",
            )
            return

        try:
            actual_hash = _hash_file(referenced_path, algorithm)
        except OSError as error:
            QMessageBox.critical(main_window, "Fehler", f"Datei konnte nicht gehasht werden: {error}")
            return

        if actual_hash.lower() == expected_hash:
            QMessageBox.information(
                main_window,
                "Prüfsumme gültig",
                f"'{referenced_name}' stimmt mit der gespeicherten {algorithm.upper()}-Prüfsumme überein.",
            )
        else:
            QMessageBox.critical(
                main_window,
                "Prüfsumme ungültig!",
                f"'{referenced_name}' stimmt NICHT mit der gespeicherten Prüfsumme überein.\n\n"
                f"Erwartet: {expected_hash}\n"
                f"Ermittelt: {actual_hash}",
            )

    def _status_bar(self) -> Any | None:
        main_window = self._context.get("main_window")
        return getattr(main_window, "statusBar", lambda: None)()
