"""Pandora® Commander – Hashwerkzeuge.

Berechnet MD5-, SHA1-, SHA256- und SHA512-Prüfsummen für einzelne
Dateien. Große Dateien werden blockweise gelesen, um den Speicher-
verbrauch konstant zu halten. Die eigentliche Berechnung läuft über
``HashWorker`` in einem eigenen QThread, damit die Oberfläche bei
großen Dateien nicht einfriert (siehe Abschnitt "Performance").

Verwendung:
    worker = HashWorker(paths=[Path("a.iso")], algorithms=[HashAlgorithm.SHA256])
    worker.result_ready.connect(...)
    worker.start()
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal

from app.core.logging_setup import get_logger

logger = get_logger(__name__)

#: Blockgröße beim Einlesen der Datei (1 MiB), Kompromiss zwischen
#: Systemaufrufen und Speicherverbrauch.
CHUNK_SIZE: int = 1024 * 1024


class HashAlgorithm(str, Enum):
    """Unterstützte Hash-Algorithmen."""

    MD5 = "md5"
    SHA1 = "sha1"
    SHA256 = "sha256"
    SHA512 = "sha512"


@dataclass
class HashResult:
    """Ergebnis der Hashberechnung für eine einzelne Datei.

    Attributes:
        path: Die gehashte Datei.
        digests: Zuordnung von Algorithmus zu Hexadezimal-Digest.
        error: Fehlermeldung, falls die Berechnung fehlgeschlagen ist.
    """

    path: Path
    digests: dict[HashAlgorithm, str] = field(default_factory=dict)
    error: str | None = None


def compute_hashes(
    path: Path,
    algorithms: list[HashAlgorithm],
    chunk_size: int = CHUNK_SIZE,
) -> HashResult:
    """Berechnet einen oder mehrere Hashes für eine Datei.

    Args:
        path: Pfad zur zu hashenden Datei.
        algorithms: Liste der zu berechnenden Algorithmen.
        chunk_size: Blockgröße beim Lesen der Datei in Bytes.

    Returns:
        Ein HashResult mit den berechneten Digests oder einer Fehlermeldung.
    """
    hashers = {algo: hashlib.new(algo.value) for algo in algorithms}
    try:
        with path.open("rb") as file_handle:
            while chunk := file_handle.read(chunk_size):
                for hasher in hashers.values():
                    hasher.update(chunk)
    except OSError as error:
        logger.error("Hash konnte nicht berechnet werden für %s: %s", path, error)
        return HashResult(path=path, error=str(error))

    digests = {algo: hasher.hexdigest() for algo, hasher in hashers.items()}
    return HashResult(path=path, digests=digests)


class HashWorker(QThread):
    """Berechnet Hashes für mehrere Dateien im Hintergrund.

    Signals:
        progress_changed: (aktueller Index, Gesamtanzahl, aktueller Pfad).
        file_hashed: HashResult einer einzelnen abgeschlossenen Datei.
        finished_all: Liste aller HashResults nach Abschluss.
    """

    progress_changed = pyqtSignal(int, int, str)
    file_hashed = pyqtSignal(object)
    finished_all = pyqtSignal(list)

    def __init__(
        self,
        paths: list[Path],
        algorithms: list[HashAlgorithm],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._paths = paths
        self._algorithms = algorithms
        self._cancelled = False

    def cancel(self) -> None:
        """Fordert den Abbruch der laufenden Berechnung an."""
        self._cancelled = True

    def run(self) -> None:  # noqa: D102 - Qt-Override
        results: list[HashResult] = []
        total = len(self._paths)
        for index, path in enumerate(self._paths, start=1):
            if self._cancelled:
                logger.info("Hashberechnung abgebrochen nach %d/%d Dateien", index - 1, total)
                break
            self.progress_changed.emit(index, total, str(path))
            result = compute_hashes(path, self._algorithms)
            results.append(result)
            self.file_hashed.emit(result)
        self.finished_all.emit(results)


def verify_hash(path: Path, algorithm: HashAlgorithm, expected: str) -> bool:
    """Prüft, ob der Hash einer Datei einem erwarteten Wert entspricht.

    Args:
        path: Zu prüfende Datei.
        algorithm: Zu verwendender Algorithmus.
        expected: Erwarteter Hexadezimal-Digest (Groß-/Kleinschreibung egal).

    Returns:
        True, wenn der berechnete Hash mit dem erwarteten Wert übereinstimmt.
    """
    result = compute_hashes(path, [algorithm])
    if result.error is not None:
        return False
    return result.digests.get(algorithm, "").lower() == expected.strip().lower()
