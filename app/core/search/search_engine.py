"""Pandora® Commander – Dateisuche.

Durchsucht einen Verzeichnisbaum rekursiv nach Dateien, die einer
Kombination von Kriterien entsprechen: Namensmuster (Regex oder
Wildcard), Größe, Änderungsdatum, Dateityp (Endung) und optional
Textinhalt. Läuft über ``SearchWorker`` in einem eigenen QThread und
meldet Treffer fortlaufend über ein Signal, damit Ergebnisse bereits
während der Suche in der Oberfläche erscheinen können.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal

from app.core.logging_setup import get_logger

logger = get_logger(__name__)

#: Dateien, die größer als dieser Wert sind, werden bei der
#: Inhaltssuche nicht mehr eingelesen (Schutz vor riesigen Binärdateien).
MAX_CONTENT_SEARCH_SIZE: int = 50 * 1024 * 1024  # 50 MiB


class NamePatternMode(str, Enum):
    """Art des Namensmusters bei der Suche."""

    WILDCARD = "wildcard"
    REGEX = "regex"


@dataclass
class SearchCriteria:
    """Kriterien für eine Dateisuche.

    Attributes:
        root_path: Wurzelverzeichnis, ab dem rekursiv gesucht wird.
        name_pattern: Suchmuster für den Dateinamen. Leer = alle Namen.
        pattern_mode: Ob name_pattern als Wildcard (*, ?) oder Regex
            interpretiert wird.
        case_sensitive: Ob die Namenssuche Groß-/Kleinschreibung beachtet.
        min_size_bytes: Minimale Dateigröße in Bytes (None = keine Grenze).
        max_size_bytes: Maximale Dateigröße in Bytes (None = keine Grenze).
        modified_after: Nur Dateien, die danach geändert wurden.
        modified_before: Nur Dateien, die davor geändert wurden.
        extensions: Liste erlaubter Dateiendungen ohne Punkt, z. B.
            ["txt", "py"]. Leer = alle Endungen.
        content_pattern: Optionaler Text/Regex, der im Dateiinhalt
            gesucht wird. Leer = keine Inhaltssuche.
        content_is_regex: Ob content_pattern als Regex interpretiert wird.
        include_subfolders: Ob rekursiv in Unterordner gesucht wird.
    """

    root_path: Path
    name_pattern: str = ""
    pattern_mode: NamePatternMode = NamePatternMode.WILDCARD
    case_sensitive: bool = False
    min_size_bytes: int | None = None
    max_size_bytes: int | None = None
    modified_after: datetime | None = None
    modified_before: datetime | None = None
    extensions: list[str] | None = None
    content_pattern: str = ""
    content_is_regex: bool = False
    include_subfolders: bool = True


@dataclass
class SearchHit:
    """Ein einzelner Treffer der Dateisuche.

    Attributes:
        path: Pfad der gefundenen Datei.
        size_bytes: Dateigröße in Bytes.
        modified: Änderungsdatum der Datei.
        matched_line: Bei Inhaltssuche die erste passende Zeile (gekürzt).
    """

    path: Path
    size_bytes: int
    modified: datetime
    matched_line: str | None = None


def _name_matches(name: str, criteria: SearchCriteria) -> bool:
    if not criteria.name_pattern:
        return True

    haystack = name if criteria.case_sensitive else name.lower()
    needle = criteria.name_pattern if criteria.case_sensitive else criteria.name_pattern.lower()

    if criteria.pattern_mode == NamePatternMode.WILDCARD:
        return fnmatch.fnmatch(haystack, needle)

    flags = 0 if criteria.case_sensitive else re.IGNORECASE
    try:
        return re.search(criteria.name_pattern, name, flags) is not None
    except re.error as error:
        logger.warning("Ungültiger regulärer Ausdruck '%s': %s", criteria.name_pattern, error)
        return False


def _size_matches(size: int, criteria: SearchCriteria) -> bool:
    if criteria.min_size_bytes is not None and size < criteria.min_size_bytes:
        return False
    if criteria.max_size_bytes is not None and size > criteria.max_size_bytes:
        return False
    return True


def _date_matches(modified: datetime, criteria: SearchCriteria) -> bool:
    if criteria.modified_after is not None and modified < criteria.modified_after:
        return False
    if criteria.modified_before is not None and modified > criteria.modified_before:
        return False
    return True


def _extension_matches(path: Path, criteria: SearchCriteria) -> bool:
    if not criteria.extensions:
        return True
    suffix = path.suffix.lstrip(".").lower()
    return suffix in {ext.lower().lstrip(".") for ext in criteria.extensions}


def _search_content(path: Path, criteria: SearchCriteria, size: int) -> str | None:
    """Sucht nach content_pattern im Text einer Datei.

    Returns:
        Die erste Zeile mit Treffer (max. 200 Zeichen) oder None.
    """
    if not criteria.content_pattern or size > MAX_CONTENT_SEARCH_SIZE:
        return None

    try:
        with path.open("r", encoding="utf-8", errors="ignore") as file_handle:
            for line in file_handle:
                if criteria.content_is_regex:
                    if re.search(criteria.content_pattern, line):
                        return line.strip()[:200]
                else:
                    haystack = line if criteria.case_sensitive else line.lower()
                    needle = (
                        criteria.content_pattern
                        if criteria.case_sensitive
                        else criteria.content_pattern.lower()
                    )
                    if needle in haystack:
                        return line.strip()[:200]
    except OSError:
        return None

    return None


def search_files(criteria: SearchCriteria, hit_callback=None, is_cancelled=None) -> list[SearchHit]:
    """Führt eine Dateisuche anhand der übergebenen Kriterien aus.

    Args:
        criteria: Die anzuwendenden Suchkriterien.
        hit_callback: Optionaler Callback(SearchHit), pro Treffer aufgerufen.
        is_cancelled: Optionaler Callback() -> bool zum vorzeitigen Abbruch.

    Returns:
        Liste aller gefundenen SearchHit-Objekte.
    """
    hits: list[SearchHit] = []
    iterator = criteria.root_path.rglob("*") if criteria.include_subfolders else criteria.root_path.glob("*")

    for entry in iterator:
        if is_cancelled is not None and is_cancelled():
            logger.info("Suche abgebrochen nach %d Treffern", len(hits))
            break

        if not entry.is_file():
            continue
        if not _name_matches(entry.name, criteria):
            continue
        if not _extension_matches(entry, criteria):
            continue

        try:
            stat_result = entry.stat()
        except OSError:
            continue

        size = stat_result.st_size
        modified = datetime.fromtimestamp(stat_result.st_mtime)

        if not _size_matches(size, criteria):
            continue
        if not _date_matches(modified, criteria):
            continue

        matched_line = None
        if criteria.content_pattern:
            matched_line = _search_content(entry, criteria, size)
            if matched_line is None:
                continue

        hit = SearchHit(path=entry, size_bytes=size, modified=modified, matched_line=matched_line)
        hits.append(hit)
        if hit_callback is not None:
            hit_callback(hit)

    return hits


class SearchWorker(QThread):
    """Führt eine Dateisuche im Hintergrund aus.

    Signals:
        hit_found: Ein einzelner SearchHit, sobald er gefunden wurde.
        search_finished: Gesamtanzahl gefundener Treffer nach Abschluss.
    """

    hit_found = pyqtSignal(object)
    search_finished = pyqtSignal(int)

    def __init__(self, criteria: SearchCriteria, parent=None) -> None:
        super().__init__(parent)
        self._criteria = criteria
        self._cancelled = False

    def cancel(self) -> None:
        """Fordert den Abbruch der laufenden Suche an."""
        self._cancelled = True

    def run(self) -> None:  # noqa: D102 - Qt-Override
        hits = search_files(
            self._criteria,
            hit_callback=lambda hit: self.hit_found.emit(hit),
            is_cancelled=lambda: self._cancelled,
        )
        self.search_finished.emit(len(hits))
