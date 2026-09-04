"""Pandora® Commander – Archivverwaltung.

Erlaubt das Öffnen von Archiven wie normale Ordner (Auflisten des
Inhalts), das Extrahieren einzelner Einträge oder des gesamten
Archivs sowie das Erstellen neuer Archive aus Dateien/Ordnern.

Unterstützt nativ (Python-Standardbibliothek):
    * ZIP        (zipfile)
    * TAR        (tarfile, unkomprimiert)
    * TAR.GZ/TGZ (tarfile, gzip-komprimiert)
    * TAR.BZ2    (tarfile, bzip2-komprimiert)
    * GZ         (einzelne gzip-Datei)
    * BZ2        (einzelne bzip2-Datei)

7Z wird unterstützt, wenn das optionale Paket ``py7zr`` installiert
ist; andernfalls wird beim Zugriff eine klare ArchiveError-Meldung
ausgelöst, statt die Anwendung abstürzen zu lassen.
"""

from __future__ import annotations

import bz2
import gzip
import shutil
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from app.core.logging_setup import get_logger

logger = get_logger(__name__)

#: Von der Anwendung als "Archiv" erkannte Dateiendungen.
ARCHIVE_EXTENSIONS: set[str] = {
    ".zip", ".tar", ".gz", ".tgz", ".bz2", ".tbz2", ".7z",
}


class ArchiveError(Exception):
    """Ausgelöst bei Fehlern rund um Archivoperationen."""


@dataclass
class ArchiveEntryInfo:
    """Ein einzelner Eintrag innerhalb eines Archivs.

    Attributes:
        name: Relativer Pfad innerhalb des Archivs.
        size_bytes: Unkomprimierte Größe in Bytes.
        is_dir: Ob der Eintrag ein Verzeichnis ist.
    """

    name: str
    size_bytes: int
    is_dir: bool


def is_archive(path: Path) -> bool:
    """Prüft anhand der Dateiendung, ob ein Pfad ein unterstütztes Archiv ist."""
    name = path.name.lower()
    if name.endswith(".tar.gz") or name.endswith(".tar.bz2"):
        return True
    return path.suffix.lower() in ARCHIVE_EXTENSIONS


def _is_7z(path: Path) -> bool:
    return path.suffix.lower() == ".7z"


def _get_py7zr():
    """Importiert py7zr bei Bedarf, mit klarer Fehlermeldung falls fehlend."""
    try:
        import py7zr  # type: ignore[import-untyped]
    except ImportError as error:
        raise ArchiveError(
            "7Z-Unterstützung erfordert das optionale Paket 'py7zr' "
            "(pip install py7zr)."
        ) from error
    return py7zr


def list_archive(path: Path) -> list[ArchiveEntryInfo]:
    """Listet den Inhalt eines Archivs auf, ohne es zu entpacken.

    Args:
        path: Pfad zur Archivdatei.

    Returns:
        Liste der enthaltenen Einträge.

    Raises:
        ArchiveError: Wenn das Format nicht unterstützt wird oder das
            Archiv beschädigt ist.
    """
    try:
        if path.suffix.lower() == ".zip":
            with zipfile.ZipFile(path) as archive:
                return [
                    ArchiveEntryInfo(info.filename, info.file_size, info.is_dir())
                    for info in archive.infolist()
                ]

        if _is_7z(path):
            py7zr = _get_py7zr()
            with py7zr.SevenZipFile(path, mode="r") as archive:
                return [
                    ArchiveEntryInfo(info.filename, info.uncompressed or 0, info.is_directory)
                    for info in archive.list()
                ]

        if tarfile.is_tarfile(path):
            with tarfile.open(path) as archive:
                return [
                    ArchiveEntryInfo(member.name, member.size, member.isdir())
                    for member in archive.getmembers()
                ]

        if path.suffix.lower() in {".gz", ".bz2"}:
            # Einzelne komprimierte Datei ohne Container-Struktur.
            inner_name = path.stem
            return [ArchiveEntryInfo(inner_name, -1, False)]

        raise ArchiveError(f"Nicht unterstütztes Archivformat: {path.suffix}")
    except (zipfile.BadZipFile, tarfile.TarError, OSError) as error:
        raise ArchiveError(f"Archiv konnte nicht gelesen werden: {error}") from error


def extract_archive(path: Path, destination: Path, members: list[str] | None = None) -> None:
    """Entpackt ein Archiv (vollständig oder ausgewählte Einträge).

    Args:
        path: Pfad zur Archivdatei.
        destination: Zielordner, wird bei Bedarf angelegt.
        members: Optionale Liste konkreter Einträge; None = alles.

    Raises:
        ArchiveError: Wenn das Format nicht unterstützt wird oder das
            Entpacken fehlschlägt.
    """
    destination.mkdir(parents=True, exist_ok=True)

    try:
        if path.suffix.lower() == ".zip":
            with zipfile.ZipFile(path) as archive:
                archive.extractall(destination, members=members)
            return

        if _is_7z(path):
            py7zr = _get_py7zr()
            with py7zr.SevenZipFile(path, mode="r") as archive:
                if members:
                    archive.extract(path=destination, targets=members)
                else:
                    archive.extractall(path=destination)
            return

        if tarfile.is_tarfile(path):
            with tarfile.open(path) as archive:
                selected = None
                if members:
                    selected = [m for m in archive.getmembers() if m.name in members]
                archive.extractall(destination, members=selected, filter="data")
            return

        if path.suffix.lower() == ".gz":
            target_file = destination / path.stem
            with gzip.open(path, "rb") as source, target_file.open("wb") as target:
                shutil.copyfileobj(source, target)
            return

        if path.suffix.lower() == ".bz2":
            target_file = destination / path.stem
            with bz2.open(path, "rb") as source, target_file.open("wb") as target:
                shutil.copyfileobj(source, target)
            return

        raise ArchiveError(f"Nicht unterstütztes Archivformat: {path.suffix}")
    except (zipfile.BadZipFile, tarfile.TarError, OSError) as error:
        raise ArchiveError(f"Archiv konnte nicht entpackt werden: {error}") from error


def create_archive(
    sources: list[Path],
    destination: Path,
    archive_format: str = "zip",
) -> None:
    """Erstellt ein neues Archiv aus einer Liste von Dateien/Ordnern.

    Args:
        sources: Zu archivierende Dateien und/oder Ordner.
        destination: Zielpfad der neuen Archivdatei (inklusive Endung).
        archive_format: Eines von "zip", "tar", "tar.gz", "tar.bz2", "7z".

    Raises:
        ArchiveError: Bei nicht unterstütztem Format oder Schreibfehlern.
    """
    try:
        if archive_format == "zip":
            with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
                for source in sources:
                    _add_to_zip(archive, source)
            return

        if archive_format in {"tar", "tar.gz", "tar.bz2"}:
            mode_map = {"tar": "w", "tar.gz": "w:gz", "tar.bz2": "w:bz2"}
            with tarfile.open(destination, mode_map[archive_format]) as archive:
                for source in sources:
                    archive.add(source, arcname=source.name)
            return

        if archive_format == "7z":
            py7zr = _get_py7zr()
            with py7zr.SevenZipFile(destination, mode="w") as archive:
                for source in sources:
                    archive.writeall(source, arcname=source.name)
            return

        raise ArchiveError(f"Nicht unterstütztes Zielformat: {archive_format}")
    except OSError as error:
        raise ArchiveError(f"Archiv konnte nicht erstellt werden: {error}") from error


def _add_to_zip(archive: zipfile.ZipFile, source: Path) -> None:
    """Fügt eine Datei oder rekursiv einen Ordner zu einem ZipFile hinzu."""
    if source.is_file():
        archive.write(source, arcname=source.name)
        return
    for entry in source.rglob("*"):
        if entry.is_file():
            archive.write(entry, arcname=str(Path(source.name) / entry.relative_to(source)))
