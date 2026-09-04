"""Pandora® Commander – Miniaturansichten (Thumbnails) für Bilddateien.

Lädt und cacht kleine Vorschaubilder für Bilddateien im Hintergrund
(über den globalen QThreadPool), damit das Anzeigen einer Datei-
liste mit vielen Bildern die Oberfläche nicht blockiert ("darf
niemals einfrieren" laut Lastenheft). Fertige Miniaturansichten
werden per Signal gemeldet, sodass die Panel-Tabelle nur die
jeweils betroffene Zeile neu zeichnet, statt komplett neu zu laden.

Verwendung (aus file_panel_model.py heraus):

    provider = ThumbnailProvider()
    provider.thumbnail_ready.connect(self._on_thumbnail_ready)
    ...
    icon = provider.icon_for(entry)  # None, solange noch nicht geladen
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, Qt, pyqtSignal
from PyQt6.QtGui import QIcon, QImage, QImageReader, QPixmap

from app.core.filesystem.file_model import EntryType, FileEntry
from app.core.logging_setup import get_logger

logger = get_logger(__name__)

#: Dateiendungen, für die eine Miniaturansicht statt eines
#: generischen Icons erzeugt wird. Beschränkt auf Formate, die Qt
#: ohne Zusatzabhängigkeiten (über QImageReader bzw. das mitgelieferte
#: QtSvg-Plugin) zuverlässig lesen kann.
IMAGE_EXTENSIONS: frozenset[str] = frozenset(
    {"png", "jpg", "jpeg", "bmp", "gif", "webp", "svg", "ico", "tif", "tiff", "pbm", "ppm", "xpm"}
)

#: Kantenlänge der erzeugten Miniaturansichten in Pixeln.
DEFAULT_THUMBNAIL_SIZE: int = 32

#: Obergrenze für Dateien, die überhaupt für eine Miniaturansicht
#: eingelesen werden – verhindert, dass ein versehentlich als Bild
#: erkanntes Riesenobjekt den Hintergrund-Worker blockiert.
_MAX_SOURCE_BYTES: int = 64 * 1024 * 1024  # 64 MB


class _ThumbnailSignals(QObject):
    """Eigenständiges QObject für die Signale eines Hintergrund-Tasks.

    QRunnable selbst kann keine Qt-Signale aussenden (kein QObject),
    daher der Umweg über ein separates Signal-Objekt pro Provider.
    """

    finished = pyqtSignal(str, QImage)


class _ThumbnailLoadTask(QRunnable):
    """Lädt und skaliert ein einzelnes Bild im Hintergrund-Thread-Pool."""

    def __init__(self, path: Path, size: int, signals: _ThumbnailSignals) -> None:
        super().__init__()
        self._path = path
        self._size = size
        self._signals = signals
        self.setAutoDelete(True)

    def run(self) -> None:  # noqa: D102 - QRunnable-Override
        image = QImage()
        try:
            if self._path.stat().st_size <= _MAX_SOURCE_BYTES:
                reader = QImageReader(str(self._path))
                reader.setAutoTransform(True)
                # Direkt in Zielgröße skaliert einlesen, statt erst
                # voll zu laden und danach zu verkleinern – deutlich
                # schneller/speicherschonender bei großen Fotos.
                original_size = reader.size()
                if original_size.isValid() and original_size.width() > 0:
                    reader.setScaledSize(
                        original_size.scaled(
                            self._size,
                            self._size,
                            Qt.AspectRatioMode.KeepAspectRatio,
                        )
                    )
                loaded = reader.read()
                if not loaded.isNull():
                    image = loaded
        except OSError as error:
            logger.debug("Miniaturansicht konnte nicht gelesen werden: %s (%s)", self._path, error)

        self._signals.finished.emit(str(self._path), image)


class ThumbnailProvider(QObject):
    """Verwaltet asynchrones Laden und Zwischenspeichern von Bild-Vorschauen.

    Signals:
        thumbnail_ready: Wird gesendet, sobald eine Miniaturansicht
            fertig geladen (oder das Laden endgültig fehlgeschlagen)
            ist, mit dem betroffenen Pfad als Argument.
    """

    thumbnail_ready = pyqtSignal(Path)

    def __init__(self, size: int = DEFAULT_THUMBNAIL_SIZE, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._size = size
        # Wert: (mtime_bei_erstellung, QIcon | None). None bedeutet
        # "kein Bild bzw. Laden fehlgeschlagen" – so wird ein
        # kaputtes Bild nicht bei jedem data()-Aufruf erneut versucht.
        self._cache: dict[str, tuple[float, QIcon | None]] = {}
        self._pending: set[str] = set()
        self._signals = _ThumbnailSignals()
        self._signals.finished.connect(self._on_task_finished)

    @staticmethod
    def is_image(entry: FileEntry) -> bool:
        """Ob für diesen Eintrag grundsätzlich eine Miniaturansicht in Frage kommt."""
        return entry.entry_type == EntryType.FILE and entry.extension.lower() in IMAGE_EXTENSIONS

    def icon_for(self, entry: FileEntry) -> QIcon | None:
        """Liefert die Miniaturansicht eines Eintrags, falls bereits geladen.

        Ist noch keine (aktuelle) Miniaturansicht im Cache, wird ein
        Hintergrund-Ladevorgang angestoßen (sofern nicht bereits
        einer läuft) und None zurückgegeben – der Aufrufer zeigt in
        der Zwischenzeit einfach kein/ein generisches Icon; sobald
        thumbnail_ready gesendet wird, kann erneut abgefragt werden.

        Args:
            entry: Der darzustellende Dateisystemeintrag.

        Returns:
            Ein QIcon mit der Miniaturansicht, oder None (kein
            Bildtyp, noch nicht geladen, oder Laden fehlgeschlagen).
        """
        if not self.is_image(entry):
            return None

        key = str(entry.path)
        mtime = entry.modified.timestamp()
        cached = self._cache.get(key)
        if cached is not None and cached[0] == mtime:
            return cached[1]

        if key not in self._pending:
            self._pending.add(key)
            task = _ThumbnailLoadTask(entry.path, self._size, self._signals)
            QThreadPool.globalInstance().start(task)

        return None

    def invalidate(self, path: Path) -> None:
        """Verwirft eine ggf. zwischengespeicherte Miniaturansicht.

        Sinnvoll, wenn sich eine Datei geändert hat, ohne dass sich
        ihr Änderungsdatum-Vergleich zuverlässig genug auflöst (z. B.
        knapp aufeinanderfolgende Speichervorgänge innerhalb einer
        Sekunde bei manchen Dateisystemen).

        Args:
            path: Zu verwerfender Pfad.
        """
        self._cache.pop(str(path), None)

    def _on_task_finished(self, path_str: str, image: QImage) -> None:
        self._pending.discard(path_str)
        path = Path(path_str)
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = 0.0

        icon = QIcon(QPixmap.fromImage(image)) if not image.isNull() else None
        self._cache[path_str] = (mtime, icon)
        self.thumbnail_ready.emit(path)
