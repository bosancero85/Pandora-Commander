"""Pandora® Commander – Plugin: Thumbnail-Cache-Boost.

Der eingebaute ``ThumbnailProvider`` (siehe app/utils/thumbnail_provider.py)
hält fertig geladene Miniaturansichten bislang ausschließlich im
Arbeitsspeicher – bei jedem Neustart der Anwendung oder erneutem
Öffnen eines großen Bildordners müssen alle Vorschaubilder erneut von
der Festplatte gelesen und skaliert werden. Dieses Plugin ergänzt
einen persistenten, plattenbasierten Zweit-Cache, ohne den Core-Code
selbst zu verändern:

    1. Beim Laden patcht das Plugin ``_ThumbnailLoadTask.run`` im
       ``thumbnail_provider``-Modul (Monkeypatch, mit Aufbewahrung der
       Originalmethode als Fallback): vor dem eigentlichen Decodieren
       wird zunächst geprüft, ob im Cache-Verzeichnis
       (``~/.cache/pandora_commander/thumbnails/``) bereits eine
       passende, bereits herunterskalierte PNG-Miniaturansicht liegt.
       Der Cache-Schlüssel enthält Pfad, Änderungsdatum, Dateigröße
       und Zielauflösung – ändert sich die Quelldatei, wird also
       automatisch neu generiert statt eine veraltete Miniaturansicht
       auszuliefern.
    2. Zusätzlich registriert das Plugin sich für
       ``on_panel_directory_changed``: beim Betreten eines Ordners
       werden alle enthaltenen Bilddateien im Hintergrund
       vorab in den Plattencache geschrieben ("Prefetch"), sodass ein
       erneuter Besuch desselben Ordners die Miniaturansichten praktisch
       sofort anzeigt.

Das Plugin selbst zeigt keine eigene Oberfläche – es wirkt rein im
Hintergrund. Über das Plugins-Menü lässt sich der Plattencache bei
Bedarf leeren (z. B. nach massenhaftem Umbenennen/Verschieben, falls
verwaiste Cache-Dateien unnötig Platz belegen sollen).
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QRunnable, Qt, QThreadPool
from PyQt6.QtGui import QAction, QImage, QImageReader
from PyQt6.QtWidgets import QMessageBox

from app.core.logging_setup import get_logger
from app.plugins.plugin_manager import PandoraPlugin
from app.ui.widgets.file_panel import FilePanel
from app.utils import thumbnail_provider as tp_module

logger = get_logger(__name__)

_CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "pandora_commander" / "thumbnails"


def _ensure_cache_dir() -> None:
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        logger.warning("Thumbnail-Cache-Verzeichnis konnte nicht angelegt werden: %s", error)


def _cache_file_for(path: Path, mtime: float, size_bytes: int, target_size: int) -> Path:
    raw_key = f"{path.resolve()}|{mtime}|{size_bytes}|{target_size}"
    digest = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    return _CACHE_DIR / f"{digest}.png"


# Referenz auf die ursprüngliche, ungepatchte Methode – dient als Fallback
# und wird beim Entladen des Plugins wiederhergestellt.
_original_run = tp_module._ThumbnailLoadTask.run


def _cache_boosted_run(self: "tp_module._ThumbnailLoadTask") -> None:  # noqa: ANN001
    """Ersetzt _ThumbnailLoadTask.run: prüft zuerst den Plattencache."""
    image = QImage()
    try:
        stat_result = self._path.stat()
        if stat_result.st_size <= tp_module._MAX_SOURCE_BYTES:
            cache_path = _cache_file_for(self._path, stat_result.st_mtime, stat_result.st_size, self._size)

            cached_image = QImage(str(cache_path)) if cache_path.exists() else QImage()
            if not cached_image.isNull():
                image = cached_image
            else:
                reader = QImageReader(str(self._path))
                reader.setAutoTransform(True)
                original_size = reader.size()
                if original_size.isValid() and original_size.width() > 0:
                    reader.setScaledSize(
                        original_size.scaled(self._size, self._size, Qt.AspectRatioMode.KeepAspectRatio)
                    )
                loaded = reader.read()
                if not loaded.isNull():
                    image = loaded
                    try:
                        _ensure_cache_dir()
                        image.save(str(cache_path), "PNG")
                    except OSError as error:
                        logger.debug("Miniaturansicht konnte nicht in den Plattencache geschrieben werden: %s", error)
    except OSError as error:
        logger.debug("Miniaturansicht (Cache-Boost) konnte nicht gelesen werden: %s (%s)", self._path, error)

    self._signals.finished.emit(str(self._path), image)


class _PrefetchTask(QRunnable):
    """Erzeugt im Hintergrund eine Plattencache-Miniaturansicht, ohne ein Signal zu senden."""

    def __init__(self, path: Path, target_size: int) -> None:
        super().__init__()
        self._path = path
        self._target_size = target_size
        self.setAutoDelete(True)

    def run(self) -> None:  # noqa: D102 - QRunnable-Override
        try:
            stat_result = self._path.stat()
            if stat_result.st_size > tp_module._MAX_SOURCE_BYTES:
                return
            cache_path = _cache_file_for(self._path, stat_result.st_mtime, stat_result.st_size, self._target_size)
            if cache_path.exists():
                return  # Bereits im Cache – nichts zu tun.

            reader = QImageReader(str(self._path))
            reader.setAutoTransform(True)
            original_size = reader.size()
            if original_size.isValid() and original_size.width() > 0:
                reader.setScaledSize(
                    original_size.scaled(self._target_size, self._target_size, Qt.AspectRatioMode.KeepAspectRatio)
                )
            loaded = reader.read()
            if not loaded.isNull():
                _ensure_cache_dir()
                loaded.save(str(cache_path), "PNG")
        except OSError as error:
            logger.debug("Prefetch fehlgeschlagen für %s: %s", self._path, error)


class ThumbnailCacheBoostPlugin(PandoraPlugin):
    """Plugin, das einen persistenten Plattencache für Miniaturansichten ergänzt."""

    name = "Thumbnail-Cache-Boost"
    version = "1.0"
    author = "AKI_SystemDown®"
    description = (
        "Ergänzt einen persistenten Plattencache für Miniaturansichten (überlebt App-"
        "Neustarts) sowie automatisches Vorab-Cachen beim Betreten eines Ordners. "
        "Wirkt rein im Hintergrund, per Kontextmenü-losem Menüpunkt zum Leeren des Caches."
    )

    def __init__(self) -> None:
        self._context: dict[str, Any] = {}
        self._patched = False

    def on_load(self, context: dict[str, Any]) -> None:
        self._context = context
        _ensure_cache_dir()
        tp_module._ThumbnailLoadTask.run = _cache_boosted_run
        self._patched = True
        logger.info("%s geladen – Plattencache aktiv unter %s", self.name, _CACHE_DIR)

    def on_unload(self) -> None:
        if self._patched:
            tp_module._ThumbnailLoadTask.run = _original_run
            self._patched = False

    def register_menu_actions(self, context: dict[str, Any]) -> list[QAction]:
        main_window = context.get("main_window")
        action = QAction("Thumbnail-Cache leeren", main_window)
        action.triggered.connect(self._on_clear_cache_clicked)
        return [action]

    def on_panel_directory_changed(self, context: dict[str, Any], panel: FilePanel, path: Path) -> None:
        try:
            entries = list(path.iterdir())
        except OSError:
            return

        target_size = tp_module.DEFAULT_THUMBNAIL_SIZE
        for entry_path in entries:
            if entry_path.is_file() and entry_path.suffix.lower().lstrip(".") in tp_module.IMAGE_EXTENSIONS:
                QThreadPool.globalInstance().start(_PrefetchTask(entry_path, target_size))

    def _on_clear_cache_clicked(self) -> None:
        main_window = self._context.get("main_window")
        confirmed = QMessageBox.question(
            main_window,
            "Thumbnail-Cache leeren",
            f"Alle zwischengespeicherten Miniaturansichten unter\n{_CACHE_DIR}\nlöschen?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirmed != QMessageBox.StandardButton.Yes:
            return

        removed_count = 0
        errors: list[str] = []
        if _CACHE_DIR.is_dir():
            for cache_file in _CACHE_DIR.glob("*.png"):
                try:
                    cache_file.unlink()
                    removed_count += 1
                except OSError as error:
                    errors.append(f"{cache_file.name}: {error}")

        if errors:
            QMessageBox.warning(
                main_window,
                "Teilweise fehlgeschlagen",
                f"{removed_count} Datei(en) gelöscht.\n\n" + "\n".join(errors[:10]),
            )
        else:
            QMessageBox.information(main_window, "Fertig", f"{removed_count} Cache-Datei(en) gelöscht.")
