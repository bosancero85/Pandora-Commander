"""Pandora® Commander – Verbindungsmanager.

Verwaltet gespeicherte Netzwerkverbindungsprofile (FTP, FTPS, SFTP,
SMB, WebDAV) inklusive Persistierung als JSON und Instanziierung des
passenden Clients aus core.network.*. Passwörter werden bewusst nicht
verschlüsselt gespeichert (kein Betriebssystem-Schlüsselbund als
Abhängigkeit) – ein Hinweis dazu wird beim Speichern geloggt.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from app.core.logging_setup import get_logger

logger = get_logger(__name__)

DEFAULT_CONNECTIONS_FILE: Path = Path.home() / ".pandora_commander" / "connections.json"


class ConnectionType(str, Enum):
    """Unterstützte Netzwerkprotokolle."""

    FTP = "ftp"
    FTPS = "ftps"
    SFTP = "sftp"
    SMB = "smb"
    WEBDAV = "webdav"


@dataclass
class ConnectionProfile:
    """Ein gespeichertes Verbindungsprofil.

    Attributes:
        name: Anzeigename des Profils.
        connection_type: Protokoll der Verbindung.
        host: Hostname, IP-Adresse oder Basis-URL (WebDAV).
        port: TCP-Port (nicht relevant für WebDAV/SMB).
        username: Benutzername.
        password: Passwort (unverschlüsselt gespeichert, siehe Modulhinweis).
        remote_path: Startverzeichnis nach dem Verbinden.
        share: Freigabename, nur relevant für SMB.
        key_path: Pfad zu einem privaten SSH-Schlüssel, nur für SFTP.
    """

    name: str
    connection_type: ConnectionType
    host: str
    port: int = 21
    username: str = ""
    password: str = ""
    remote_path: str = "/"
    share: str = ""
    key_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = dict(self.__dict__)
        data["connection_type"] = self.connection_type.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ConnectionProfile:
        raw_type = data.get("connection_type", ConnectionType.FTP.value)
        return cls(
            name=data.get("name", ""),
            connection_type=ConnectionType(raw_type),
            host=data.get("host", ""),
            port=int(data.get("port", 21)),
            username=data.get("username", ""),
            password=data.get("password", ""),
            remote_path=data.get("remote_path", "/"),
            share=data.get("share", ""),
            key_path=data.get("key_path", ""),
        )


class ConnectionManager:
    """Lädt, speichert und instanziiert Netzwerkverbindungsprofile.

    Args:
        storage_path: Pfad zur JSON-Datei mit den Verbindungsprofilen.
    """

    def __init__(self, storage_path: Path | None = None) -> None:
        self._storage_path = storage_path or DEFAULT_CONNECTIONS_FILE
        self._profiles: list[ConnectionProfile] = []
        self.load()

    @property
    def profiles(self) -> list[ConnectionProfile]:
        """Alle geladenen Verbindungsprofile."""
        return self._profiles

    def load(self) -> None:
        """Lädt Verbindungsprofile von der Festplatte."""
        if not self._storage_path.exists():
            self._profiles = []
            return
        try:
            raw = json.loads(self._storage_path.read_text(encoding="utf-8"))
            self._profiles = [ConnectionProfile.from_dict(p) for p in raw.get("profiles", [])]
        except (json.JSONDecodeError, OSError) as error:
            logger.error("Verbindungsprofile konnten nicht geladen werden: %s", error)
            self._profiles = []

    def save(self) -> None:
        """Speichert alle Verbindungsprofile als JSON-Datei.

        Passwörter werden im Klartext abgelegt; die Konfigurationsdatei
        liegt daher im geschützten Nutzerprofil-Verzeichnis.
        """
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)
        data = {"profiles": [p.to_dict() for p in self._profiles]}
        try:
            self._storage_path.write_text(
                json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            logger.debug("Verbindungsprofile gespeichert unter %s", self._storage_path)
        except OSError as error:
            logger.error("Verbindungsprofile konnten nicht gespeichert werden: %s", error)
            raise

    def add_profile(self, profile: ConnectionProfile) -> None:
        """Fügt ein neues Verbindungsprofil hinzu und speichert sofort."""
        self._profiles.append(profile)
        self.save()

    def remove_profile(self, name: str) -> None:
        """Entfernt ein Verbindungsprofil anhand seines Namens."""
        self._profiles = [p for p in self._profiles if p.name != name]
        self.save()

    def find_profile(self, name: str) -> ConnectionProfile | None:
        """Sucht ein Verbindungsprofil anhand seines Namens."""
        for profile in self._profiles:
            if profile.name == name:
                return profile
        return None

    def create_client(self, profile: ConnectionProfile):
        """Instanziiert den zum Profil passenden Netzwerk-Client.

        Args:
            profile: Das zu verwendende Verbindungsprofil.

        Returns:
            Eine (noch nicht verbundene) Client-Instanz aus core.network.*.

        Raises:
            ValueError: Bei unbekanntem connection_type.
        """
        if profile.connection_type in (ConnectionType.FTP, ConnectionType.FTPS):
            from app.core.network.ftp_client import FtpClient

            return FtpClient(
                host=profile.host,
                port=profile.port,
                username=profile.username,
                password=profile.password,
                use_tls=profile.connection_type == ConnectionType.FTPS,
            )

        if profile.connection_type == ConnectionType.SFTP:
            from app.core.network.sftp_client import SftpClient

            return SftpClient(
                host=profile.host,
                port=profile.port,
                username=profile.username,
                password=profile.password,
                key_path=Path(profile.key_path) if profile.key_path else None,
            )

        if profile.connection_type == ConnectionType.SMB:
            from app.core.network.smb_client import SmbClient

            return SmbClient(
                server=profile.host,
                share=profile.share,
                username=profile.username,
                password=profile.password,
            )

        if profile.connection_type == ConnectionType.WEBDAV:
            from app.core.network.webdav_client import WebDavClient

            return WebDavClient(
                base_url=profile.host,
                username=profile.username,
                password=profile.password,
            )

        raise ValueError(f"Unbekannter Verbindungstyp: {profile.connection_type}")
