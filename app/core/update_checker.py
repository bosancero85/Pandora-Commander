"""Pandora® Commander – Automatische Update-Prüfung.

Prüft im Hintergrund (QThread, blockiert also nie die Oberfläche), ob
eine neuere Version von Pandora® Commander verfügbar ist. Die Prüfung
erfolgt gegen ein einfaches JSON-Manifest, das unter einer
konfigurierbaren URL (Einstellungen -> Allgemein -> Update-URL)
erreichbar sein muss und folgendes Format hat:

    {
        "version": "1.2.0",
        "download_url": "https://example.com/downloads/PandoraCommander-1.2.0.zip",
        "notes": "- Neue Funktion A\\n- Fehlerbehebung B"
    }

"notes" und "download_url" sind optional. Ist keine Update-URL
konfiguriert oder das Manifest nicht erreichbar, wird die Prüfung
fehlertolerant abgebrochen (siehe Sicherheits-Vorgabe: robust,
fehlertolerant) – es kommt nie zu einem Absturz der Anwendung, weder
beim automatischen Start-Check noch bei der manuellen Prüfung über
Hilfe -> Nach Updates suchen.

Verwendung:
    from app.core.update_checker import UpdateCheckWorker

    worker = UpdateCheckWorker(current_version="0.1.0", manifest_url=url)
    worker.update_available.connect(on_update_available)
    worker.no_update_found.connect(on_no_update_found)
    worker.check_failed.connect(on_check_failed)
    worker.start()
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from PyQt6.QtCore import QThread, pyqtSignal

from app.core.logging_setup import get_logger

logger = get_logger(__name__)

#: Zeitlimit für die Anfrage an das Update-Manifest, in Sekunden.
_REQUEST_TIMEOUT_SECONDS = 8


@dataclass(frozen=True)
class UpdateInfo:
    """Informationen über eine verfügbare neuere Version.

    Attributes:
        version: Versionsnummer der verfügbaren Version (z. B. "1.2.0").
        download_url: Optionale URL, unter der die neue Version
            heruntergeladen werden kann.
        notes: Optionaler, mehrzeiliger Änderungstext (Changelog).
    """

    version: str
    download_url: str = ""
    notes: str = ""


def _parse_version(version_text: str) -> tuple[int, ...]:
    """Wandelt einen Versionsstring in ein vergleichbares Tupel um.

    Nicht-numerische Anteile (z. B. "-beta", "+build3") werden für den
    Vergleich ignoriert, sodass sowohl "1.2.0" als auch "1.2" oder
    "1.2.0-beta" robust verglichen werden können.

    Args:
        version_text: Rohe Versionsangabe, z. B. aus dem Manifest oder
            aus APP_VERSION.

    Returns:
        Tupel aus Ganzzahlen, z. B. (1, 2, 0).
    """
    numbers = re.findall(r"\d+", version_text)
    return tuple(int(part) for part in numbers) if numbers else (0,)


def is_newer_version(candidate: str, current: str) -> bool:
    """Prüft, ob candidate eine echte Höherversion gegenüber current ist.

    Args:
        candidate: Zu prüfende Versionsnummer (aus dem Manifest).
        current: Aktuell installierte Versionsnummer (APP_VERSION).

    Returns:
        True, wenn candidate > current gemäß numerischem Vergleich.
    """
    return _parse_version(candidate) > _parse_version(current)


class UpdateCheckWorker(QThread):
    """Prüft im Hintergrund-Thread auf eine neuere verfügbare Version.

    Signals:
        update_available: Emittiert mit einer UpdateInfo, wenn eine
            neuere Version gefunden wurde.
        no_update_found: Emittiert, wenn kein Update verfügbar ist
            (aktuelle Version ist die neueste).
        check_failed: Emittiert mit einer Fehlermeldung, wenn die
            Prüfung nicht durchgeführt werden konnte (z. B. keine
            Internetverbindung, ungültiges Manifest, keine URL
            konfiguriert).

    Args:
        current_version: Aktuell installierte Version (APP_VERSION).
        manifest_url: URL des JSON-Update-Manifests. Ist die URL leer,
            wird sofort check_failed mit einer entsprechenden Meldung
            emittiert.
    """

    update_available = pyqtSignal(object)
    no_update_found = pyqtSignal()
    check_failed = pyqtSignal(str)

    def __init__(self, current_version: str, manifest_url: str, parent=None) -> None:
        super().__init__(parent)
        self._current_version = current_version
        self._manifest_url = manifest_url.strip()

    def run(self) -> None:  # noqa: D102 - Qt-Override, Doku siehe Klasse
        if not self._manifest_url:
            self.check_failed.emit("Keine Update-URL in den Einstellungen konfiguriert.")
            return

        try:
            import requests  # type: ignore[import-untyped]

            response = requests.get(self._manifest_url, timeout=_REQUEST_TIMEOUT_SECONDS)
            response.raise_for_status()
            manifest = response.json()
        except Exception as error:  # noqa: BLE001 - jede Netz-/Parsefehlerart abfangen
            logger.warning("Update-Prüfung fehlgeschlagen: %s", error)
            self.check_failed.emit(f"Update-Prüfung fehlgeschlagen: {error}")
            return

        remote_version = str(manifest.get("version", "")).strip()
        if not remote_version:
            self.check_failed.emit("Update-Manifest enthält keine Versionsangabe.")
            return

        if is_newer_version(remote_version, self._current_version):
            info = UpdateInfo(
                version=remote_version,
                download_url=str(manifest.get("download_url", "")).strip(),
                notes=str(manifest.get("notes", "")).strip(),
            )
            logger.info(
                "Update verfügbar: %s -> %s", self._current_version, remote_version
            )
            self.update_available.emit(info)
        else:
            logger.debug(
                "Kein Update verfügbar (installiert: %s, Server: %s)",
                self._current_version,
                remote_version,
            )
            self.no_update_found.emit()
