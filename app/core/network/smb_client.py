"""Pandora® Commander – SMB-Client (Windows-Freigaben / UNC-Pfade).

Kapselt das optionale Paket ``smbprotocol`` für den Zugriff auf
SMB/CIFS-Freigaben, z. B. \\\\server\\freigabe. Ist das Paket nicht
installiert, wird beim Verbindungsaufbau eine klare SmbError-Meldung
ausgelöst.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from app.core.logging_setup import get_logger

logger = get_logger(__name__)


class SmbError(Exception):
    """Ausgelöst bei Fehlern während einer SMB-Operation."""


@dataclass
class SmbEntry:
    """Ein Eintrag eines SMB-Verzeichnislistings.

    Attributes:
        name: Name des Eintrags.
        is_dir: Ob es sich um ein Verzeichnis handelt.
        size_bytes: Größe in Bytes.
    """

    name: str
    is_dir: bool
    size_bytes: int


def _get_smbclient():
    """Importiert smbclient (aus smbprotocol) bei Bedarf."""
    try:
        import smbclient  # type: ignore[import-untyped]
    except ImportError as error:
        raise SmbError(
            "SMB-Unterstützung erfordert das optionale Paket 'smbprotocol' "
            "(pip install smbprotocol)."
        ) from error
    return smbclient


class SmbClient:
    """Verbindung zu einer SMB/CIFS-Netzwerkfreigabe.

    Args:
        server: Servername oder IP-Adresse (ohne führende Backslashes).
        share: Name der Freigabe.
        username: Benutzername, ggf. "DOMÄNE\\Benutzer".
        password: Passwort.
    """

    def __init__(self, server: str, share: str, username: str = "", password: str = "") -> None:
        self._server = server
        self._share = share
        self._username = username
        self._password = password
        self._session_id = str(uuid.uuid4())
        self._connected = False

    def _unc(self, relative_path: str = "") -> str:
        """Baut einen vollständigen UNC-Pfad aus Server, Freigabe und Rest."""
        base = f"\\\\{self._server}\\{self._share}"
        cleaned = relative_path.strip("\\/")
        return f"{base}\\{cleaned}" if cleaned else base

    def connect(self) -> None:
        """Registriert die Session bei smbclient.

        Raises:
            SmbError: Wenn smbprotocol fehlt oder die Anmeldung fehlschlägt.
        """
        smbclient = _get_smbclient()
        try:
            smbclient.register_session(
                self._server,
                username=self._username,
                password=self._password,
                connection_cache=None,
            )
            self._connected = True
            logger.info("SMB-Verbindung hergestellt zu \\\\%s\\%s", self._server, self._share)
        except Exception as error:
            raise SmbError(f"SMB-Verbindung zu {self._server} fehlgeschlagen: {error}") from error

    def disconnect(self) -> None:
        """Beendet die registrierte SMB-Session."""
        if not self._connected:
            return
        smbclient = _get_smbclient()
        try:
            smbclient.delete_session(self._server)
        except Exception as error:  # smbprotocol wirft diverse Exception-Typen
            logger.warning("Fehler beim Trennen der SMB-Session: %s", error)
        self._connected = False

    def list_dir(self, relative_path: str = "") -> list[SmbEntry]:
        """Listet den Inhalt eines Freigabe-Verzeichnisses auf."""
        smbclient = _get_smbclient()
        entries: list[SmbEntry] = []
        try:
            for info in smbclient.scandir(self._unc(relative_path)):
                entries.append(
                    SmbEntry(
                        name=info.name,
                        is_dir=info.is_dir(),
                        size_bytes=0 if info.is_dir() else info.stat().st_size,
                    )
                )
        except Exception as error:
            raise SmbError(f"Verzeichnis konnte nicht gelesen werden: {error}") from error
        return entries

    def download_file(self, relative_path: str, local_path) -> None:
        """Lädt eine Datei von der Freigabe herunter."""
        smbclient = _get_smbclient()
        local_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with smbclient.open_file(self._unc(relative_path), mode="rb") as remote_file:
                local_path.write_bytes(remote_file.read())
        except Exception as error:
            raise SmbError(f"Download fehlgeschlagen für {relative_path}: {error}") from error

    def upload_file(self, local_path, relative_path: str) -> None:
        """Lädt eine lokale Datei auf die Freigabe hoch."""
        smbclient = _get_smbclient()
        try:
            with smbclient.open_file(self._unc(relative_path), mode="wb") as remote_file:
                remote_file.write(local_path.read_bytes())
        except Exception as error:
            raise SmbError(f"Upload fehlgeschlagen für {local_path}: {error}") from error

    def __enter__(self) -> SmbClient:
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.disconnect()
