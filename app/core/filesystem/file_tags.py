"""Pandora® Commander – Datei-Tags & Farbmarkierungen.

Verwaltet frei vergebbare Text-Tags sowie eine Farbmarkierung
(analog zu macOS-Finder-Tags bzw. Total-Commander-Farbmarkierungen)
für beliebige Dateien und Ordner. Die Zuordnung ist reiner Metadaten-
Zustand der Anwendung (nicht im Dateisystem selbst gespeichert) und
wird als JSON-Datei im Konfigurationsverzeichnis persistiert.

Bewusst Qt-frei gehalten (wie app.core.filesystem.file_model), damit
es unabhängig von der Oberfläche testbar bleibt. Die Anbindung an die
Panel-Tabelle erfolgt in app/ui/widgets/file_panel_model.py.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from app.core.config import CONFIG_DIR
from app.core.logging_setup import get_logger

logger = get_logger(__name__)

#: Standard-Ablageort der Tag-/Farbmarkierungs-Metadaten.
DEFAULT_STORAGE_FILE: Path = CONFIG_DIR / "file_tags.json"

#: Feste Palette benannter Farbmarkierungen (Name -> Hex-Farbwert),
#: analog zu den Finder-/Total-Commander-Farbmarkierungen. Wird sowohl
#: für das Kontextmenü als auch für die Zeilenfärbung in der
#: Panel-Tabelle verwendet.
LABEL_COLORS: dict[str, str] = {
    "Rot": "#e5484d",
    "Orange": "#f5a623",
    "Gelb": "#f7d354",
    "Grün": "#3fb950",
    "Blau": "#5b8def",
    "Lila": "#a371f7",
    "Grau": "#9a9da2",
}

#: Vorgeschlagene Standard-Tags, die im Kontextmenü immer zum
#: schnellen Umschalten angeboten werden (zusätzlich zu bereits
#: anderswo verwendeten, eigenen Tags – siehe all_known_tags()).
SUGGESTED_TAGS: tuple[str, ...] = ("Wichtig", "In Bearbeitung", "Erledigt", "Archiv")


@dataclass
class FileTagInfo:
    """Tags und Farbmarkierung eines einzelnen Dateisystemeintrags.

    Attributes:
        tags: Liste frei vergebener Text-Tags (ohne feste Reihenfolge,
            Duplikate werden beim Setzen automatisch entfernt).
        color: Name einer Farbmarkierung aus LABEL_COLORS, oder None
            für "keine Farbmarkierung".
    """

    tags: list[str] = field(default_factory=list)
    color: str | None = None

    @property
    def is_empty(self) -> bool:
        """True, wenn weder Tags noch eine Farbmarkierung gesetzt sind."""
        return not self.tags and self.color is None


class TagsManager:
    """Lädt, verwaltet und persistiert Datei-Tags & Farbmarkierungen.

    Jede Änderung wird sofort auf die Festplatte geschrieben (robust
    gegen Abstürze/erzwungenes Beenden), Lese-/Schreibfehler werden
    geloggt statt die Anwendung abstürzen zu lassen.
    """

    def __init__(self, storage_path: Path | None = None) -> None:
        self._storage_path = storage_path or DEFAULT_STORAGE_FILE
        self._entries: dict[str, FileTagInfo] = {}
        self._load()

    # ------------------------------------------------------------------
    # Persistenz
    # ------------------------------------------------------------------

    def _load(self) -> None:
        if not self._storage_path.exists():
            return
        try:
            raw = json.loads(self._storage_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            logger.warning("Tag-Metadaten konnten nicht gelesen werden: %s", error)
            return

        entries: dict[str, FileTagInfo] = {}
        for key, value in raw.items():
            if not isinstance(value, dict):
                continue
            tags = [str(tag) for tag in value.get("tags", []) if str(tag).strip()]
            color = value.get("color")
            if color is not None and color not in LABEL_COLORS:
                color = None
            if tags or color is not None:
                entries[key] = FileTagInfo(tags=sorted(set(tags)), color=color)
        self._entries = entries

    def _save(self) -> None:
        try:
            self._storage_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                key: {"tags": info.tags, "color": info.color}
                for key, info in self._entries.items()
            }
            self._storage_path.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        except OSError as error:
            logger.warning("Tag-Metadaten konnten nicht gespeichert werden: %s", error)

    # ------------------------------------------------------------------
    # Abfragen
    # ------------------------------------------------------------------

    def get(self, path: Path) -> FileTagInfo:
        """Liefert Tags/Farbmarkierung eines Pfades (nie KeyError).

        Args:
            path: Abzufragender Dateisystempfad.

        Returns:
            Eine FileTagInfo; leer (is_empty), falls nichts hinterlegt ist.
        """
        info = self._entries.get(str(path))
        if info is None:
            return FileTagInfo()
        return FileTagInfo(tags=list(info.tags), color=info.color)

    def all_known_tags(self) -> list[str]:
        """Alle aktuell irgendwo verwendeten Tags, alphabetisch sortiert."""
        known: set[str] = set()
        for info in self._entries.values():
            known.update(info.tags)
        return sorted(known)

    # ------------------------------------------------------------------
    # Änderungen
    # ------------------------------------------------------------------

    def set_color(self, path: Path, color: str | None) -> None:
        """Setzt (oder löscht) die Farbmarkierung eines Pfades.

        Args:
            path: Betroffener Dateisystempfad.
            color: Name aus LABEL_COLORS, oder None zum Entfernen.

        Raises:
            ValueError: Wenn color gesetzt ist, aber kein bekannter
                Farbname aus LABEL_COLORS ist.
        """
        if color is not None and color not in LABEL_COLORS:
            raise ValueError(f"Unbekannte Farbmarkierung: {color!r}")
        info = self._entries.get(str(path), FileTagInfo())
        info.color = color
        self._store(path, info)

    def set_tags(self, path: Path, tags: list[str]) -> None:
        """Setzt die vollständige Tag-Liste eines Pfades (überschreibt vorherige).

        Args:
            path: Betroffener Dateisystempfad.
            tags: Neue Tag-Liste; leere/Whitespace-Einträge werden
                verworfen, Duplikate entfernt.
        """
        cleaned = sorted({tag.strip() for tag in tags if tag.strip()})
        info = self._entries.get(str(path), FileTagInfo())
        info.tags = cleaned
        self._store(path, info)

    def toggle_tag(self, path: Path, tag: str) -> bool:
        """Schaltet einen einzelnen Tag für einen Pfad um.

        Args:
            path: Betroffener Dateisystempfad.
            tag: Umzuschaltender Tag-Name.

        Returns:
            True, wenn der Tag danach gesetzt ist; False, wenn er
            entfernt wurde.
        """
        info = self._entries.get(str(path), FileTagInfo())
        tag = tag.strip()
        if tag in info.tags:
            info.tags = [existing for existing in info.tags if existing != tag]
            self._store(path, info)
            return False
        info.tags = sorted({*info.tags, tag})
        self._store(path, info)
        return True

    def forget(self, path: Path) -> None:
        """Entfernt alle Metadaten zu einem Pfad vollständig (z. B. nach Löschen)."""
        if self._entries.pop(str(path), None) is not None:
            self._save()

    def forget_many(self, paths: list[Path]) -> None:
        """Entfernt Metadaten für mehrere Pfade in einem Rutsch."""
        changed = False
        for path in paths:
            if self._entries.pop(str(path), None) is not None:
                changed = True
        if changed:
            self._save()

    def rename(self, old_path: Path, new_path: Path) -> None:
        """Überträgt Metadaten von old_path auf new_path (z. B. nach Umbenennen/Verschieben)."""
        info = self._entries.pop(str(old_path), None)
        if info is None:
            return
        self._entries[str(new_path)] = info
        self._save()

    def rename_many(self, pairs: list[tuple[Path, Path]]) -> None:
        """Überträgt Metadaten für mehrere (alt, neu)-Pfadpaare in einem Rutsch."""
        changed = False
        for old_path, new_path in pairs:
            info = self._entries.pop(str(old_path), None)
            if info is not None:
                self._entries[str(new_path)] = info
                changed = True
        if changed:
            self._save()

    def _store(self, path: Path, info: FileTagInfo) -> None:
        key = str(path)
        if info.is_empty:
            self._entries.pop(key, None)
        else:
            self._entries[key] = info
        self._save()
