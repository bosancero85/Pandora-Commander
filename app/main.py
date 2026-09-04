"""Pandora® Commander – Anwendungseinstiegspunkt.

Dieses Modul initialisiert die Qt-Anwendung, konfiguriert High-DPI-
Unterstützung, richtet das globale Logging ein und startet das
Hauptfenster der Anwendung.

Ausführen mit:
    python -m app.main
oder über den in pyproject.toml definierten Entry-Point:
    pandora-commander
"""

from __future__ import annotations

import sys
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from app.themes.dark_theme import apply_dark_theme
from app.utils.icon_provider import get_icon

# Anwendungsmetadaten – zentral gepflegt, damit alle Module (Titel-
# leisten, Über-Dialog, Logging-Header, Einstellungsdatei-Pfad usw.)
# dieselben Werte referenzieren können.
APP_NAME: str = "Pandora® Commander"
APP_VERSION: str = "0.1.0"
APP_ORGANIZATION: str = "AKI_SystemDown® / Pandora®"

# Basisverzeichnis des Projekts (…/PandoraCommander), wird von anderen
# Modulen (Icons, Themes, Übersetzungen, Konfiguration) referenziert.
BASE_DIR: Path = Path(__file__).resolve().parent.parent


def _configure_high_dpi() -> None:
    """Aktiviert High-DPI-Skalierung und rundet Pixmaps sauber.

    Muss aufgerufen werden, bevor die erste QApplication-Instanz
    erzeugt wird, da Qt einige dieser Attribute nur vor der
    Initialisierung übernimmt.
    """
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    # Hinweis: AA_UseHighDpiPixmaps existiert in Qt6/PyQt6 nicht mehr,
    # da High-DPI-Pixmap-Skalierung dort standardmäßig aktiviert ist.
    # Das Setzen dieses Attributs entfällt daher unter Qt6.


def create_application(argv: list[str]) -> QApplication:
    """Erstellt und konfiguriert die zentrale QApplication-Instanz.

    Args:
        argv: Kommandozeilenargumente (üblicherweise sys.argv).

    Returns:
        Die konfigurierte, aber noch nicht ausgeführte QApplication.
    """
    _configure_high_dpi()

    application = QApplication(argv)
    application.setApplicationName(APP_NAME)
    application.setApplicationVersion(APP_VERSION)
    application.setOrganizationName(APP_ORGANIZATION)
    application.setQuitOnLastWindowClosed(True)
    application.setWindowIcon(get_icon("app_icon"))

    apply_dark_theme(application)

    return application


def main() -> int:
    """Startet Pandora® Commander.

    Returns:
        Der Exit-Code des Qt-Event-Loops, geeignet für sys.exit().
    """
    application = create_application(sys.argv)

    # Hauptfenster (Menüs, Symbolleiste, zwei Panels, Statusleiste) ist
    # in app/ui/main_window.py implementiert.
    from app.ui.main_window import MainWindow

    window = MainWindow()
    window.show()

    return application.exec()


if __name__ == "__main__":
    sys.exit(main())
