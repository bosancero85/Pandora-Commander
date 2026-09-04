"""Pandora® Commander – Dateisystem-Datenmodell.

Stellt eine typisierte Repräsentation einzelner Dateisystemeinträge
(FileEntry) sowie eine Funktion zum Einlesen eines Verzeichnisses
(scan_directory) bereit. Dieses Modul ist bewusst frei von Qt-
Abhängigkeiten gehalten, damit es unabhängig testbar ist; die
Anbindung an QAbstractTableModel für die Panel-Ansichten erfolgt in
einer späteren Datei (app/ui/widgets/file_panel_model.py), die auf
FileEntry und scan_directory aufbaut.
"""

from __future__ import annotations

import stat
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app.core.logging_setup import get_logger

logger = get_logger(__name__)

#: Größeneinheiten für die menschenlesbare Formatierung, aufsteigend.
_SIZE_UNITS: tuple[str, ...] = ("B", "KB", "MB", "GB", "TB", "PB")


class EntryType:
    """Symbolische Konstanten für die Art eines Dateisystemeintrags."""

    FILE = "file"
    DIRECTORY = "directory"
    SYMLINK = "symlink"
    PARENT = "parent"  # Sonderzeile ".." zum Aufsteigen ins Elternverzeichnis


@dataclass(frozen=True)
class FileEntry:
    """Ein einzelner Eintrag (Datei, Ordner oder Symlink) im Dateisystem.

    Attributes:
        name: Anzeigename (Dateiname inkl. Endung, ohne Pfad).
        path: Vollständiger, absoluter Pfad zum Eintrag.
        entry_type: Art des Eintrags, siehe EntryType.
        size_bytes: Größe in Bytes (0 für Ordner, sofern nicht
            rekursiv berechnet).
        modified: Zeitpunkt der letzten Änderung.
        is_hidden: Ob der Eintrag als versteckt gilt (Punkt-Dateien
            unter Unix bzw. das Hidden-Attribut unter Windows).
        extension: Dateiendung ohne Punkt, klein geschrieben
            (leer bei Ordnern oder Dateien ohne Endung).
        readable: Ob der Eintrag vom aktuellen Nutzer gelesen werden
            kann (relevant für Fehlerbehandlung in der UI).
    """

    name: str
    path: Path
    entry_type: str
    size_bytes: int
    modified: datetime
    is_hidden: bool
    extension: str
    readable: bool

    @property
    def is_directory(self) -> bool:
        """True, wenn der Eintrag ein Ordner oder die Parent-Zeile ist."""
        return self.entry_type in (EntryType.DIRECTORY, EntryType.PARENT)

    @property
    def display_size(self) -> str:
        """Menschenlesbare Größenangabe, z. B. '4.2 MB'.

        Ordner (außer bei rekursiver Berechnung an anderer Stelle)
        zeigen einen Gedankenstrich statt einer Größe von 0 Bytes,
        analog zum Verhalten von Total Commander & Co.
        """
        if self.is_directory:
            return "—"
        return format_size(self.size_bytes)

    @property
    def display_modified(self) -> str:
        """Änderungsdatum im Format 'DD.MM.YYYY HH:MM'."""
        return self.modified.strftime("%d.%m.%Y %H:%M")


def format_size(size_bytes: int) -> str:
    """Formatiert eine Byte-Anzahl menschenlesbar (z. B. '4.20 MB').

    Args:
        size_bytes: Größe in Bytes, muss >= 0 sein.

    Returns:
        Formatierte Größe mit passender Einheit.
    """
    if size_bytes < 0:
        raise ValueError("size_bytes darf nicht negativ sein")

    size = float(size_bytes)
    for unit in _SIZE_UNITS:
        if size < 1024.0 or unit == _SIZE_UNITS[-1]:
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.2f} {unit}"
        size /= 1024.0
    # Unerreichbar, aber für vollständige Typprüfung vorhanden.
    return f"{size:.2f} {_SIZE_UNITS[-1]}"


def _is_hidden(path: Path) -> bool:
    """Bestimmt, ob ein Pfad als versteckt gilt (plattformübergreifend).

    Unter Unix gelten Punkt-Dateien (".bashrc") als versteckt. Unter
    Windows wird zusätzlich das FILE_ATTRIBUTE_HIDDEN-Bit geprüft,
    sofern verfügbar.

    Args:
        path: Der zu prüfende Pfad.

    Returns:
        True, wenn der Eintrag als versteckt gilt.
    """
    if path.name.startswith("."):
        return True

    try:
        file_stat = path.stat()
        # FILE_ATTRIBUTE_HIDDEN existiert nur unter Windows; auf
        # anderen Plattformen ist das Attribut schlicht nicht gesetzt.
        hidden_attr = getattr(stat, "FILE_ATTRIBUTE_HIDDEN", None)
        st_file_attributes = getattr(file_stat, "st_file_attributes", None)
        if hidden_attr is not None and st_file_attributes is not None:
            return bool(st_file_attributes & hidden_attr)
    except OSError:
        pass

    return False


def _classify(path: Path) -> str:
    """Bestimmt den EntryType für einen gegebenen Pfad.

    Symlinks werden unabhängig von ihrem Ziel als SYMLINK klassifiziert,
    damit die UI sie später optisch kennzeichnen kann (z. B. kursiv
    oder mit Pfeil-Overlay-Icon).

    Args:
        path: Der zu klassifizierende Pfad.

    Returns:
        Einer der EntryType-Werte FILE, DIRECTORY oder SYMLINK.
    """
    if path.is_symlink():
        return EntryType.SYMLINK
    if path.is_dir():
        return EntryType.DIRECTORY
    return EntryType.FILE


def _build_entry(path: Path) -> FileEntry | None:
    """Erstellt einen FileEntry für einen einzelnen Pfad.

    Gibt None zurück (statt eine Exception zu werfen), wenn auf den
    Eintrag nicht zugegriffen werden kann (z. B. Berechtigungsfehler,
    zwischenzeitlich gelöschte Datei) – der Aufrufer überspringt
    solche Einträge, statt das gesamte Verzeichnislisting abzubrechen.

    Args:
        path: Vollständiger Pfad zum Dateisystemeintrag.

    Returns:
        Ein FileEntry oder None bei Zugriffsfehlern.
    """
    try:
        entry_type = _classify(path)
        file_stat = path.stat() if entry_type != EntryType.SYMLINK else path.lstat()

        size_bytes = 0
        if entry_type == EntryType.FILE:
            size_bytes = file_stat.st_size
        elif entry_type == EntryType.SYMLINK and path.is_file():
            # Für Datei-Symlinks wird die Zielgröße angezeigt, sofern
            # das Ziel existiert und lesbar ist.
            try:
                size_bytes = path.stat().st_size
            except OSError:
                size_bytes = 0

        extension = path.suffix.lstrip(".").lower() if entry_type == EntryType.FILE else ""

        return FileEntry(
            name=path.name,
            path=path,
            entry_type=entry_type,
            size_bytes=size_bytes,
            modified=datetime.fromtimestamp(file_stat.st_mtime),
            is_hidden=_is_hidden(path),
            extension=extension,
            readable=True,
        )
    except (OSError, PermissionError) as error:
        logger.debug("Eintrag konnte nicht gelesen werden: %s (%s)", path, error)
        return None


def scan_directory(
    directory: Path,
    show_hidden: bool = False,
    include_parent_entry: bool = True,
) -> list[FileEntry]:
    """Liest den Inhalt eines Verzeichnisses in eine Liste von FileEntry ein.

    Ordner werden bewusst vor Dateien sortiert (Commander-typisches
    Verhalten), jeweils alphabetisch, ohne Berücksichtigung von
    Groß-/Kleinschreibung. Einträge, auf die kein Zugriff möglich ist,
    werden übersprungen statt die gesamte Operation abzubrechen.

    Args:
        directory: Das einzulesende Verzeichnis. Muss existieren und
            ein Ordner sein.
        show_hidden: Ob versteckte Einträge mit aufgenommen werden.
        include_parent_entry: Ob eine ".."-Zeile für den Aufstieg ins
            Elternverzeichnis vorangestellt wird (sofern eines
            existiert).

    Returns:
        Sortierte Liste von FileEntry-Objekten.

    Raises:
        NotADirectoryError: Wenn directory kein Ordner ist.
        FileNotFoundError: Wenn directory nicht existiert.
        PermissionError: Wenn das Verzeichnis selbst nicht gelesen
            werden kann.
    """
    if not directory.exists():
        raise FileNotFoundError(f"Verzeichnis nicht gefunden: {directory}")
    if not directory.is_dir():
        raise NotADirectoryError(f"Kein Verzeichnis: {directory}")

    entries: list[FileEntry] = []

    if include_parent_entry and directory.parent != directory:
        entries.append(
            FileEntry(
                name="..",
                path=directory.parent,
                entry_type=EntryType.PARENT,
                size_bytes=0,
                modified=datetime.fromtimestamp(0),
                is_hidden=False,
                extension="",
                readable=True,
            )
        )

    try:
        children = list(directory.iterdir())
    except PermissionError as error:
        logger.warning("Keine Leseberechtigung für %s: %s", directory, error)
        raise

    scanned: list[FileEntry] = []
    for child_path in children:
        entry = _build_entry(child_path)
        if entry is None:
            continue
        if entry.is_hidden and not show_hidden:
            continue
        scanned.append(entry)

    scanned.sort(key=lambda e: (e.entry_type == EntryType.FILE, e.name.lower()))
    entries.extend(scanned)

    logger.debug("Verzeichnis gescannt: %s (%d Einträge)", directory, len(scanned))
    return entries


def calculate_directory_size(directory: Path) -> int:
    """Berechnet die Gesamtgröße eines Verzeichnisses rekursiv in Bytes.

    Nicht lesbare Unterobjekte werden übersprungen, statt die
    Berechnung abzubrechen – das Ergebnis ist dann eine Untergrenze
    der tatsächlichen Größe.

    Args:
        directory: Das zu berechnende Verzeichnis.

    Returns:
        Gesamtgröße in Bytes.
    """
    total_size = 0
    try:
        for item in directory.rglob("*"):
            try:
                if item.is_file() and not item.is_symlink():
                    total_size += item.stat().st_size
            except OSError:
                continue
    except OSError as error:
        logger.debug("Größenberechnung abgebrochen für %s: %s", directory, error)

    return total_size
