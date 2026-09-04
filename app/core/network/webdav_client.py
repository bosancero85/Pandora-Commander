"""Pandora® Commander – WebDAV-Client.

Implementiert die für einen Dateimanager benötigten WebDAV-Operationen
(PROPFIND zum Auflisten, GET/PUT zum Herunter-/Hochladen, DELETE,
MKCOL) direkt über HTTP mit dem optionalen Paket ``requests``, um eine
zusätzliche schwergewichtige WebDAV-Bibliothek zu vermeiden.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

from app.core.logging_setup import get_logger

logger = get_logger(__name__)

_DAV_NAMESPACE = "{DAV:}"


class WebDavError(Exception):
    """Ausgelöst bei Fehlern während einer WebDAV-Operation."""


@dataclass
class WebDavEntry:
    """Ein Eintrag eines WebDAV-Verzeichnislistings.

    Attributes:
        name: Name des Eintrags (letztes Pfadsegment).
        href: Vollständiger Pfad relativ zum Server.
        is_dir: Ob es sich um eine Sammlung (Ordner) handelt.
        size_bytes: Größe in Bytes, 0 bei Ordnern.
    """

    name: str
    href: str
    is_dir: bool
    size_bytes: int = 0


def _get_requests():
    """Importiert requests bei Bedarf, mit klarer Fehlermeldung falls fehlend."""
    try:
        import requests  # type: ignore[import-untyped]
    except ImportError as error:
        raise WebDavError(
            "WebDAV-Unterstützung erfordert das optionale Paket 'requests' "
            "(pip install requests)."
        ) from error
    return requests


_PROPFIND_BODY = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<D:propfind xmlns:D="DAV:">'
    "<D:prop><D:displayname/><D:resourcetype/><D:getcontentlength/></D:prop>"
    "</D:propfind>"
)


class WebDavClient:
    """Verbindung zu einem WebDAV-Server.

    Args:
        base_url: Basis-URL des WebDAV-Servers, z. B. "https://cloud.example.com/remote.php/dav/files/user".
        username: Benutzername für HTTP Basic Auth.
        password: Passwort für HTTP Basic Auth.
        timeout: Timeout in Sekunden je Anfrage.
    """

    def __init__(self, base_url: str, username: str = "", password: str = "", timeout: float = 15.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._username = username
        self._password = password
        self._timeout = timeout
        self._session = None

    def connect(self) -> None:
        """Erstellt eine wiederverwendbare HTTP-Session mit Zugangsdaten."""
        requests = _get_requests()
        session = requests.Session()
        session.auth = (self._username, self._password)
        self._session = session
        logger.info("WebDAV-Session vorbereitet für %s", self._base_url)

    def disconnect(self) -> None:
        """Schließt die HTTP-Session."""
        if self._session is not None:
            self._session.close()
            self._session = None

    def _require_session(self):
        if self._session is None:
            raise WebDavError("Nicht verbunden – connect() muss zuerst aufgerufen werden.")
        return self._session

    def list_dir(self, relative_path: str = "") -> list[WebDavEntry]:
        """Listet den Inhalt eines WebDAV-Verzeichnisses per PROPFIND auf."""
        session = self._require_session()
        url = f"{self._base_url}/{relative_path.strip('/')}".rstrip("/")
        try:
            response = session.request(
                "PROPFIND",
                url,
                data=_PROPFIND_BODY,
                headers={"Depth": "1", "Content-Type": "application/xml"},
                timeout=self._timeout,
            )
            response.raise_for_status()
        except Exception as error:
            raise WebDavError(f"Verzeichnis konnte nicht gelesen werden: {error}") from error

        return self._parse_propfind(response.text, url)

    def _parse_propfind(self, xml_text: str, base_url: str) -> list[WebDavEntry]:
        entries: list[WebDavEntry] = []
        try:
            root = ElementTree.fromstring(xml_text)
        except ElementTree.ParseError as error:
            raise WebDavError(f"WebDAV-Antwort konnte nicht geparst werden: {error}") from error

        for response_element in root.findall(f"{_DAV_NAMESPACE}response"):
            href_element = response_element.find(f"{_DAV_NAMESPACE}href")
            if href_element is None or href_element.text is None:
                continue
            href = href_element.text

            # Das erste Element (der angefragte Ordner selbst) überspringen.
            if href.rstrip("/") == base_url.rstrip("/").split(self._base_url, 1)[-1]:
                pass

            resource_type = response_element.find(f".//{_DAV_NAMESPACE}resourcetype")
            is_dir = resource_type is not None and resource_type.find(f"{_DAV_NAMESPACE}collection") is not None

            length_element = response_element.find(f".//{_DAV_NAMESPACE}getcontentlength")
            size = int(length_element.text) if length_element is not None and length_element.text else 0

            name = Path(href.rstrip("/")).name
            if not name:
                continue
            entries.append(WebDavEntry(name=name, href=href, is_dir=is_dir, size_bytes=size))

        return entries

    def download_file(self, relative_path: str, local_path: Path) -> None:
        """Lädt eine Datei per HTTP GET herunter."""
        session = self._require_session()
        url = f"{self._base_url}/{relative_path.strip('/')}"
        local_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            response = session.get(url, timeout=self._timeout, stream=True)
            response.raise_for_status()
            with local_path.open("wb") as file_handle:
                for chunk in response.iter_content(chunk_size=1024 * 256):
                    file_handle.write(chunk)
        except Exception as error:
            raise WebDavError(f"Download fehlgeschlagen für {relative_path}: {error}") from error

    def upload_file(self, local_path: Path, relative_path: str) -> None:
        """Lädt eine lokale Datei per HTTP PUT hoch."""
        session = self._require_session()
        url = f"{self._base_url}/{relative_path.strip('/')}"
        try:
            with local_path.open("rb") as file_handle:
                response = session.put(url, data=file_handle, timeout=self._timeout)
                response.raise_for_status()
        except Exception as error:
            raise WebDavError(f"Upload fehlgeschlagen für {local_path}: {error}") from error

    def delete(self, relative_path: str) -> None:
        """Löscht eine Datei oder ein Verzeichnis per HTTP DELETE."""
        session = self._require_session()
        url = f"{self._base_url}/{relative_path.strip('/')}"
        try:
            response = session.delete(url, timeout=self._timeout)
            response.raise_for_status()
        except Exception as error:
            raise WebDavError(f"Löschen fehlgeschlagen für {relative_path}: {error}") from error

    def make_collection(self, relative_path: str) -> None:
        """Legt ein Verzeichnis (Collection) per HTTP MKCOL an."""
        session = self._require_session()
        url = f"{self._base_url}/{relative_path.strip('/')}"
        try:
            response = session.request("MKCOL", url, timeout=self._timeout)
            response.raise_for_status()
        except Exception as error:
            raise WebDavError(f"Verzeichnis konnte nicht erstellt werden: {error}") from error

    def __enter__(self) -> WebDavClient:
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.disconnect()
