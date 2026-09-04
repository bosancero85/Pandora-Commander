"""Pandora® Commander – FTP/FTPS-Client.

Kapselt ftplib.FTP / ftplib.FTP_TLS in einer schlanken, für den
Dateimanager nutzbaren Schnittstelle: Verbinden, Auflisten,
Herunterladen, Hochladen, Löschen, Verzeichnisse anlegen.
"""

from __future__ import annotations

import ftplib
from dataclasses import dataclass
from pathlib import Path

from app.core.logging_setup import get_logger

logger = get_logger(__name__)


class FtpError(Exception):
    """Ausgelöst bei Fehlern während einer FTP/FTPS-Operation."""


@dataclass
class FtpEntry:
    """Ein Eintrag (Datei oder Ordner) eines FTP-Verzeichnislistings.

    Attributes:
        name: Name des Eintrags.
        is_dir: Ob es sich um ein Verzeichnis handelt.
        size_bytes: Größe in Bytes, -1 wenn unbekannt (z. B. bei Ordnern).
    """

    name: str
    is_dir: bool
    size_bytes: int = -1


class FtpClient:
    """Verbindung zu einem FTP- oder FTPS-Server.

    Args:
        host: Hostname oder IP-Adresse des Servers.
        port: TCP-Port, Standard 21.
        username: Benutzername, Standard "anonymous".
        password: Passwort.
        use_tls: Ob FTPS (explizites TLS) verwendet werden soll.
        passive: Ob der passive Modus verwendet werden soll (empfohlen
            hinter NAT/Firewalls).
        timeout: Verbindungs-Timeout in Sekunden.
    """

    def __init__(
        self,
        host: str,
        port: int = 21,
        username: str = "anonymous",
        password: str = "",
        use_tls: bool = False,
        passive: bool = True,
        timeout: float = 15.0,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._use_tls = use_tls
        self._passive = passive
        self._timeout = timeout
        self._connection: ftplib.FTP | None = None

    def connect(self) -> None:
        """Baut die Verbindung auf und authentifiziert sich.

        Raises:
            FtpError: Wenn Verbindung oder Login fehlschlagen.
        """
        try:
            connection = ftplib.FTP_TLS(timeout=self._timeout) if self._use_tls else ftplib.FTP(timeout=self._timeout)
            connection.connect(self._host, self._port)
            connection.login(self._username, self._password)
            if self._use_tls:
                connection.prot_p()  # type: ignore[attr-defined]
            connection.set_pasv(self._passive)
            self._connection = connection
            logger.info("FTP-Verbindung hergestellt zu %s:%d", self._host, self._port)
        except (OSError, ftplib.all_errors) as error:
            raise FtpError(f"Verbindung zu {self._host} fehlgeschlagen: {error}") from error

    def disconnect(self) -> None:
        """Trennt die Verbindung, falls verbunden."""
        if self._connection is not None:
            try:
                self._connection.quit()
            except ftplib.all_errors:
                self._connection.close()
            self._connection = None

    def _require_connection(self) -> ftplib.FTP:
        if self._connection is None:
            raise FtpError("Nicht verbunden – connect() muss zuerst aufgerufen werden.")
        return self._connection

    def list_dir(self, remote_path: str = ".") -> list[FtpEntry]:
        """Listet den Inhalt eines Remote-Verzeichnisses auf."""
        connection = self._require_connection()
        entries: list[FtpEntry] = []
        try:
            listing: list[tuple[str, dict]] = list(connection.mlsd(remote_path))
            for name, facts in listing:
                if name in (".", ".."):
                    continue
                is_dir = facts.get("type") == "dir"
                size = int(facts.get("size", -1)) if facts.get("size") else -1
                entries.append(FtpEntry(name=name, is_dir=is_dir, size_bytes=size))
        except ftplib.error_perm:
            # Server unterstützt MLSD nicht – Fallback auf NLST (ohne Metadaten).
            names = connection.nlst(remote_path)
            entries = [FtpEntry(name=Path(n).name, is_dir=False) for n in names]
        return entries

    def download_file(self, remote_path: str, local_path: Path) -> None:
        """Lädt eine Datei vom Server herunter."""
        connection = self._require_connection()
        local_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with local_path.open("wb") as file_handle:
                connection.retrbinary(f"RETR {remote_path}", file_handle.write)
        except ftplib.all_errors as error:
            raise FtpError(f"Download fehlgeschlagen für {remote_path}: {error}") from error

    def upload_file(self, local_path: Path, remote_path: str) -> None:
        """Lädt eine lokale Datei auf den Server hoch."""
        connection = self._require_connection()
        try:
            with local_path.open("rb") as file_handle:
                connection.storbinary(f"STOR {remote_path}", file_handle)
        except ftplib.all_errors as error:
            raise FtpError(f"Upload fehlgeschlagen für {local_path}: {error}") from error

    def delete_file(self, remote_path: str) -> None:
        """Löscht eine Datei auf dem Server."""
        connection = self._require_connection()
        try:
            connection.delete(remote_path)
        except ftplib.all_errors as error:
            raise FtpError(f"Löschen fehlgeschlagen für {remote_path}: {error}") from error

    def make_dir(self, remote_path: str) -> None:
        """Legt ein Verzeichnis auf dem Server an."""
        connection = self._require_connection()
        try:
            connection.mkd(remote_path)
        except ftplib.all_errors as error:
            raise FtpError(f"Verzeichnis konnte nicht erstellt werden: {error}") from error

    def remove_dir(self, remote_path: str) -> None:
        """Entfernt ein (leeres) Verzeichnis auf dem Server."""
        connection = self._require_connection()
        try:
            connection.rmd(remote_path)
        except ftplib.all_errors as error:
            raise FtpError(f"Verzeichnis konnte nicht entfernt werden: {error}") from error

    def __enter__(self) -> FtpClient:
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.disconnect()
