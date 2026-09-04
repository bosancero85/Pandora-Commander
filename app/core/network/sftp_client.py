"""Pandora® Commander – SFTP-Client.

Kapselt paramiko für SFTP-Verbindungen. paramiko ist eine optionale
Abhängigkeit (siehe pyproject.toml, Extra "network"); ist es nicht
installiert, wird beim Verbindungsaufbau eine klare SftpError-Meldung
ausgelöst statt eines ImportErrors beim Anwendungsstart.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.core.logging_setup import get_logger

logger = get_logger(__name__)


class SftpError(Exception):
    """Ausgelöst bei Fehlern während einer SFTP-Operation."""


@dataclass
class SftpEntry:
    """Ein Eintrag eines SFTP-Verzeichnislistings.

    Attributes:
        name: Name des Eintrags.
        is_dir: Ob es sich um ein Verzeichnis handelt.
        size_bytes: Größe in Bytes.
    """

    name: str
    is_dir: bool
    size_bytes: int


def _get_paramiko():
    """Importiert paramiko bei Bedarf, mit klarer Fehlermeldung falls fehlend."""
    try:
        import paramiko  # type: ignore[import-untyped]
    except ImportError as error:
        raise SftpError(
            "SFTP-Unterstützung erfordert das optionale Paket 'paramiko' "
            "(pip install paramiko)."
        ) from error
    return paramiko


class SftpClient:
    """Verbindung zu einem SFTP-Server (SSH File Transfer Protocol).

    Args:
        host: Hostname oder IP-Adresse.
        port: TCP-Port, Standard 22.
        username: Benutzername für die Authentifizierung.
        password: Passwort (alternativ zu key_path).
        key_path: Pfad zu einem privaten SSH-Schlüssel (alternativ zu password).
        timeout: Verbindungs-Timeout in Sekunden.
    """

    def __init__(
        self,
        host: str,
        port: int = 22,
        username: str = "",
        password: str = "",
        key_path: Path | None = None,
        timeout: float = 15.0,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._key_path = key_path
        self._timeout = timeout
        self._transport = None
        self._sftp = None

    def connect(self) -> None:
        """Baut die SSH-Transportverbindung auf und öffnet einen SFTP-Kanal.

        Raises:
            SftpError: Wenn paramiko fehlt oder die Verbindung fehlschlägt.
        """
        paramiko = _get_paramiko()
        try:
            transport = paramiko.Transport((self._host, self._port))
            transport.banner_timeout = self._timeout

            if self._key_path is not None:
                private_key = paramiko.RSAKey.from_private_key_file(str(self._key_path))
                transport.connect(username=self._username, pkey=private_key)
            else:
                transport.connect(username=self._username, password=self._password)

            self._transport = transport
            self._sftp = paramiko.SFTPClient.from_transport(transport)
            logger.info("SFTP-Verbindung hergestellt zu %s:%d", self._host, self._port)
        except Exception as error:  # paramiko wirft diverse Exception-Typen
            raise SftpError(f"SFTP-Verbindung zu {self._host} fehlgeschlagen: {error}") from error

    def disconnect(self) -> None:
        """Schließt SFTP-Kanal und SSH-Transport."""
        if self._sftp is not None:
            self._sftp.close()
            self._sftp = None
        if self._transport is not None:
            self._transport.close()
            self._transport = None

    def _require_sftp(self):
        if self._sftp is None:
            raise SftpError("Nicht verbunden – connect() muss zuerst aufgerufen werden.")
        return self._sftp

    def list_dir(self, remote_path: str = ".") -> list[SftpEntry]:
        """Listet den Inhalt eines Remote-Verzeichnisses auf."""
        sftp = self._require_sftp()
        entries: list[SftpEntry] = []
        try:
            for attr in sftp.listdir_attr(remote_path):
                is_dir = bool(attr.st_mode and (attr.st_mode & 0o040000))
                entries.append(SftpEntry(name=attr.filename, is_dir=is_dir, size_bytes=attr.st_size or 0))
        except OSError as error:
            raise SftpError(f"Verzeichnis konnte nicht gelesen werden: {error}") from error
        return entries

    def download_file(self, remote_path: str, local_path: Path) -> None:
        """Lädt eine Datei vom Server herunter."""
        sftp = self._require_sftp()
        local_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            sftp.get(remote_path, str(local_path))
        except OSError as error:
            raise SftpError(f"Download fehlgeschlagen für {remote_path}: {error}") from error

    def upload_file(self, local_path: Path, remote_path: str) -> None:
        """Lädt eine lokale Datei auf den Server hoch."""
        sftp = self._require_sftp()
        try:
            sftp.put(str(local_path), remote_path)
        except OSError as error:
            raise SftpError(f"Upload fehlgeschlagen für {local_path}: {error}") from error

    def delete_file(self, remote_path: str) -> None:
        """Löscht eine Datei auf dem Server."""
        sftp = self._require_sftp()
        try:
            sftp.remove(remote_path)
        except OSError as error:
            raise SftpError(f"Löschen fehlgeschlagen für {remote_path}: {error}") from error

    def make_dir(self, remote_path: str) -> None:
        """Legt ein Verzeichnis auf dem Server an."""
        sftp = self._require_sftp()
        try:
            sftp.mkdir(remote_path)
        except OSError as error:
            raise SftpError(f"Verzeichnis konnte nicht erstellt werden: {error}") from error

    def __enter__(self) -> SftpClient:
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.disconnect()
