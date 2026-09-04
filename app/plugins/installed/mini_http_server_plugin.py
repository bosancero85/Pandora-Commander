"""Pandora® Commander – Plugin: Mini-HTTP-Server.

Fügt einen Toolbar-Button sowie einen Menüeintrag "Mini-Server"
hinzu, der einen einfachen HTTP-Server (Python-Standardbibliothek,
``http.server.ThreadingHTTPServer`` + ``SimpleHTTPRequestHandler``)
im aktuellen Verzeichnis des aktiven Panels startet bzw. stoppt.

Anders als das Quick-Share-Plugin (Upload zu einem öffentlichen
Internetdienst) bleibt hier alles im lokalen Netzwerk: andere Geräte
im selben WLAN/LAN können den Ordnerinhalt direkt über den Browser
durchsuchen und herunterladen, ohne dass Daten das lokale Netz
verlassen oder ein Account/Internetzugang nötig wäre – ideal für den
schnellen Dateitransfer zwischen z. B. Raspberry Pi und Smartphone im
selben Netz.

Ablauf:
    1. Klick auf "🌐 Mini-Server starten" bindet den Server an
       ``0.0.0.0`` auf einem automatisch gewählten freien Port und
       zeigt einen Dialog mit allen erreichbaren Adressen im lokalen
       Netzwerk (eine pro Netzwerkschnittstelle, da z. B. LAN und
       WLAN unterschiedliche IPs haben können) sowie einer expliziten
       Sicherheitswarnung, dass der Ordnerinhalt für jeden im
       selben Netzwerk lesbar ist.
    2. Der Server läuft in einem eigenen Hintergrund-Thread
       (``ThreadingHTTPServer`` bedient mehrere Downloads parallel,
       ohne die Oberfläche zu blockieren).
    3. Derselbe Menüeintrag/Toolbar-Button dient danach zum Stoppen
       ("🌐 Mini-Server stoppen (läuft: <Ordnername>)").

Es kann jeweils nur ein Server gleichzeitig laufen; ein erneuter
Start-Versuch bei bereits laufendem Server wechselt stattdessen das
servierte Verzeichnis (nach Bestätigung), statt einen zweiten Port zu
öffnen.
"""

from __future__ import annotations

import functools
import http.server
import socket
import threading
from pathlib import Path
from typing import Any

from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QDialog, QLabel, QMessageBox, QPushButton, QVBoxLayout, QWidget

from app.core.logging_setup import get_logger
from app.plugins.plugin_manager import PandoraPlugin

logger = get_logger(__name__)

_BIND_ADDRESS = "0.0.0.0"


def _quiet_log_message(self: http.server.BaseHTTPRequestHandler, format_string: str, *args: Any) -> None:
    """Ersetzt die Standard-Ausgabe auf stderr durch eine Zeile im App-Logger."""
    logger.debug("Mini-HTTP-Server: %s - %s", self.address_string(), format_string % args)


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    log_message = _quiet_log_message


def _local_ip_addresses() -> list[str]:
    """Ermittelt die wahrscheinlich im LAN erreichbaren lokalen IP-Adressen.

    Nutzt einen UDP-"Verbindungsversuch" ohne tatsächlich Daten zu
    senden, um die für ausgehenden Verkehr verwendete Schnittstellen-IP
    zu bestimmen – ein gängiger, plattformunabhängiger Trick, da
    ``socket.gethostname()`` auf vielen Linux-Systemen nur
    "127.0.1.1" liefert.
    """
    addresses: set[str] = set()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe_socket:
            probe_socket.connect(("8.8.8.8", 80))
            addresses.add(probe_socket.getsockname()[0])
    except OSError:
        pass

    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            candidate = info[4][0]
            if not candidate.startswith("127."):
                addresses.add(candidate)
    except OSError:
        pass

    return sorted(addresses)


class ServerStatusDialog(QDialog):
    """Zeigt die erreichbaren Adressen des laufenden Mini-HTTP-Servers an."""

    def __init__(self, directory: Path, port: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Pandora® Commander – Mini-HTTP-Server läuft")

        addresses = _local_ip_addresses()
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"Serviert Ordner:\n{directory}\n"))

        if addresses:
            layout.addWidget(QLabel("Erreichbar im lokalen Netzwerk unter:"))
            for address in addresses:
                url = f"http://{address}:{port}/"
                link_label = QLabel(f'<a href="{url}">{url}</a>')
                link_label.setOpenExternalLinks(True)
                layout.addWidget(link_label)
        else:
            layout.addWidget(QLabel(f"Lokal erreichbar unter: http://localhost:{port}/"))
            layout.addWidget(QLabel("Keine weitere Netzwerkschnittstelle erkannt."))

        warning_label = QLabel(
            "⚠ Jeder im selben Netzwerk kann diesen Ordner durchsuchen und "
            "herunterladen, solange der Server läuft."
        )
        warning_label.setStyleSheet("color: #e67e22;")
        warning_label.setWordWrap(True)
        layout.addWidget(warning_label)

        close_button = QPushButton("OK")
        close_button.clicked.connect(self.accept)
        layout.addWidget(close_button)


class MiniHttpServerPlugin(PandoraPlugin):
    """Plugin zum Start/Stop eines lokalen HTTP-Servers für das aktive Panel-Verzeichnis."""

    name = "Mini-HTTP-Server"
    version = "1.0"
    author = "AKI_SystemDown®"
    description = (
        "Startet/stoppt per Toolbar-Button oder Menüeintrag einen einfachen HTTP-Server "
        "im aktuellen Verzeichnis des aktiven Panels – für schnellen Dateizugriff aus dem "
        "lokalen Netzwerk heraus, ganz ohne Internetdienst wie bei Quick-Share."
    )

    def __init__(self) -> None:
        self._context: dict[str, Any] = {}
        self._server: http.server.ThreadingHTTPServer | None = None
        self._server_thread: threading.Thread | None = None
        self._served_directory: Path | None = None
        self._action: QAction | None = None

    def on_load(self, context: dict[str, Any]) -> None:
        self._context = context
        logger.info("%s geladen.", self.name)

    def on_unload(self) -> None:
        self._stop_server()

    def register_toolbar_actions(self, context: dict[str, Any]) -> list[QAction]:
        main_window = context.get("main_window")
        self._action = QAction("🌐 Mini-Server starten", main_window)
        self._action.triggered.connect(self._on_action_triggered)
        return [self._action]

    def register_menu_actions(self, context: dict[str, Any]) -> list[QAction]:
        # Derselbe QAction erscheint zusätzlich im Plugins-Menü, damit
        # der Status (Text, aktiviert/deaktiviert) an einer einzigen
        # Stelle gepflegt werden muss.
        if self._action is None:
            return self.register_toolbar_actions(context)
        return [self._action]

    def _on_action_triggered(self) -> None:
        if self._server is not None:
            self._confirm_and_stop()
        else:
            self._start_for_active_panel()

    def _start_for_active_panel(self) -> None:
        main_window = self._context.get("main_window")
        left_panel = self._context.get("left_panel")
        current_directory = getattr(left_panel, "current_directory", None)
        directory = current_directory if isinstance(current_directory, Path) else Path.home()

        try:
            handler_class = functools.partial(_QuietHandler, directory=str(directory))
            server = http.server.ThreadingHTTPServer((_BIND_ADDRESS, 0), handler_class)
        except OSError as error:
            QMessageBox.critical(main_window, "Server konnte nicht gestartet werden", str(error))
            return

        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        self._server = server
        self._server_thread = thread
        self._served_directory = directory

        if self._action is not None:
            self._action.setText(f"🌐 Mini-Server stoppen ({directory.name})")

        dialog = ServerStatusDialog(directory, server.server_port, parent=main_window)
        dialog.exec()

    def _confirm_and_stop(self) -> None:
        main_window = self._context.get("main_window")
        confirmed = QMessageBox.question(
            main_window,
            "Mini-HTTP-Server stoppen",
            f"Server für '{self._served_directory}' jetzt stoppen?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if confirmed == QMessageBox.StandardButton.Yes:
            self._stop_server()

    def _stop_server(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._server_thread is not None:
            self._server_thread.join(timeout=2)
            self._server_thread = None
        self._served_directory = None

        if self._action is not None:
            self._action.setText("🌐 Mini-Server starten")
