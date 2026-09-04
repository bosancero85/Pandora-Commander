"""Pandora® Commander – Massenumbenennung.

Erzeugt aus einer Liste von Dateien und einem Umbenennungsschema neue
Dateinamen. Unterstützt Platzhalter, fortlaufende Nummerierung,
Datumsangaben sowie regelbasierte Suchen/Ersetzen-Operationen über
reguläre Ausdrücke. Die eigentliche Umbenennung erfolgt erst nach
Bestätigung einer Vorschau (siehe ``preview_rename`` / ``apply_rename``).

Verfügbare Platzhalter im Muster:
    {name}    – ursprünglicher Dateiname ohne Endung
    {ext}     – ursprüngliche Dateiendung (ohne Punkt)
    {n}       – fortlaufende Nummer (siehe start/step/padding)
    {date}    – Änderungsdatum der Datei (YYYY-MM-DD)
    {time}    – Änderungszeit der Datei (HH-MM-SS)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app.core.logging_setup import get_logger

logger = get_logger(__name__)

_PLACEHOLDER_PATTERN = re.compile(r"\{(name|ext|n|date|time)\}")


@dataclass
class RenameRule:
    """Regeln für eine Massenumbenennungs-Operation.

    Attributes:
        pattern: Zielmuster mit Platzhaltern, z. B. "Urlaub_{n}{ext}".
        start_number: Startwert der fortlaufenden Nummerierung.
        step: Schrittweite der Nummerierung.
        padding: Mindestanzahl Stellen der Nummer (führende Nullen).
        search_regex: Optionaler regulärer Ausdruck, der im
            ursprünglichen Namen gesucht und ersetzt wird, bevor das
            Muster ausgewertet wird. Leer = kein Suchen/Ersetzen.
        replace_with: Ersetzungstext für search_regex.
        lowercase: Erzwingt Kleinschreibung des Ergebnisnamens.
        uppercase: Erzwingt Großschreibung des Ergebnisnamens.
    """

    pattern: str = "{name}{ext}"
    start_number: int = 1
    step: int = 1
    padding: int = 2
    search_regex: str = ""
    replace_with: str = ""
    lowercase: bool = False
    uppercase: bool = False


@dataclass
class RenamePreviewItem:
    """Ein einzelner Eintrag der Umbenennungsvorschau.

    Attributes:
        original_path: Ursprünglicher Pfad der Datei.
        new_name: Vorgeschlagener neuer Dateiname (ohne Verzeichnis).
        conflict: True, wenn der neue Name bereits vergeben ist
            (entweder mit einer anderen umzubenennenden Datei oder
            einer bestehenden Datei im Zielordner).
    """

    original_path: Path
    new_name: str
    conflict: bool = False


def _apply_regex(name: str, rule: RenameRule) -> str:
    """Wendet die optionale Suchen/Ersetzen-Regel auf einen Namen an."""
    if not rule.search_regex:
        return name
    try:
        return re.sub(rule.search_regex, rule.replace_with, name)
    except re.error as error:
        logger.warning("Ungültiger regulärer Ausdruck '%s': %s", rule.search_regex, error)
        return name


def build_new_name(path: Path, index: int, rule: RenameRule) -> str:
    """Berechnet den neuen Dateinamen für eine einzelne Datei.

    Args:
        path: Ursprünglicher Dateipfad.
        index: Position der Datei innerhalb der Auswahl (0-basiert),
            bestimmt zusammen mit start_number/step die Nummer {n}.
        rule: Die anzuwendende Umbenennungsregel.

    Returns:
        Der berechnete neue Dateiname (inklusive Endung).
    """
    stem = _apply_regex(path.stem, rule)
    suffix = path.suffix.lstrip(".")

    try:
        modified_time = datetime.fromtimestamp(path.stat().st_mtime)
    except OSError:
        modified_time = datetime.now()

    number = rule.start_number + index * rule.step
    number_str = str(number).zfill(rule.padding)

    new_name = rule.pattern.format(
        name=stem,
        ext=f".{suffix}" if suffix else "",
        n=number_str,
        date=modified_time.strftime("%Y-%m-%d"),
        time=modified_time.strftime("%H-%M-%S"),
    )

    if rule.lowercase:
        new_name = new_name.lower()
    if rule.uppercase:
        new_name = new_name.upper()

    return new_name


def preview_rename(paths: list[Path], rule: RenameRule) -> list[RenamePreviewItem]:
    """Erzeugt eine Vorschau der Umbenennung ohne Dateien zu verändern.

    Args:
        paths: Liste der umzubenennenden Dateien (gleicher Zielordner
            wird nicht vorausgesetzt, jede Datei bleibt in ihrem
            eigenen Verzeichnis).
        rule: Anzuwendende Umbenennungsregel.

    Returns:
        Liste von RenamePreviewItem, inklusive Konflikterkennung.
    """
    items: list[RenamePreviewItem] = []
    seen_targets: dict[Path, int] = {}

    for index, path in enumerate(paths):
        new_name = build_new_name(path, index, rule)
        target_path = path.parent / new_name

        conflict = False
        if target_path in seen_targets:
            conflict = True
        elif target_path.exists() and target_path != path:
            conflict = True

        seen_targets[target_path] = seen_targets.get(target_path, 0) + 1
        items.append(RenamePreviewItem(original_path=path, new_name=new_name, conflict=conflict))

    return items


@dataclass
class RenameOutcome:
    """Ergebnis einer tatsächlich durchgeführten Massenumbenennung."""

    renamed: list[tuple[Path, Path]]
    failed: list[tuple[Path, str]]


def apply_rename(items: list[RenamePreviewItem]) -> RenameOutcome:
    """Führt eine zuvor erzeugte Umbenennungsvorschau tatsächlich aus.

    Konfliktbehaftete Einträge werden übersprungen und als Fehler
    zurückgemeldet, um versehentliches Überschreiben zu verhindern.

    Args:
        items: Die anzuwendenden Vorschau-Einträge.

    Returns:
        RenameOutcome mit erfolgreichen und fehlgeschlagenen Umbenennungen.
    """
    renamed: list[tuple[Path, Path]] = []
    failed: list[tuple[Path, str]] = []

    for item in items:
        if item.conflict:
            failed.append((item.original_path, "Namenskonflikt – übersprungen"))
            continue
        target_path = item.original_path.parent / item.new_name
        try:
            item.original_path.rename(target_path)
            renamed.append((item.original_path, target_path))
        except OSError as error:
            logger.error("Umbenennung fehlgeschlagen für %s: %s", item.original_path, error)
            failed.append((item.original_path, str(error)))

    return RenameOutcome(renamed=renamed, failed=failed)
