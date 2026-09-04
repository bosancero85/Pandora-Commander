"""Pandora® Commander – Favoritenverwaltung.

Verwaltet gespeicherte Lieblingsordner in benannten Gruppen
(z. B. "Projekte", "Downloads") sowie deren Persistierung als JSON.
Getrennt von ConfigManager, damit Favoriten unabhängig von den
übrigen Einstellungen exportiert und importiert werden können.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from app.core.logging_setup import get_logger

logger = get_logger(__name__)

DEFAULT_FAVORITES_FILE: Path = Path.home() / ".pandora_commander" / "favorites.json"
DEFAULT_GROUP_NAME: str = "Allgemein"


@dataclass
class FavoriteEntry:
    """Ein einzelner Favoritenordner.

    Attributes:
        name: Anzeigename des Favoriten.
        path: Absoluter Pfad zum Ordner.
    """

    name: str
    path: str

    def to_dict(self) -> dict[str, str]:
        return {"name": self.name, "path": self.path}

    @classmethod
    def from_dict(cls, data: dict[str, str]) -> FavoriteEntry:
        return cls(name=data.get("name", ""), path=data.get("path", ""))


@dataclass
class FavoriteGroup:
    """Eine benannte Gruppe von Favoriten.

    Attributes:
        name: Name der Gruppe.
        entries: Liste der Favoriten in dieser Gruppe.
    """

    name: str
    entries: list[FavoriteEntry] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"name": self.name, "entries": [e.to_dict() for e in self.entries]}

    @classmethod
    def from_dict(cls, data: dict) -> FavoriteGroup:
        entries = [FavoriteEntry.from_dict(e) for e in data.get("entries", [])]
        return cls(name=data.get("name", DEFAULT_GROUP_NAME), entries=entries)


class FavoritesManager:
    """Lädt, speichert und verwaltet Favoritengruppen.

    Args:
        storage_path: Pfad zur JSON-Datei mit den Favoriten.
    """

    def __init__(self, storage_path: Path | None = None) -> None:
        self._storage_path = storage_path or DEFAULT_FAVORITES_FILE
        self._groups: list[FavoriteGroup] = []
        self.load()

    @property
    def groups(self) -> list[FavoriteGroup]:
        """Alle geladenen Favoritengruppen."""
        return self._groups

    def load(self) -> None:
        """Lädt Favoritengruppen von der Festplatte.

        Existiert noch keine Datei, wird eine leere Standardgruppe
        angelegt. Beschädigte Dateien führen nicht zum Absturz.
        """
        if not self._storage_path.exists():
            self._groups = [FavoriteGroup(name=DEFAULT_GROUP_NAME)]
            return

        try:
            raw = json.loads(self._storage_path.read_text(encoding="utf-8"))
            self._groups = [FavoriteGroup.from_dict(g) for g in raw.get("groups", [])]
            if not self._groups:
                self._groups = [FavoriteGroup(name=DEFAULT_GROUP_NAME)]
        except (json.JSONDecodeError, OSError) as error:
            logger.error("Favoriten konnten nicht geladen werden: %s", error)
            self._groups = [FavoriteGroup(name=DEFAULT_GROUP_NAME)]

    def save(self) -> None:
        """Speichert alle Favoritengruppen als JSON-Datei."""
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)
        data = {"groups": [g.to_dict() for g in self._groups]}
        try:
            self._storage_path.write_text(
                json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        except OSError as error:
            logger.error("Favoriten konnten nicht gespeichert werden: %s", error)
            raise

    def add_group(self, name: str) -> FavoriteGroup:
        """Erstellt eine neue, leere Favoritengruppe."""
        group = FavoriteGroup(name=name)
        self._groups.append(group)
        self.save()
        return group

    def remove_group(self, name: str) -> None:
        """Entfernt eine Favoritengruppe anhand ihres Namens."""
        self._groups = [g for g in self._groups if g.name != name]
        self.save()

    def add_favorite(self, group_name: str, entry_name: str, path: Path) -> None:
        """Fügt einen Favoriten zu einer Gruppe hinzu (legt sie ggf. an).

        Args:
            group_name: Name der Zielgruppe.
            entry_name: Anzeigename des neuen Favoriten.
            path: Pfad des Favoritenordners.
        """
        group = self._find_or_create_group(group_name)
        group.entries.append(FavoriteEntry(name=entry_name, path=str(path)))
        self.save()

    def remove_favorite(self, group_name: str, path: str) -> None:
        """Entfernt einen Favoriten anhand seines Pfades aus einer Gruppe."""
        group = self._find_group(group_name)
        if group is None:
            return
        group.entries = [e for e in group.entries if e.path != path]
        self.save()

    def _find_group(self, name: str) -> FavoriteGroup | None:
        for group in self._groups:
            if group.name == name:
                return group
        return None

    def _find_or_create_group(self, name: str) -> FavoriteGroup:
        group = self._find_group(name)
        if group is None:
            group = FavoriteGroup(name=name)
            self._groups.append(group)
        return group

    def export_to_file(self, target_path: Path) -> None:
        """Exportiert alle Favoritengruppen in eine beliebige JSON-Datei."""
        data = {"groups": [g.to_dict() for g in self._groups]}
        target_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info("Favoriten exportiert nach %s", target_path)

    def import_from_file(self, source_path: Path, merge: bool = True) -> None:
        """Importiert Favoritengruppen aus einer JSON-Datei.

        Args:
            source_path: Quelldatei im gleichen Format wie export_to_file.
            merge: Wenn True, werden bestehende Gruppen ergänzt statt
                ersetzt (Gruppen mit gleichem Namen werden zusammengeführt).
        """
        raw = json.loads(source_path.read_text(encoding="utf-8"))
        imported_groups = [FavoriteGroup.from_dict(g) for g in raw.get("groups", [])]

        if not merge:
            self._groups = imported_groups or [FavoriteGroup(name=DEFAULT_GROUP_NAME)]
        else:
            for imported in imported_groups:
                existing = self._find_or_create_group(imported.name)
                existing_paths = {e.path for e in existing.entries}
                for entry in imported.entries:
                    if entry.path not in existing_paths:
                        existing.entries.append(entry)

        self.save()
        logger.info("Favoriten importiert aus %s", source_path)
