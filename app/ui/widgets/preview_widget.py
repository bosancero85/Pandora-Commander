"""Pandora® Commander – Dateivorschau.

Zeigt je nach Dateityp eine passende Vorschau im rechten/geteilten
Bereich des Dateimanagers an: Bilder direkt, PDFs über QtPdf (falls
verfügbar) oder Fallback-Hinweis, Text/Markdown/JSON/XML/HTML als
formatierter oder eingefärbter Text, Video/Audio über QMediaPlayer.

Die Vorschau ist bewusst rein lesend (kein Editor – dafür siehe
ui/dialogs/editor_window.py) und arbeitet defensiv: nicht darstellbare
oder zu große Dateien führen zu einer Hinweismeldung statt zum Absturz.
"""

from __future__ import annotations

import json
from pathlib import Path
from xml.dom import minidom

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QLabel, QStackedWidget, QTextBrowser, QVBoxLayout, QWidget

from app.core.logging_setup import get_logger
from app.themes.dark_theme import PALETTE

logger = get_logger(__name__)

#: Maximale Dateigröße, die noch als Text eingelesen wird (5 MiB).
MAX_TEXT_PREVIEW_SIZE = 5 * 1024 * 1024

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg", ".ico"}
TEXT_EXTENSIONS = {".txt", ".log", ".ini", ".cfg", ".py", ".toml", ".yaml", ".yml"}
MARKDOWN_EXTENSIONS = {".md", ".markdown"}
JSON_EXTENSIONS = {".json"}
XML_EXTENSIONS = {".xml"}
HTML_EXTENSIONS = {".html", ".htm"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".flac", ".ogg", ".m4a"}
VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".mov", ".webm"}
PDF_EXTENSIONS = {".pdf"}


class PreviewWidget(QStackedWidget):
    """Container-Widget, das die passende Unteransicht für einen Pfad wählt."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._empty_label = self._make_message_label("Keine Vorschau verfügbar.")
        self._image_label = QLabel(self)
        self._image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image_label.setScaledContents(False)
        self._text_view = QTextBrowser(self)
        self._text_view.setOpenExternalLinks(True)
        self._text_view.setStyleSheet(
            f"QTextBrowser {{ background-color: {PALETTE.surface}; "
            f"color: {PALETTE.text_primary}; border: none; font-family: Consolas, monospace; }}"
        )
        self._media_label = self._make_message_label("Vorschau für Audio/Video nicht eingebettet – bitte extern öffnen.")

        for widget in (self._empty_label, self._image_label, self._text_view, self._media_label):
            self.addWidget(widget)

        self.setCurrentWidget(self._empty_label)

    def _make_message_label(self, text: str) -> QLabel:
        label = QLabel(text, self)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setWordWrap(True)
        label.setStyleSheet(f"color: {PALETTE.text_secondary}; padding: 24px;")
        return label

    def show_path(self, path: Path) -> None:
        """Wählt und rendert die passende Vorschau für den gegebenen Pfad.

        Args:
            path: Pfad zur darzustellenden Datei.
        """
        if not path.is_file():
            self._show_message("Kein Vorschau-fähiger Dateipfad.")
            return

        suffix = path.suffix.lower()
        try:
            if suffix in IMAGE_EXTENSIONS and suffix != ".svg":
                self._show_image(path)
            elif suffix == ".svg":
                self._show_svg(path)
            elif suffix in JSON_EXTENSIONS:
                self._show_json(path)
            elif suffix in XML_EXTENSIONS:
                self._show_xml(path)
            elif suffix in HTML_EXTENSIONS:
                self._show_html(path)
            elif suffix in MARKDOWN_EXTENSIONS:
                self._show_markdown(path)
            elif suffix in TEXT_EXTENSIONS:
                self._show_text(path)
            elif suffix in PDF_EXTENSIONS:
                self._show_pdf(path)
            elif suffix in AUDIO_EXTENSIONS or suffix in VIDEO_EXTENSIONS:
                self._show_message(f"{path.name}\n\nAudio-/Video-Vorschau: bitte mit Standardprogramm öffnen.")
            else:
                self._show_message(f"Kein Vorschau-Handler für '{suffix or 'unbekannt'}'.")
        except Exception as error:  # Vorschau darf die App niemals crashen
            logger.warning("Vorschau fehlgeschlagen für %s: %s", path, error)
            self._show_message(f"Vorschau konnte nicht geladen werden:\n{error}")

    def _show_message(self, text: str) -> None:
        self._empty_label.setText(text)
        self.setCurrentWidget(self._empty_label)

    def _show_image(self, path: Path) -> None:
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            self._show_message("Bild konnte nicht gelesen werden.")
            return
        scaled = pixmap.scaled(
            self._image_label.size() if self._image_label.size().width() > 0 else pixmap.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._image_label.setPixmap(scaled)
        self.setCurrentWidget(self._image_label)

    def _show_svg(self, path: Path) -> None:
        # Einfache Darstellung von SVG als Bild über QPixmap (Qt lädt SVG
        # nativ über sein Bild-Plugin, sofern Qt6Svg installiert ist).
        self._show_image(path)

    def _read_text_safely(self, path: Path) -> str | None:
        try:
            if path.stat().st_size > MAX_TEXT_PREVIEW_SIZE:
                self._show_message("Datei zu groß für Textvorschau (> 5 MiB).")
                return None
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError as error:
            self._show_message(f"Datei konnte nicht gelesen werden:\n{error}")
            return None

    def _show_text(self, path: Path) -> None:
        content = self._read_text_safely(path)
        if content is None:
            return
        self._text_view.setPlainText(content)
        self.setCurrentWidget(self._text_view)

    def _show_markdown(self, path: Path) -> None:
        content = self._read_text_safely(path)
        if content is None:
            return
        self._text_view.setMarkdown(content)
        self.setCurrentWidget(self._text_view)

    def _show_json(self, path: Path) -> None:
        content = self._read_text_safely(path)
        if content is None:
            return
        try:
            parsed = json.loads(content)
            formatted = json.dumps(parsed, indent=2, ensure_ascii=False)
        except json.JSONDecodeError:
            formatted = content
        self._text_view.setPlainText(formatted)
        self.setCurrentWidget(self._text_view)

    def _show_xml(self, path: Path) -> None:
        content = self._read_text_safely(path)
        if content is None:
            return
        try:
            formatted = minidom.parseString(content).toprettyxml(indent="  ")
        except Exception:
            formatted = content
        self._text_view.setPlainText(formatted)
        self.setCurrentWidget(self._text_view)

    def _show_html(self, path: Path) -> None:
        content = self._read_text_safely(path)
        if content is None:
            return
        self._text_view.setHtml(content)
        self.setCurrentWidget(self._text_view)

    def _show_pdf(self, path: Path) -> None:
        try:
            from PyQt6.QtPdf import QPdfDocument
            from PyQt6.QtPdfWidgets import QPdfView
        except ImportError:
            self._show_message(
                f"{path.name}\n\nPDF-Vorschau erfordert das Paket 'PyQt6-QtPdf'."
            )
            return

        pdf_view = QPdfView(self)
        document = QPdfDocument(pdf_view)
        document.load(str(path))
        pdf_view.setDocument(document)
        pdf_view.setPageMode(QPdfView.PageMode.MultiPage)

        # Vorherige PDF-Ansicht (falls vorhanden) entfernen, um Speicher
        # nicht unbegrenzt anwachsen zu lassen.
        existing_index = self.indexOf(self._pdf_view) if hasattr(self, "_pdf_view") else -1
        if existing_index != -1:
            self.removeWidget(self._pdf_view)
            self._pdf_view.deleteLater()

        self._pdf_view = pdf_view
        self.addWidget(pdf_view)
        self.setCurrentWidget(pdf_view)
