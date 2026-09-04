"""Pandora® Commander – Integrierter Editor: Editor-Fenster.

Bettet CodeEditor (app/ui/widgets/code_editor.py) in ein
eigenständiges, nicht-modales Fenster mit Symbolleiste sowie
einblendbaren Suchen-/Ersetzen-Leisten ein. Mehrere EditorWindow-
Instanzen können gleichzeitig geöffnet sein (eine pro bearbeiteter
Datei), unabhängig vom Hauptfenster.

Wird in einer der nächsten Dateien über F4 aus main_window.py heraus
geöffnet.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QCloseEvent, QKeySequence, QTextCursor, QTextDocument
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStatusBar,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from app.core.logging_setup import get_logger
from app.main import APP_NAME
from app.ui.widgets.code_editor import CodeEditor

logger = get_logger(__name__)


class _FindBar(QWidget):
    """Einblendbare Leiste zum Vorwärts-/Rückwärtssuchen im Editor.

    Signals werden bewusst nicht verwendet; die Buttons rufen direkt
    Callbacks des Elternfensters auf, da diese Leiste ausschließlich
    innerhalb von EditorWindow eingesetzt wird.

    Args:
        on_find_next: Callback, aufgerufen mit dem Suchtext, um
            vorwärts zu suchen.
        on_find_previous: Callback, aufgerufen mit dem Suchtext, um
            rückwärts zu suchen.
        on_close: Callback zum Ausblenden der Leiste.
        parent: Optionales Eltern-Widget.
    """

    def __init__(
        self,
        on_find_next,
        on_find_previous,
        on_close,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        self.search_field = QLineEdit()
        self.search_field.setPlaceholderText("Suchen …")
        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #9a9da2;")

        next_button = QPushButton("▼")
        next_button.setToolTip("Nächstes Vorkommen (Enter)")
        previous_button = QPushButton("▲")
        previous_button.setToolTip("Vorheriges Vorkommen (Umschalt+Enter)")
        close_button = QPushButton("✕")
        close_button.setFixedWidth(28)

        self.search_field.returnPressed.connect(lambda: on_find_next(self.search_field.text()))
        next_button.clicked.connect(lambda: on_find_next(self.search_field.text()))
        previous_button.clicked.connect(lambda: on_find_previous(self.search_field.text()))
        close_button.clicked.connect(on_close)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.addWidget(QLabel("Suchen:"))
        layout.addWidget(self.search_field, stretch=1)
        layout.addWidget(previous_button)
        layout.addWidget(next_button)
        layout.addWidget(self.status_label)
        layout.addWidget(close_button)


class _ReplaceBar(QWidget):
    """Einblendbare Leiste zum Ersetzen von Text im Editor.

    Args:
        on_replace_one: Callback (suchtext, ersatztext) für "Ersetzen".
        on_replace_all: Callback (suchtext, ersatztext) für "Alle ersetzen".
        on_close: Callback zum Ausblenden der Leiste.
        parent: Optionales Eltern-Widget.
    """

    def __init__(
        self,
        on_replace_one,
        on_replace_all,
        on_close,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        self.search_field = QLineEdit()
        self.search_field.setPlaceholderText("Suchen …")
        self.replace_field = QLineEdit()
        self.replace_field.setPlaceholderText("Ersetzen durch …")

        replace_one_button = QPushButton("Ersetzen")
        replace_all_button = QPushButton("Alle ersetzen")
        close_button = QPushButton("✕")
        close_button.setFixedWidth(28)

        replace_one_button.clicked.connect(
            lambda: on_replace_one(self.search_field.text(), self.replace_field.text())
        )
        replace_all_button.clicked.connect(
            lambda: on_replace_all(self.search_field.text(), self.replace_field.text())
        )
        close_button.clicked.connect(on_close)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.addWidget(QLabel("Suchen:"))
        layout.addWidget(self.search_field, stretch=1)
        layout.addWidget(QLabel("Ersetzen durch:"))
        layout.addWidget(self.replace_field, stretch=1)
        layout.addWidget(replace_one_button)
        layout.addWidget(replace_all_button)
        layout.addWidget(close_button)


class EditorWindow(QMainWindow):
    """Eigenständiges Fenster für den integrierten Editor.

    Args:
        path: Optional sofort zu ladende Datei.
        parent: Optionales Eltern-Widget (üblicherweise None, damit
            das Editor-Fenster unabhängig vom Hauptfenster bleibt).
    """

    def __init__(self, path: Path | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.resize(900, 650)

        self._editor = CodeEditor()
        self._status_bar = QStatusBar()

        self._find_bar = _FindBar(
            on_find_next=self._find_next,
            on_find_previous=self._find_previous,
            on_close=self._hide_find_bar,
        )
        self._replace_bar = _ReplaceBar(
            on_replace_one=self._replace_one,
            on_replace_all=self._replace_all,
            on_close=self._hide_replace_bar,
        )
        self._find_bar.setVisible(False)
        self._replace_bar.setVisible(False)

        self._setup_central_widget()
        self._setup_actions()
        self._setup_toolbar()
        self.setStatusBar(self._status_bar)

        self._editor.document().modificationChanged.connect(self._update_window_title)

        if path is not None:
            self.open_file(path)
        else:
            self._update_window_title()

    # ------------------------------------------------------------------
    # Aufbau
    # ------------------------------------------------------------------

    def _setup_central_widget(self) -> None:
        """Baut das zentrale Layout: Editor, darunter Such-/Ersetzen-Leisten."""
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._editor, stretch=1)
        layout.addWidget(self._find_bar)
        layout.addWidget(self._replace_bar)
        self.setCentralWidget(container)

    def _setup_actions(self) -> None:
        """Erstellt die QAction-Objekte für Symbolleiste/Tastenkürzel."""
        self.action_save = QAction("Speichern", self)
        self.action_save.setShortcut(QKeySequence.StandardKey.Save)
        self.action_save.triggered.connect(self._on_save)

        self.action_find = QAction("Suchen", self)
        self.action_find.setShortcut(QKeySequence.StandardKey.Find)
        self.action_find.triggered.connect(self._show_find_bar)

        self.action_replace = QAction("Ersetzen", self)
        self.action_replace.setShortcut(QKeySequence.StandardKey.Replace)
        self.action_replace.triggered.connect(self._show_replace_bar)

        self.action_close = QAction("Schließen", self)
        self.action_close.setShortcut(QKeySequence("Ctrl+W"))
        self.action_close.triggered.connect(self.close)

    def _setup_toolbar(self) -> None:
        """Baut die Symbolleiste des Editor-Fensters."""
        toolbar = QToolBar("Editor", self)
        toolbar.setMovable(False)
        toolbar.addAction(self.action_save)
        toolbar.addSeparator()
        toolbar.addAction(self.action_find)
        toolbar.addAction(self.action_replace)
        toolbar.addSeparator()
        toolbar.addAction(self.action_close)
        self.addToolBar(toolbar)

    # ------------------------------------------------------------------
    # Datei laden/speichern
    # ------------------------------------------------------------------

    def open_file(self, path: Path) -> None:
        """Lädt eine Datei in den Editor und aktualisiert den Fenstertitel.

        Args:
            path: Zu öffnende Datei.
        """
        try:
            self._editor.load_file(path)
        except (OSError, UnicodeDecodeError) as error:
            QMessageBox.critical(
                self, "Datei öffnen", f"Die Datei konnte nicht geöffnet werden:\n{error}"
            )
            return
        self._update_window_title()
        self._status_bar.showMessage(f"Geöffnet: {path}", 3000)

    def _on_save(self) -> None:
        """Speichert die aktuelle Datei; robust gegenüber Schreibfehlern."""
        if self._editor.current_path is None:
            return
        try:
            self._editor.save_file()
        except OSError as error:
            QMessageBox.critical(
                self, "Speichern", f"Die Datei konnte nicht gespeichert werden:\n{error}"
            )
            return
        self._status_bar.showMessage("Gespeichert.", 2000)

    def _update_window_title(self, *_args: object) -> None:
        """Aktualisiert den Fenstertitel inkl. Änderungs-Sternchen."""
        path = self._editor.current_path
        name = path.name if path is not None else "Unbenannt"
        modified_marker = "*" if self._editor.document().isModified() else ""
        self.setWindowTitle(f"{modified_marker}{name} – {APP_NAME} Editor")

    # ------------------------------------------------------------------
    # Suchen
    # ------------------------------------------------------------------

    def _show_find_bar(self) -> None:
        """Blendet die Suchen-Leiste ein und fokussiert das Eingabefeld."""
        self._replace_bar.setVisible(False)
        self._find_bar.setVisible(True)
        self._find_bar.search_field.setFocus()
        self._find_bar.search_field.selectAll()

    def _hide_find_bar(self) -> None:
        """Blendet die Suchen-Leiste aus."""
        self._find_bar.setVisible(False)
        self._editor.setFocus()

    def _find_next(self, text: str) -> None:
        """Sucht das nächste Vorkommen von text ab der Cursorposition."""
        self._run_find(text, backwards=False)

    def _find_previous(self, text: str) -> None:
        """Sucht das vorherige Vorkommen von text vor der Cursorposition."""
        self._run_find(text, backwards=True)

    def _run_find(self, text: str, backwards: bool) -> None:
        """Führt die eigentliche Suche im Editordokument aus.

        Wird nichts (mehr) gefunden, springt die Suche einmal an den
        Anfang bzw. das Ende des Dokuments zurück (umlaufende Suche),
        damit Nutzer nicht manuell den Cursor zurücksetzen müssen.

        Args:
            text: Suchtext. Leere Eingaben werden ignoriert.
            backwards: Ob rückwärts statt vorwärts gesucht wird.
        """
        if not text:
            return

        flags = QTextDocument.FindFlag.FindBackward if backwards else QTextDocument.FindFlag(0)
        found = self._editor.find(text, flags)

        if not found:
            cursor = self._editor.textCursor()
            cursor.movePosition(
                QTextCursor.MoveOperation.End if backwards else QTextCursor.MoveOperation.Start
            )
            self._editor.setTextCursor(cursor)
            found = self._editor.find(text, flags)

        self._find_bar.status_label.setText("" if found else "Nicht gefunden")

    # ------------------------------------------------------------------
    # Ersetzen
    # ------------------------------------------------------------------

    def _show_replace_bar(self) -> None:
        """Blendet die Ersetzen-Leiste (inkl. Suchen-Leiste) ein."""
        self._find_bar.setVisible(False)
        self._replace_bar.setVisible(True)
        self._replace_bar.search_field.setFocus()

    def _hide_replace_bar(self) -> None:
        """Blendet die Ersetzen-Leiste aus."""
        self._replace_bar.setVisible(False)
        self._editor.setFocus()

    def _replace_one(self, search_text: str, replacement: str) -> None:
        """Ersetzt das aktuell markierte Vorkommen (falls es search_text entspricht)
        und springt anschließend zum nächsten Vorkommen.

        Args:
            search_text: Zu suchender Text.
            replacement: Ersatztext.
        """
        if not search_text:
            return

        cursor = self._editor.textCursor()
        if cursor.hasSelection() and cursor.selectedText() == search_text:
            cursor.insertText(replacement)
            self._editor.setTextCursor(cursor)

        self._run_find(search_text, backwards=False)

    def _replace_all(self, search_text: str, replacement: str) -> None:
        """Ersetzt alle Vorkommen von search_text im gesamten Dokument.

        Args:
            search_text: Zu suchender Text.
            replacement: Ersatztext.
        """
        if not search_text:
            return

        document_cursor = self._editor.textCursor()
        document_cursor.beginEditBlock()

        cursor = self._editor.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        self._editor.setTextCursor(cursor)

        replaced_count = 0
        while self._editor.find(search_text):
            found_cursor = self._editor.textCursor()
            found_cursor.insertText(replacement)
            self._editor.setTextCursor(found_cursor)
            replaced_count += 1

        document_cursor.endEditBlock()
        self._status_bar.showMessage(f"{replaced_count} Vorkommen ersetzt.", 3000)

    # ------------------------------------------------------------------
    # Schließen: bei ungespeicherten Änderungen nachfragen
    # ------------------------------------------------------------------

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        """Fragt bei ungespeicherten Änderungen nach, bevor das Fenster schließt."""
        if not self._editor.document().isModified():
            super().closeEvent(event)
            return

        answer = QMessageBox.question(
            self,
            "Ungespeicherte Änderungen",
            "Es gibt ungespeicherte Änderungen. Vor dem Schließen speichern?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )

        if answer == QMessageBox.StandardButton.Cancel:
            event.ignore()
            return
        if answer == QMessageBox.StandardButton.Save:
            self._on_save()
            if self._editor.document().isModified():
                # Speichern ist fehlgeschlagen (Fehlermeldung wurde
                # bereits gezeigt) – Fenster in diesem Fall nicht
                # schließen, damit keine Änderungen verloren gehen.
                event.ignore()
                return

        super().closeEvent(event)
