"""Pandora® Commander – zentrales Logging-System.

Stellt eine einheitliche Logging-Konfiguration für die gesamte
Anwendung bereit: Konsolenausgabe, rotierende Logdatei sowie ein
separates Fehlerprotokoll für WARNING/ERROR/CRITICAL-Einträge.

Verwendung in anderen Modulen:
    from app.core.logging_setup import get_logger

    logger = get_logger(__name__)
    logger.info("Anwendung gestartet")
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

# Log-Verzeichnis unterhalb des Nutzer-Konfigurationsordners, damit
# Logs nicht im (ggf. schreibgeschützten) Installationsverzeichnis
# landen. Wird beim ersten Import automatisch angelegt.
LOG_DIR: Path = Path.home() / ".pandora_commander" / "logs"
LOG_FILE: Path = LOG_DIR / "pandora_commander.log"
ERROR_LOG_FILE: Path = LOG_DIR / "pandora_commander.error.log"

_MAX_BYTES: int = 5 * 1024 * 1024  # 5 MB pro Logdatei
_BACKUP_COUNT: int = 5

_LOG_FORMAT: str = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_DATE_FORMAT: str = "%Y-%m-%d %H:%M:%S"

_configured: bool = False


def configure_logging(debug: bool = False) -> None:
    """Konfiguriert das Root-Logging der Anwendung einmalig.

    Richtet drei Handler ein:
      * Konsole (stdout) – Level abhängig vom Debug-Modus
      * Rotierende Gesamt-Logdatei – alle Level ab INFO
      * Rotierende Fehler-Logdatei – nur WARNING und höher

    Args:
        debug: Wenn True, wird auf der Konsole und in der
            Gesamt-Logdatei DEBUG-Level statt INFO verwendet.
    """
    global _configured
    if _configured:
        # Erneuter Aufruf (z. B. weil der Debug-Modus zur Laufzeit
        # umgeschaltet wurde) soll den Pegel anpassen, aber keine
        # doppelten Handler registrieren.
        set_debug_mode(debug)
        return

    LOG_DIR.mkdir(parents=True, exist_ok=True)

    root_logger = logging.getLogger("pandora_commander")
    root_logger.setLevel(logging.DEBUG)
    root_logger.propagate = False

    formatter = logging.Formatter(fmt=_LOG_FORMAT, datefmt=_DATE_FORMAT)

    console_handler = logging.StreamHandler(stream=sys.stdout)
    console_handler.setLevel(logging.DEBUG if debug else logging.INFO)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    file_handler = RotatingFileHandler(
        filename=LOG_FILE,
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG if debug else logging.INFO)
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    error_handler = RotatingFileHandler(
        filename=ERROR_LOG_FILE,
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    error_handler.setLevel(logging.WARNING)
    error_handler.setFormatter(formatter)
    root_logger.addHandler(error_handler)

    _configured = True
    root_logger.info(
        "Logging initialisiert (debug=%s, log_dir=%s)", debug, LOG_DIR
    )


def set_debug_mode(debug: bool) -> None:
    """Schaltet den Debug-Modus zur Laufzeit um.

    Ändert das Level der Konsolen- und Gesamt-Logdatei-Handler,
    ohne die Handler-Liste neu aufzubauen.

    Args:
        debug: True für DEBUG-Level, False für INFO-Level.
    """
    level = logging.DEBUG if debug else logging.INFO
    root_logger = logging.getLogger("pandora_commander")
    for handler in root_logger.handlers:
        if isinstance(handler, RotatingFileHandler) and handler.baseFilename == str(
            ERROR_LOG_FILE
        ):
            # Das Fehlerprotokoll bleibt unabhängig vom Debug-Modus
            # immer auf WARNING beschränkt.
            continue
        handler.setLevel(level)
    root_logger.info("Debug-Modus %s", "aktiviert" if debug else "deaktiviert")


def get_logger(name: str) -> logging.Logger:
    """Liefert einen Logger unterhalb des Anwendungs-Root-Loggers.

    Konfiguriert das Logging automatisch mit Standardeinstellungen,
    falls dies noch nicht geschehen ist (z. B. bei Nutzung in Tests).

    Args:
        name: Üblicherweise __name__ des aufrufenden Moduls.

    Returns:
        Ein konfigurierter logging.Logger.
    """
    if not _configured:
        configure_logging(debug=False)
    return logging.getLogger(f"pandora_commander.{name}")
