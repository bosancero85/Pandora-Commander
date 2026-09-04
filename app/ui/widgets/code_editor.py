"""Pandora® Commander – Integrierter Editor: Editor-Widget mit Zeilennummern
und Syntax-Highlighting.

Stellt CodeEditor bereit, ein QPlainTextEdit mit:
    * Fortlaufender Zeilennummernanzeige am linken Rand.
    * Hervorhebung der aktuellen Zeile.
    * Einfachem, regelbasiertem Syntax-Highlighting, das automatisch
      anhand der Dateiendung ausgewählt wird (Python, JSON, XML/HTML,
      Markdown; unbekannte Endungen bleiben unformatiert).
    * UTF-8-Laden/Speichern über load_file()/save_file().

Eine spätere Datei (app/ui/dialogs/editor_window.py) bettet CodeEditor
in ein eigenständiges Editor-Fenster mit Suchen/Ersetzen-Leiste und
Speichern-Aktion ein und verdrahtet F4 im Hauptfenster.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QRect, QSize, Qt
from PyQt6.QtGui import (
    QColor,
    QFont,
    QPainter,
    QSyntaxHighlighter,
    QTextCharFormat,
    QTextFormat,
)
from PyQt6.QtCore import QRegularExpression
from PyQt6.QtWidgets import QPlainTextEdit, QTextEdit, QWidget

from app.core.logging_setup import get_logger
from app.themes.dark_theme import PALETTE

logger = get_logger(__name__)

#: Dateiendungen (ohne Punkt, klein geschrieben), für die eine
#: spezialisierte Syntax-Highlighting-Regelmenge existiert.
_SUPPORTED_LANGUAGES: dict[str, str] = {
    "py": "python",
    "pyw": "python",
    "json": "json",
    "html": "xml",
    "htm": "xml",
    "xml": "xml",
    "md": "markdown",
    "markdown": "markdown",
}

_PYTHON_KEYWORDS: tuple[str, ...] = (
    "and", "as", "assert", "async", "await", "break", "class", "continue",
    "def", "del", "elif", "else", "except", "False", "finally", "for",
    "from", "global", "if", "import", "in", "is", "lambda", "None",
    "nonlocal", "not", "or", "pass", "raise", "return", "True", "try",
    "while", "with", "yield", "self",
)


class _HighlightRule:
    """Eine einzelne Syntax-Highlighting-Regel (Muster + Format)."""

    __slots__ = ("pattern", "char_format")

    def __init__(self, pattern: QRegularExpression, char_format: QTextCharFormat) -> None:
        self.pattern = pattern
        self.char_format = char_format


class SyntaxHighlighter(QSyntaxHighlighter):
    """Einfacher, regelbasierter Syntax-Highlighter für mehrere Sprachen.

    Bewusst regelbasiert (statt eines vollständigen Parsers) gehalten,
    damit das Verhalten leicht nachvollziehbar und um weitere
    Dateitypen erweiterbar bleibt. Mehrzeilige Python-Docstrings
    werden über blockState-Tracking korrekt erkannt.

    Args:
        parent: Das QTextDocument, auf das der Highlighter angewendet wird.
        language: Einer der Schlüssel aus _SUPPORTED_LANGUAGES-Werten
            ("python", "json", "xml", "markdown") oder None für
            unformatierten Text.
    """

    def __init__(self, parent, language: str | None = None) -> None:  # noqa: ANN001
        super().__init__(parent)
        self._rules: list[_HighlightRule] = []
        self._triple_quote_format = self._make_format(PALETTE.success)
        self._triple_quote_pattern = QRegularExpression(r'"""')
        self.set_language(language)

    # ------------------------------------------------------------------
    # Konfiguration
    # ------------------------------------------------------------------

    def set_language(self, language: str | None) -> None:
        """Wechselt die aktive Regelmenge und wendet sie sofort an.

        Args:
            language: Neue Sprache ("python", "json", "xml",
                "markdown") oder None, um alle Regeln zu entfernen.
        """
        self._rules = self._build_rules(language)
        self.rehighlight()

    def _build_rules(self, language: str | None) -> list[_HighlightRule]:
        """Baut die Regelliste für die angegebene Sprache."""
        if language == "python":
            return self._python_rules()
        if language == "json":
            return self._json_rules()
        if language == "xml":
            return self._xml_rules()
        if language == "markdown":
            return self._markdown_rules()
        return []

    @staticmethod
    def _make_format(color: str, bold: bool = False, italic: bool = False) -> QTextCharFormat:
        """Erstellt ein QTextCharFormat mit gegebener Farbe/Auszeichnung."""
        char_format = QTextCharFormat()
        char_format.setForeground(QColor(color))
        if bold:
            char_format.setFontWeight(QFont.Weight.Bold)
        char_format.setFontItalic(italic)
        return char_format

    def _python_rules(self) -> list[_HighlightRule]:
        rules: list[_HighlightRule] = []
        keyword_format = self._make_format(PALETTE.accent_hover, bold=True)
        for keyword in _PYTHON_KEYWORDS:
            pattern = QRegularExpression(rf"\b{keyword}\b")
            rules.append(_HighlightRule(pattern, keyword_format))

        string_format = self._make_format(PALETTE.success)
        rules.append(_HighlightRule(QRegularExpression(r"'[^'\\]*(\\.[^'\\]*)*'"), string_format))
        rules.append(_HighlightRule(QRegularExpression(r'"[^"\\]*(\\.[^"\\]*)*"'), string_format))

        number_format = self._make_format(PALETTE.warning)
        rules.append(_HighlightRule(QRegularExpression(r"\b[0-9]+(\.[0-9]+)?\b"), number_format))

        comment_format = self._make_format(PALETTE.text_secondary, italic=True)
        rules.append(_HighlightRule(QRegularExpression(r"#[^\n]*"), comment_format))

        decorator_format = self._make_format(PALETTE.danger)
        rules.append(_HighlightRule(QRegularExpression(r"@\w+"), decorator_format))

        function_format = self._make_format(PALETTE.accent)
        rules.append(
            _HighlightRule(QRegularExpression(r"\b[A-Za-z_][A-Za-z0-9_]*(?=\()"), function_format)
        )
        return rules

    def _json_rules(self) -> list[_HighlightRule]:
        rules: list[_HighlightRule] = []
        key_format = self._make_format(PALETTE.accent_hover, bold=True)
        rules.append(_HighlightRule(QRegularExpression(r'"[^"\\]*(\\.[^"\\]*)*"(?=\s*:)'), key_format))

        string_format = self._make_format(PALETTE.success)
        rules.append(
            _HighlightRule(QRegularExpression(r'"[^"\\]*(\\.[^"\\]*)*"(?!\s*:)'), string_format)
        )

        number_format = self._make_format(PALETTE.warning)
        rules.append(_HighlightRule(QRegularExpression(r"\b-?[0-9]+(\.[0-9]+)?\b"), number_format))

        literal_format = self._make_format(PALETTE.danger)
        rules.append(_HighlightRule(QRegularExpression(r"\b(true|false|null)\b"), literal_format))
        return rules

    def _xml_rules(self) -> list[_HighlightRule]:
        rules: list[_HighlightRule] = []
        tag_format = self._make_format(PALETTE.accent_hover, bold=True)
        rules.append(_HighlightRule(QRegularExpression(r"</?[A-Za-z0-9_:\-]+"), tag_format))
        rules.append(_HighlightRule(QRegularExpression(r"/?>"), tag_format))

        attribute_format = self._make_format(PALETTE.warning)
        rules.append(_HighlightRule(QRegularExpression(r"\b[A-Za-z_:][A-Za-z0-9_:.\-]*(?=\=)"), attribute_format))

        string_format = self._make_format(PALETTE.success)
        rules.append(_HighlightRule(QRegularExpression(r'"[^"]*"'), string_format))

        comment_format = self._make_format(PALETTE.text_secondary, italic=True)
        rules.append(_HighlightRule(QRegularExpression(r"<!--[^>]*-->"), comment_format))
        return rules

    def _markdown_rules(self) -> list[_HighlightRule]:
        rules: list[_HighlightRule] = []
        heading_format = self._make_format(PALETTE.accent_hover, bold=True)
        rules.append(_HighlightRule(QRegularExpression(r"^#{1,6}\s.*$"), heading_format))

        bold_format = self._make_format(PALETTE.warning, bold=True)
        rules.append(_HighlightRule(QRegularExpression(r"\*\*[^*]+\*\*"), bold_format))

        code_format = self._make_format(PALETTE.success)
        rules.append(_HighlightRule(QRegularExpression(r"`[^`]+`"), code_format))

        link_format = self._make_format(PALETTE.accent)
        rules.append(_HighlightRule(QRegularExpression(r"\[[^\]]*\]\([^)]*\)"), link_format))
        return rules

    # ------------------------------------------------------------------
    # Qt-Hook
    # ------------------------------------------------------------------

    def highlightBlock(self, text: str) -> None:  # noqa: N802
        """Wendet alle aktiven Regeln auf einen einzelnen Textblock an.

        Zusätzlich werden mehrzeilige Python-Triple-Quote-Strings
        (\"\"\"…\"\"\") über den blockState verfolgt, damit sie über
        Zeilengrenzen hinweg korrekt eingefärbt bleiben.

        Args:
            text: Der Text des aktuell zu verarbeitenden Blocks.
        """
        for rule in self._rules:
            match_iterator = rule.pattern.globalMatch(text)
            while match_iterator.hasNext():
                match = match_iterator.next()
                self.setFormat(match.capturedStart(), match.capturedLength(), rule.char_format)

        self._highlight_triple_quoted_strings(text)

    def _highlight_triple_quoted_strings(self, text: str) -> None:
        """Verfolgt mehrzeilige \"\"\"…\"\"\"-Strings über blockState.

        Klassisches Qt-Muster für blockübergreifendes Highlighting:
        Der blockState eines Blocks merkt sich, ob am Blockende ein
        Triple-Quote-String noch offen ist (1) oder nicht (0), damit
        der jeweils nächste Block weiß, ob er "mitten in" einem
        String beginnt.

        Args:
            text: Text des aktuell zu verarbeitenden Blocks.
        """
        if not self._rules:
            return  # Nur relevant, wenn Python-Regeln aktiv sind.

        self.setCurrentBlockState(0)
        search_from = 0

        if self.previousBlockState() == 1:
            end_match = self._triple_quote_pattern.match(text)
            if end_match.hasMatch():
                length = end_match.capturedStart() + 3
                self.setFormat(0, length, self._triple_quote_format)
                search_from = length
            else:
                self.setCurrentBlockState(1)
                self.setFormat(0, len(text), self._triple_quote_format)
                return

        start_match = self._triple_quote_pattern.match(text, search_from)
        while start_match.hasMatch():
            start_index = start_match.capturedStart()
            end_match = self._triple_quote_pattern.match(text, start_index + 3)
            if not end_match.hasMatch():
                self.setCurrentBlockState(1)
                self.setFormat(start_index, len(text) - start_index, self._triple_quote_format)
                return

            length = end_match.capturedStart() + 3 - start_index
            self.setFormat(start_index, length, self._triple_quote_format)
            search_from = start_index + length
            start_match = self._triple_quote_pattern.match(text, search_from)


class _LineNumberArea(QWidget):
    """Schmaler Randbereich links im Editor, der die Zeilennummern zeichnet."""

    def __init__(self, editor: "CodeEditor") -> None:
        super().__init__(editor)
        self._editor = editor

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(self._editor.line_number_area_width(), 0)

    def paintEvent(self, event) -> None:  # noqa: ANN001, N802
        self._editor.paint_line_numbers(event)


class CodeEditor(QPlainTextEdit):
    """Texteditor-Widget mit Zeilennummern und Syntax-Highlighting.

    Args:
        parent: Optionales Eltern-Widget.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._current_path: Path | None = None
        self._line_number_area = _LineNumberArea(self)
        self._highlighter = SyntaxHighlighter(self.document())

        mono_font = QFont("Consolas")
        mono_font.setStyleHint(QFont.StyleHint.Monospace)
        mono_font.setPointSize(10)
        self.setFont(mono_font)
        self.setTabStopDistance(4 * self.fontMetrics().horizontalAdvance(" "))
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)

        self.blockCountChanged.connect(self._update_line_number_area_width)
        self.updateRequest.connect(self._update_line_number_area)
        self.cursorPositionChanged.connect(self._highlight_current_line)

        self._update_line_number_area_width(0)
        self._highlight_current_line()

    # ------------------------------------------------------------------
    # Datei laden/speichern
    # ------------------------------------------------------------------

    @property
    def current_path(self) -> Path | None:
        """Pfad der zuletzt geladenen/gespeicherten Datei, falls vorhanden."""
        return self._current_path

    def load_file(self, path: Path) -> None:
        """Lädt eine Textdatei als UTF-8 in den Editor.

        Wählt automatisch anhand der Dateiendung ein passendes
        Syntax-Highlighting aus (siehe _SUPPORTED_LANGUAGES).

        Args:
            path: Pfad der zu ladenden Datei.

        Raises:
            OSError: Wenn die Datei nicht gelesen werden kann.
            UnicodeDecodeError: Wenn der Inhalt kein gültiges UTF-8 ist.
        """
        content = path.read_text(encoding="utf-8")
        self.setPlainText(content)
        self._current_path = path

        extension = path.suffix.lstrip(".").lower()
        language = _SUPPORTED_LANGUAGES.get(extension)
        self._highlighter.set_language(language)

        logger.debug("Datei im Editor geladen: %s (Sprache: %s)", path, language)

    def save_file(self, path: Path | None = None) -> None:
        """Speichert den aktuellen Editorinhalt als UTF-8-Textdatei.

        Args:
            path: Zielpfad. Wird ausgelassen, um unter dem zuletzt
                geladenen Pfad zu speichern (current_path).

        Raises:
            ValueError: Wenn weder path noch current_path vorliegen.
            OSError: Wenn die Datei nicht geschrieben werden kann.
        """
        target = path or self._current_path
        if target is None:
            raise ValueError("Kein Zielpfad zum Speichern angegeben.")

        target.write_text(self.toPlainText(), encoding="utf-8")
        self._current_path = target
        self.document().setModified(False)
        logger.debug("Datei im Editor gespeichert: %s", target)

    # ------------------------------------------------------------------
    # Zeilennummern
    # ------------------------------------------------------------------

    def line_number_area_width(self) -> int:
        """Berechnet die benötigte Breite des Zeilennummern-Randbereichs."""
        digits = len(str(max(1, self.blockCount())))
        return 12 + self.fontMetrics().horizontalAdvance("9") * digits

    def _update_line_number_area_width(self, _new_block_count: int) -> None:
        self.setViewportMargins(self.line_number_area_width(), 0, 0, 0)

    def _update_line_number_area(self, rect: QRect, dy: int) -> None:
        if dy:
            self._line_number_area.scroll(0, dy)
        else:
            self._line_number_area.update(0, rect.y(), self._line_number_area.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_line_number_area_width(0)

    def resizeEvent(self, event) -> None:  # noqa: ANN001, N802
        super().resizeEvent(event)
        contents_rect = self.contentsRect()
        self._line_number_area.setGeometry(
            QRect(contents_rect.left(), contents_rect.top(), self.line_number_area_width(), contents_rect.height())
        )

    def paint_line_numbers(self, event) -> None:  # noqa: ANN001
        """Zeichnet die Zeilennummern in den Randbereich (von _LineNumberArea aufgerufen)."""
        painter = QPainter(self._line_number_area)
        painter.fillRect(event.rect(), QColor(PALETTE.surface))

        block = self.firstVisibleBlock()
        block_number = block.blockNumber()
        top = int(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + int(self.blockBoundingRect(block).height())

        painter.setPen(QColor(PALETTE.text_secondary))
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                number_text = str(block_number + 1)
                painter.drawText(
                    0,
                    top,
                    self._line_number_area.width() - 6,
                    self.fontMetrics().height(),
                    Qt.AlignmentFlag.AlignRight,
                    number_text,
                )
            block = block.next()
            top = bottom
            bottom = top + int(self.blockBoundingRect(block).height())
            block_number += 1

    # ------------------------------------------------------------------
    # Aktuelle Zeile hervorheben
    # ------------------------------------------------------------------

    def _highlight_current_line(self) -> None:
        """Hebt die Zeile hervor, in der sich der Cursor aktuell befindet."""
        selection = QTextEdit.ExtraSelection()
        line_color = QColor(PALETTE.surface_alt)
        selection.format.setBackground(line_color)
        selection.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
        selection.cursor = self.textCursor()
        selection.cursor.clearSelection()
        self.setExtraSelections([selection])
