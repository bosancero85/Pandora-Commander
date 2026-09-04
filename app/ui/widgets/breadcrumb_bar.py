"""Pandora® Commander – Klickbare Breadcrumb-Leiste.

Ersetzt die reine Pfad-Texteingabe durch eine Kette klickbarer
Segment-Buttons (wie in modernen Dateimanagern/Explorer): Jedes
Pfadsegment ist ein eigener Button, ein Klick darauf navigiert
direkt in dieses Verzeichnis. Für Sonderfälle (UNC-Pfade, noch
nicht existierende Zielpfade, schnelles Einfügen eines kompletten
Pfades) kann jederzeit in einen klassischen Texteingabemodus
gewechselt werden – per Klick auf den Editier-Knopf oder auf die
freie Fläche rechts neben den Segmenten.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSizePolicy,
    QStackedLayout,
    QToolButton,
    QWidget,
)


class _PathLineEdit(QLineEdit):
    """QLineEdit, das Fokusverlust und Escape als Signale meldet.

    Wird benötigt, damit die Breadcrumb-Leiste beim Verlassen des
    Textfeldes (Klick woanders hin, Tab-Wechsel, …) automatisch
    wieder in den Breadcrumb-Modus zurückspringt, auch ohne dass
    der Nutzer Enter gedrückt hat.
    """

    focus_lost = pyqtSignal()
    escape_pressed = pyqtSignal()

    def focusOutEvent(self, event) -> None:  # noqa: N802 - Qt-Override
        super().focusOutEvent(event)
        self.focus_lost.emit()

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt-Override
        if event.key() == Qt.Key.Key_Escape:
            self.escape_pressed.emit()
            return
        super().keyPressEvent(event)


class _ClickableSpacer(QWidget):
    """Unsichtbarer, klickbarer Füllbereich rechts neben den Segmenten.

    Ein Klick auf die freie Fläche neben den Breadcrumb-Segmenten
    wechselt – analog zu vielen Dateimanagern – ebenfalls in den
    Texteingabemodus, ohne dass extra der kleine Editier-Knopf
    getroffen werden muss.
    """

    clicked = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class BreadcrumbBar(QWidget):
    """Klickbare Pfad-Breadcrumb-Leiste mit Text-Editiermodus.

    Zeigt den aktuellen Pfad standardmäßig als Kette klickbarer
    Segment-Buttons. Ein Klick auf ein Segment navigiert direkt
    dorthin (path_selected). Ein Klick auf den Editier-Knopf, die
    freie Fläche daneben, oder Strg+L (vom Hauptfenster verdrahtet)
    wechselt in ein klassisches Texteingabefeld; Enter bestätigt
    den eingegebenen Pfad (ebenfalls über path_selected), Escape
    oder Fokusverlust kehrt ohne Navigation zum Breadcrumb-Modus
    zurück.

    Signals:
        path_selected: Ein Segment wurde angeklickt oder ein im
            Textmodus eingegebener Pfad per Enter bestätigt.
            Übergibt den (noch nicht validierten) Zielpfad.
    """

    path_selected = pyqtSignal(Path)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._current_path = Path.home()
        self._segment_buttons: list[QToolButton] = []

        self._crumb_container = QWidget(self)
        self._crumb_layout = QHBoxLayout(self._crumb_container)
        self._crumb_layout.setContentsMargins(2, 0, 2, 0)
        self._crumb_layout.setSpacing(0)

        self._edit_button = QToolButton(self._crumb_container)
        self._edit_button.setText("✎")
        self._edit_button.setToolTip("Pfad als Text bearbeiten (Strg+L)")
        self._edit_button.setAutoRaise(True)
        self._edit_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._edit_button.clicked.connect(self.enter_edit_mode)

        self._spacer = _ClickableSpacer(self._crumb_container)
        self._spacer.clicked.connect(self.enter_edit_mode)
        self._spacer.setCursor(Qt.CursorShape.IBeamCursor)

        self._path_edit = _PathLineEdit(self)
        self._path_edit.setPlaceholderText("Pfad eingeben und Enter drücken …")
        self._path_edit.returnPressed.connect(self._on_edit_confirmed)
        self._path_edit.focus_lost.connect(self._on_edit_focus_lost)
        self._path_edit.escape_pressed.connect(self._on_edit_cancelled)

        self._stack = QStackedLayout(self)
        self._stack.setContentsMargins(0, 0, 0, 0)
        self._stack.addWidget(self._crumb_container)  # Index 0: Breadcrumbs
        self._stack.addWidget(self._path_edit)  # Index 1: Texteingabe

        self.set_path(self._current_path)

    # ------------------------------------------------------------------
    # Öffentliche API
    # ------------------------------------------------------------------

    def set_path(self, path: Path) -> None:
        """Aktualisiert die Anzeige auf den übergebenen Pfad.

        Baut die Segment-Buttons neu auf und synchronisiert das
        (verdeckte) Textfeld. Wechselt außerdem zurück in den
        Breadcrumb-Anzeigemodus, falls gerade der Textmodus aktiv war.

        Args:
            path: Neu anzuzeigendes, aktuelles Verzeichnis.
        """
        self._current_path = path
        self._path_edit.setText(str(path))
        self._rebuild_segments()
        self._stack.setCurrentIndex(0)

    def enter_edit_mode(self) -> None:
        """Wechselt in den Texteingabemodus und markiert den Text."""
        self._path_edit.setText(str(self._current_path))
        self._stack.setCurrentIndex(1)
        self._path_edit.setFocus(Qt.FocusReason.MouseFocusReason)
        self._path_edit.selectAll()

    # ------------------------------------------------------------------
    # Breadcrumb-Aufbau
    # ------------------------------------------------------------------

    def _rebuild_segments(self) -> None:
        """Erzeugt für jedes Pfadsegment einen klickbaren Button neu."""
        while self._crumb_layout.count():
            item = self._crumb_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
        self._segment_buttons.clear()

        parts = self._current_path.parts
        if not parts:
            parts = (str(self._current_path),)

        cumulative = Path(parts[0])
        self._add_segment_button(parts[0].rstrip("\\/") or parts[0], cumulative)
        for part in parts[1:]:
            cumulative = cumulative / part
            self._add_separator()
            self._add_segment_button(part, cumulative)

        self._crumb_layout.addWidget(self._spacer, 1)
        self._crumb_layout.addWidget(self._edit_button, 0)

    def _add_segment_button(self, label: str, target: Path) -> None:
        """Fügt einen Segment-Button hinzu, der bei Klick target anspringt."""
        button = QToolButton(self._crumb_container)
        button.setText(label)
        button.setAutoRaise(True)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setToolTip(str(target))
        button.clicked.connect(lambda checked=False, p=target: self._on_segment_clicked(p))
        self._crumb_layout.addWidget(button)
        self._segment_buttons.append(button)

    def _add_separator(self) -> None:
        """Fügt ein kleines Trennsymbol zwischen zwei Segmenten ein."""
        separator = QLabel("›", self._crumb_container)
        separator.setEnabled(False)
        self._crumb_layout.addWidget(separator)

    # ------------------------------------------------------------------
    # Interne Slots
    # ------------------------------------------------------------------

    def _on_segment_clicked(self, target: Path) -> None:
        self.path_selected.emit(target)

    def _on_edit_confirmed(self) -> None:
        raw_text = self._path_edit.text().strip()
        self._stack.setCurrentIndex(0)
        if raw_text:
            self.path_selected.emit(Path(raw_text).expanduser())

    def _on_edit_cancelled(self) -> None:
        self._path_edit.setText(str(self._current_path))
        self._stack.setCurrentIndex(0)

    def _on_edit_focus_lost(self) -> None:
        # Nur zurückschalten, wenn nicht ohnehin schon durch Enter/Escape
        # geschehen (setCurrentIndex ist idempotent, daher unkritisch).
        if self._stack.currentIndex() == 1:
            self._stack.setCurrentIndex(0)
