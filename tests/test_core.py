"""Pandora® Commander – Tests für die Kernlogik (app/core/*).

Deckt die Module ab, die keine laufende QApplication benötigen:
Einstellungen, Favoriten, Verbindungsprofile, Archivverwaltung und
Massenumbenennung. UI-Module (Dialoge, Widgets) werden bewusst nicht
hier getestet, da sie eine QApplication-Instanz voraussetzen (siehe
``pytest-qt``, sobald entsprechende Widget-Tests ergänzt werden).

Ausführen mit:
    pytest
    pytest -k archive          # nur einzelne Module
    pytest --cov=app.core      # mit Coverage, falls pytest-cov installiert ist
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from app.core.config import ConfigManager, DEFAULT_LANGUAGE, DEFAULT_THEME, Settings
from app.core.filesystem.bulk_rename import RenameRule, apply_rename, build_new_name, preview_rename
from app.core.filesystem.favorites import FavoritesManager
from app.core.network.connection_manager import (
    ConnectionManager,
    ConnectionProfile,
    ConnectionType,
)
from app.core.archive.archive_handler import (
    ArchiveError,
    create_archive,
    extract_archive,
    is_archive,
    list_archive,
)

# ---------------------------------------------------------------------------
# app.core.config
# ---------------------------------------------------------------------------


class TestSettings:
    """Tests für die Settings-Dataclass und ihre (De-)Serialisierung."""

    def test_defaults(self) -> None:
        settings = Settings()
        assert settings.theme == DEFAULT_THEME
        assert settings.language == DEFAULT_LANGUAGE
        assert settings.confirm_delete is True

    def test_round_trip_via_dict(self) -> None:
        original = Settings(theme="light", font_size=14, confirm_delete=False)
        restored = Settings.from_dict(original.to_dict())
        assert restored == original

    def test_from_dict_ignores_unknown_keys(self) -> None:
        data = Settings().to_dict()
        data["does_not_exist"] = "should be ignored"
        restored = Settings.from_dict(data)
        assert restored == Settings()

    def test_from_dict_fills_missing_keys_with_defaults(self) -> None:
        restored = Settings.from_dict({"theme": "light"})
        assert restored.theme == "light"
        assert restored.language == DEFAULT_LANGUAGE


class TestConfigManager:
    """Tests für Laden/Speichern der Einstellungen als JSON-Datei."""

    def test_load_creates_and_persists_defaults_when_file_missing(self, tmp_path: Path) -> None:
        config_path = tmp_path / "settings.json"
        manager = ConfigManager(config_path=config_path)

        settings = manager.load()

        assert settings == Settings()
        assert config_path.exists()  # load() persistiert die neu erzeugten Defaults sofort

    def test_save_and_reload_round_trip(self, tmp_path: Path) -> None:
        config_path = tmp_path / "settings.json"
        manager = ConfigManager(config_path=config_path)

        settings = manager.load()
        settings.theme = "light"
        settings.font_size = 16
        manager.save(settings)

        assert config_path.exists()

        reloaded_manager = ConfigManager(config_path=config_path)
        reloaded = reloaded_manager.load()
        assert reloaded.theme == "light"
        assert reloaded.font_size == 16

    def test_load_recovers_from_corrupt_file(self, tmp_path: Path) -> None:
        config_path = tmp_path / "settings.json"
        config_path.write_text("{not valid json", encoding="utf-8")
        manager = ConfigManager(config_path=config_path)

        settings = manager.load()

        assert settings == Settings()


# ---------------------------------------------------------------------------
# app.core.filesystem.favorites
# ---------------------------------------------------------------------------


class TestFavoritesManager:
    """Tests für Favoritengruppen, Persistenz und Export/Import.

    Hinweis: Der Konstruktor legt automatisch eine Standardgruppe
    ("Allgemein") an, wenn noch keine Speicherdatei existiert – die
    Tests berücksichtigen das entsprechend, statt von einer leeren
    Gruppenliste auszugehen.
    """

    @staticmethod
    def _find_group(manager: FavoritesManager, name: str):
        return next((group for group in manager.groups if group.name == name), None)

    def test_new_manager_has_default_group(self, tmp_path: Path) -> None:
        manager = FavoritesManager(storage_path=tmp_path / "favorites.json")
        assert len(manager.groups) == 1
        assert manager.groups[0].name == "Allgemein"

    def test_add_group_and_favorite(self, tmp_path: Path) -> None:
        manager = FavoritesManager(storage_path=tmp_path / "favorites.json")

        manager.add_group("Projekte")
        manager.add_favorite("Projekte", "Pandora", tmp_path)

        group = self._find_group(manager, "Projekte")
        assert group is not None
        assert group.entries[0].path == str(tmp_path)
        assert group.entries[0].name == "Pandora"

    def test_add_favorite_creates_group_if_missing(self, tmp_path: Path) -> None:
        manager = FavoritesManager(storage_path=tmp_path / "favorites.json")

        manager.add_favorite("Neu", "Eintrag", tmp_path)

        assert self._find_group(manager, "Neu") is not None

    def test_remove_favorite(self, tmp_path: Path) -> None:
        manager = FavoritesManager(storage_path=tmp_path / "favorites.json")
        manager.add_favorite("Gruppe", "Eintrag", tmp_path)

        manager.remove_favorite("Gruppe", str(tmp_path))

        group = self._find_group(manager, "Gruppe")
        assert group is not None
        assert group.entries == []

    def test_save_and_load_round_trip(self, tmp_path: Path) -> None:
        storage_path = tmp_path / "favorites.json"
        manager = FavoritesManager(storage_path=storage_path)
        manager.add_favorite("Gruppe", "Eintrag", tmp_path)

        reloaded = FavoritesManager(storage_path=storage_path)

        group = self._find_group(reloaded, "Gruppe")
        assert group is not None
        assert group.entries[0].name == "Eintrag"

    def test_export_and_import(self, tmp_path: Path) -> None:
        manager = FavoritesManager(storage_path=tmp_path / "favorites.json")
        manager.add_favorite("Gruppe", "Eintrag", tmp_path)
        export_path = tmp_path / "export.json"
        manager.export_to_file(export_path)

        fresh_manager = FavoritesManager(storage_path=tmp_path / "other_favorites.json")
        fresh_manager.import_from_file(export_path, merge=True)

        group = self._find_group(fresh_manager, "Gruppe")
        assert group is not None
        assert group.entries[0].name == "Eintrag"


# ---------------------------------------------------------------------------
# app.core.network.connection_manager
# ---------------------------------------------------------------------------


class TestConnectionProfile:
    """Tests für die (De-)Serialisierung von Verbindungsprofilen."""

    def test_round_trip_via_dict(self) -> None:
        profile = ConnectionProfile(
            name="Mein Server",
            connection_type=ConnectionType.SFTP,
            host="example.org",
            port=22,
            username="aki",
        )
        restored = ConnectionProfile.from_dict(profile.to_dict())
        assert restored == profile

    def test_from_dict_defaults_missing_fields(self) -> None:
        profile = ConnectionProfile.from_dict({"name": "Minimal", "host": "example.org"})
        assert profile.connection_type == ConnectionType.FTP
        assert profile.port == 21
        assert profile.remote_path == "/"


class TestConnectionManager:
    """Tests für Verwaltung und Persistenz von Verbindungsprofilen."""

    def test_add_and_find_profile(self, tmp_path: Path) -> None:
        manager = ConnectionManager(storage_path=tmp_path / "connections.json")
        profile = ConnectionProfile(name="Server A", connection_type=ConnectionType.FTP, host="a.test")

        manager.add_profile(profile)

        assert manager.find_profile("Server A") == profile

    def test_save_and_reload_round_trip(self, tmp_path: Path) -> None:
        storage_path = tmp_path / "connections.json"
        manager = ConnectionManager(storage_path=storage_path)
        manager.add_profile(
            ConnectionProfile(name="Server B", connection_type=ConnectionType.WEBDAV, host="b.test")
        )
        manager.save()

        reloaded = ConnectionManager(storage_path=storage_path)
        reloaded.load()

        assert reloaded.find_profile("Server B") is not None
        assert reloaded.find_profile("Server B").host == "b.test"

    def test_remove_profile(self, tmp_path: Path) -> None:
        manager = ConnectionManager(storage_path=tmp_path / "connections.json")
        manager.add_profile(
            ConnectionProfile(name="Server C", connection_type=ConnectionType.FTP, host="c.test")
        )

        manager.remove_profile("Server C")

        assert manager.find_profile("Server C") is None

    def test_create_client_returns_matching_client_type(self, tmp_path: Path) -> None:
        from app.core.network.ftp_client import FtpClient

        manager = ConnectionManager(storage_path=tmp_path / "connections.json")
        profile = ConnectionProfile(name="Server D", connection_type=ConnectionType.FTP, host="d.test")

        client = manager.create_client(profile)

        assert isinstance(client, FtpClient)


# ---------------------------------------------------------------------------
# app.core.archive.archive_handler
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_source_tree(tmp_path: Path) -> Path:
    """Erstellt einen kleinen Ordnerbaum, der archiviert werden kann."""
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.txt").write_text("Inhalt A", encoding="utf-8")
    sub_dir = source / "sub"
    sub_dir.mkdir()
    (sub_dir / "b.txt").write_text("Inhalt B", encoding="utf-8")
    return source


class TestArchiveHandler:
    """Tests für Erstellen, Auflisten und Entpacken von Archiven."""

    def test_is_archive_recognizes_supported_extensions(self, tmp_path: Path) -> None:
        assert is_archive(tmp_path / "data.zip")
        assert is_archive(tmp_path / "data.tar")
        assert is_archive(tmp_path / "data.7z")
        assert not is_archive(tmp_path / "data.txt")

    def test_create_and_extract_zip_round_trip(
        self, tmp_path: Path, sample_source_tree: Path
    ) -> None:
        archive_path = tmp_path / "archive.zip"
        create_archive(sources=[sample_source_tree], destination=archive_path, archive_format="zip")

        assert archive_path.exists()
        assert zipfile.is_zipfile(archive_path)

        destination = tmp_path / "extracted"
        extract_archive(archive_path, destination)

        assert (destination / "source" / "a.txt").read_text(encoding="utf-8") == "Inhalt A"
        assert (destination / "source" / "sub" / "b.txt").read_text(encoding="utf-8") == "Inhalt B"

    def test_list_archive_contains_expected_entries(
        self, tmp_path: Path, sample_source_tree: Path
    ) -> None:
        archive_path = tmp_path / "archive.zip"
        create_archive(sources=[sample_source_tree], destination=archive_path, archive_format="zip")

        entries = list_archive(archive_path)
        entry_names = {entry.name.replace("\\", "/") for entry in entries}

        assert any(name.endswith("a.txt") for name in entry_names)
        assert any(name.endswith("b.txt") for name in entry_names)

    def test_create_archive_with_unsupported_format_raises(
        self, tmp_path: Path, sample_source_tree: Path
    ) -> None:
        with pytest.raises(ArchiveError):
            create_archive(
                sources=[sample_source_tree],
                destination=tmp_path / "archive.xyz",
                archive_format="xyz",
            )

    def test_extract_archive_with_non_archive_raises(self, tmp_path: Path) -> None:
        fake_archive = tmp_path / "not_an_archive.zip"
        fake_archive.write_text("kein echtes Archiv", encoding="utf-8")

        with pytest.raises(ArchiveError):
            extract_archive(fake_archive, tmp_path / "out")


# ---------------------------------------------------------------------------
# app.core.filesystem.bulk_rename
# ---------------------------------------------------------------------------


class TestBulkRename:
    """Tests für Namensgenerierung, Vorschau und tatsächliche Ausführung."""

    def test_build_new_name_with_numbering(self, tmp_path: Path) -> None:
        file_path = tmp_path / "urlaub.jpg"
        file_path.write_bytes(b"")
        rule = RenameRule(pattern="Bild_{n}{ext}", start_number=1, padding=3)

        new_name = build_new_name(file_path, index=0, rule=rule)

        assert new_name == "Bild_001.jpg"

    def test_build_new_name_with_search_replace_and_case(self, tmp_path: Path) -> None:
        file_path = tmp_path / "IMG Urlaub.png"
        file_path.write_bytes(b"")
        rule = RenameRule(
            pattern="{name}{ext}",
            search_regex=r"\s+",
            replace_with="_",
            lowercase=True,
        )

        new_name = build_new_name(file_path, index=0, rule=rule)

        assert new_name == "img_urlaub.png"

    def test_preview_rename_detects_conflicts(self, tmp_path: Path) -> None:
        first = tmp_path / "a.txt"
        second = tmp_path / "b.txt"
        first.write_text("A", encoding="utf-8")
        second.write_text("B", encoding="utf-8")
        rule = RenameRule(pattern="gleich.txt")

        items = preview_rename([first, second], rule)

        assert items[0].conflict is False
        assert items[1].conflict is True  # gleicher Zielname wie erster Eintrag

    def test_apply_rename_renames_files_and_skips_conflicts(self, tmp_path: Path) -> None:
        first = tmp_path / "a.txt"
        second = tmp_path / "b.txt"
        first.write_text("A", encoding="utf-8")
        second.write_text("B", encoding="utf-8")
        rule = RenameRule(pattern="gleich.txt")
        items = preview_rename([first, second], rule)

        outcome = apply_rename(items)

        assert len(outcome.renamed) == 1
        assert len(outcome.failed) == 1
        assert (tmp_path / "gleich.txt").exists()
        assert second.exists()  # wurde übersprungen, da Konflikt

    def test_apply_rename_with_no_conflicts(self, tmp_path: Path) -> None:
        first = tmp_path / "one.txt"
        second = tmp_path / "two.txt"
        first.write_text("1", encoding="utf-8")
        second.write_text("2", encoding="utf-8")
        rule = RenameRule(pattern="Datei_{n}{ext}", start_number=1, padding=2)
        items = preview_rename([first, second], rule)

        outcome = apply_rename(items)

        assert len(outcome.renamed) == 2
        assert not outcome.failed
        assert (tmp_path / "Datei_01.txt").exists()
        assert (tmp_path / "Datei_02.txt").exists()
