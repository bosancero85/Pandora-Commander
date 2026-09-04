"""Pandora® Commander – Übersetzungs-Loader.

Lädt die flachen Schlüssel-Wert-JSON-Kataloge aus ``app/translations/``
(``de.json``, ``en.json``) und stellt sie über eine einfache
``tr(key, fallback)``-Funktion bereit.

Der Sprachwechsel erfolgt ohne Neustart der Anwendung: Nach dem
Speichern der Einstellungen ruft ``MainWindow`` ``set_language()``
und anschließend ``_retranslate_ui()`` auf, wodurch Menü- und
Symbolleisten-Beschriftungen sofort in der neuen Sprache erscheinen.

Fehlt ein Schlüssel im aktuellen Katalog, wird der übergebene
``fallback`` (i. d. R. der deutsche Originaltext) verwendet, statt die
Anwendung abstürzen zu lassen – Übersetzungslücken degradieren also
sanft, anstatt Funktionen zu blockieren.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.core.logging_setup import get_logger

logger = get_logger(__name__)

#: Verzeichnis mit den Übersetzungskatalogen (…/app/translations/).
TRANSLATIONS_DIR: Path = Path(__file__).resolve().parent.parent / "translations"

DEFAULT_LANGUAGE: str = "de"
SUPPORTED_LANGUAGES: tuple[str, ...] = ("de", "en")


class Translator:
    """Hält den aktuell geladenen Übersetzungskatalog."""

    def __init__(self) -> None:
        self._language = DEFAULT_LANGUAGE
        self._catalog: dict[str, str] = {}
        self.set_language(DEFAULT_LANGUAGE)

    @property
    def language(self) -> str:
        """Der aktuell aktive Sprachcode (\"de\" oder \"en\")."""
        return self._language

    def set_language(self, language: str) -> None:
        """Lädt den Übersetzungskatalog der angegebenen Sprache.

        Args:
            language: Sprachcode, z. B. "de" oder "en". Unbekannte
                Codes fallen auf DEFAULT_LANGUAGE zurück.
        """
        if language not in SUPPORTED_LANGUAGES:
            logger.warning(
                "Nicht unterstützte Sprache '%s', verwende Standard '%s'.",
                language,
                DEFAULT_LANGUAGE,
            )
            language = DEFAULT_LANGUAGE

        catalog_path = TRANSLATIONS_DIR / f"{language}.json"
        try:
            self._catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            logger.error(
                "Übersetzungsdatei '%s' konnte nicht geladen werden: %s", catalog_path, error
            )
            self._catalog = {}

        self._language = language
        logger.info("Sprache gewechselt zu '%s' (%d Einträge geladen).", language, len(self._catalog))

    def tr(self, key: str, fallback: str | None = None) -> str:
        """Übersetzt einen Schlüssel in die aktuell geladene Sprache.

        Args:
            key: Übersetzungsschlüssel, z. B. "toolbar.copy".
            fallback: Text, der verwendet wird, wenn der Schlüssel im
                aktuellen Katalog fehlt. Fehlt auch dieser, wird der
                Schlüssel selbst zurückgegeben.

        Returns:
            Der übersetzte Text, der Fallback oder der rohe Schlüssel.
        """
        if key in self._catalog:
            return self._catalog[key]
        if fallback is not None:
            return fallback
        logger.warning("Übersetzungsschlüssel '%s' fehlt für Sprache '%s'.", key, self._language)
        return key


#: Anwendungsweit einzige Translator-Instanz (bewusst als Modul-Singleton
#: gehalten, damit jede UI-Datei ohne zusätzliches Dependency-Injection
#: dieselbe aktive Sprache sieht).
_translator = Translator()


def get_translator() -> Translator:
    """Gibt die anwendungsweite Translator-Instanz zurück."""
    return _translator


def tr(key: str, fallback: str | None = None) -> str:
    """Kurzform für ``get_translator().tr(key, fallback)``."""
    return _translator.tr(key, fallback)
