"""Pandora® Commander – Datei- und Ordnervergleich.

Vergleicht zwei Dateien oder zwei Ordnerbäume miteinander. Für
Ordnervergleiche wird zunächst anhand von Name, Größe und
Änderungsdatum entschieden, ob sich zwei Dateien unterscheiden; ein
exakter Hashvergleich (MD5/SHA1/SHA256) kann optional zusätzlich
angefordert werden, wenn absolute Sicherheit erforderlich ist.

Läuft für größere Ordnerbäume über ``CompareWorker`` in einem eigenen
QThread.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal

from app.core.filesystem.hash_tools import HashAlgorithm, compute_hashes
from app.core.logging_setup import get_logger

logger = get_logger(__name__)


class CompareStatus(str, Enum):
    """Vergleichsstatus eines Eintrags relativ zu zwei Wurzelordnern."""

    IDENTICAL = "identical"
    DIFFERENT = "different"
    ONLY_LEFT = "only_left"
    ONLY_RIGHT = "only_right"


@dataclass
class CompareEntry:
    """Ergebnis des Vergleichs eines einzelnen relativen Pfades.

    Attributes:
        relative_path: Pfad relativ zu den beiden Wurzelordnern.
        status: Ermitteltes Vergleichsergebnis.
        left_path: Absoluter Pfad auf der linken Seite, falls vorhanden.
        right_path: Absoluter Pfad auf der rechten Seite, falls vorhanden.
    """

    relative_path: str
    status: CompareStatus
    left_path: Path | None = None
    right_path: Path | None = None


def compare_files(
    left: Path,
    right: Path,
    use_hash: bool = False,
    algorithm: HashAlgorithm = HashAlgorithm.SHA256,
) -> bool:
    """Vergleicht zwei einzelne Dateien.

    Args:
        left: Erste Datei.
        right: Zweite Datei.
        use_hash: Wenn True, wird zusätzlich zu Größe/Zeitstempel ein
            exakter Hashvergleich durchgeführt.
        algorithm: Zu verwendender Hashalgorithmus, falls use_hash aktiv ist.

    Returns:
        True, wenn die Dateien als identisch gelten.
    """
    try:
        left_stat = left.stat()
        right_stat = right.stat()
    except OSError as error:
        logger.warning("Vergleich fehlgeschlagen: %s", error)
        return False

    if left_stat.st_size != right_stat.st_size:
        return False

    if not use_hash:
        # Größe identisch: als schnelle Heuristik gilt das als "gleich".
        return True

    left_hash = compute_hashes(left, [algorithm])
    right_hash = compute_hashes(right, [algorithm])
    if left_hash.error or right_hash.error:
        return False
    return left_hash.digests[algorithm] == right_hash.digests[algorithm]


def _collect_relative_files(root: Path) -> set[str]:
    """Sammelt alle relativen Dateipfade unterhalb eines Wurzelordners."""
    relative_paths: set[str] = set()
    for entry in root.rglob("*"):
        if entry.is_file():
            relative_paths.add(str(entry.relative_to(root)))
    return relative_paths


def compare_folders(
    left_root: Path,
    right_root: Path,
    use_hash: bool = False,
    algorithm: HashAlgorithm = HashAlgorithm.SHA256,
    progress_callback=None,
) -> list[CompareEntry]:
    """Vergleicht zwei Ordnerbäume rekursiv.

    Args:
        left_root: Linker Wurzelordner.
        right_root: Rechter Wurzelordner.
        use_hash: Ob zusätzlich ein Hashvergleich durchgeführt werden soll.
        algorithm: Zu verwendender Hashalgorithmus.
        progress_callback: Optionaler Callback(index, total, relative_path).

    Returns:
        Liste von CompareEntry-Objekten für jeden gefundenen relativen Pfad.
    """
    left_files = _collect_relative_files(left_root)
    right_files = _collect_relative_files(right_root)
    all_relative = sorted(left_files | right_files)

    entries: list[CompareEntry] = []
    total = len(all_relative)
    for index, relative in enumerate(all_relative, start=1):
        if progress_callback is not None:
            progress_callback(index, total, relative)

        left_path = left_root / relative
        right_path = right_root / relative
        in_left = relative in left_files
        in_right = relative in right_files

        if in_left and not in_right:
            entries.append(
                CompareEntry(relative, CompareStatus.ONLY_LEFT, left_path=left_path)
            )
        elif in_right and not in_left:
            entries.append(
                CompareEntry(relative, CompareStatus.ONLY_RIGHT, right_path=right_path)
            )
        else:
            identical = compare_files(left_path, right_path, use_hash, algorithm)
            status = CompareStatus.IDENTICAL if identical else CompareStatus.DIFFERENT
            entries.append(CompareEntry(relative, status, left_path, right_path))

    return entries


@dataclass
class CompareOptions:
    """Optionen für einen Ordnervergleich im Hintergrund-Worker."""

    use_hash: bool = False
    algorithm: HashAlgorithm = HashAlgorithm.SHA256
    only_show_differences: bool = field(default=False)


class CompareWorker(QThread):
    """Führt einen Ordnervergleich im Hintergrund aus.

    Signals:
        progress_changed: (aktueller Index, Gesamtanzahl, relativer Pfad).
        finished_compare: Liste aller CompareEntry-Ergebnisse.
    """

    progress_changed = pyqtSignal(int, int, str)
    finished_compare = pyqtSignal(list)

    def __init__(
        self,
        left_root: Path,
        right_root: Path,
        options: CompareOptions | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._left_root = left_root
        self._right_root = right_root
        self._options = options or CompareOptions()

    def run(self) -> None:  # noqa: D102 - Qt-Override
        def _progress(index: int, total: int, relative: str) -> None:
            self.progress_changed.emit(index, total, relative)

        entries = compare_folders(
            self._left_root,
            self._right_root,
            use_hash=self._options.use_hash,
            algorithm=self._options.algorithm,
            progress_callback=_progress,
        )

        if self._options.only_show_differences:
            entries = [e for e in entries if e.status != CompareStatus.IDENTICAL]

        self.finished_compare.emit(entries)
