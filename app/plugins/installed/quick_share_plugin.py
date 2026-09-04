"""Pandora® Commander – Plugin: Quick-Share (Temp-Link-Upload).

Fügt dem Rechtsklick-Kontextmenü der Dateipanels den Eintrag
"Quick-Share (Link erzeugen) …" hinzu. Lädt jede markierte Datei per
HTTP PUT zu transfer.sh hoch (ein kostenloser Dienst für temporäre,
zeitlich begrenzte Download-Links ohne Account-Zwang) und zeigt die
resultierenden Links in einem Ergebnisdialog an, aus dem heraus sie
einzeln oder gesammelt in die Zwischenablage kopiert werden können.

Wichtige Hinweise, die auch im Dialog angezeigt werden:
    * Hochgeladene Dateien sind über den Link öffentlich für jeden
      erreichbar, der ihn kennt – das Plugin ist daher bewusst nicht
      in Kontextmenüs mit vorselektierten sensiblen Dateien o. Ä.
      integriert, sondern erfordert stets eine explizite,
      bewusste Nutzerauswahl und -bestätigung vor dem Upload.
    * transfer.sh löscht Dateien nach einer gewissen Zeit automatisch
      (Richtwert des Dienstes: 14 Tage oder nach einer bestimmten
      Anzahl Downloads, abhängig von der aktuellen Diensteinstellung).
    * Der Upload benötigt eine funktionierende Internetverbindung;
      Verbindungsfehler werden abgefangen und verständlich angezeigt.

Der Upload läuft je Datei in einem Hintergrund-Thread, damit die
Oberfläche während der Übertragung nicht einfriert.
"""

from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QThread, Qt, pyqtSignal
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.core.logging_setup import get_logger
from app.plugins.plugin_manager import PandoraPlugin

logger = get_logger(__name__)

_UPLOAD_BASE_URL = "https://transfer.sh/"
_UPLOAD_TIMEOUT_SECONDS = 300
_MAX_UPLOAD_BYTES = 500 * 1024 * 1024  # 500 MB – großzügige, aber sinnvolle Obergrenze.


def _upload_file(path: Path) -> str:
    """Lädt eine Datei per HTTP PUT zu transfer.sh hoch und liefert den Download-Link."""
    url = _UPLOAD_BASE_URL + urllib.parse.quote(path.name)

    with path.open("rb") as file_handle:
        data = file_handle.read()

    request = urllib.request.Request(url, data=data, method="PUT")
    request.add_header("Content-Length", str(len(data)))

    with urllib.request.urlopen(request, timeout=_UPLOAD_TIMEOUT_SECONDS) as response:
        body = response.read().decode("utf-8").strip()
    if not body.startswith("http"):
        raise RuntimeError(f"Unerwartete Antwort vom Server: {body[:200]}")
    return body


class _UploadWorker(QThread):
    """Lädt eine Liste von Dateien nacheinander zu transfer.sh hoch."""

    file_finished = pyqtSignal(Path, bool, str)  # Pfad, Erfolg, Link oder Fehlermeldung
    all_finished = pyqtSignal()

    def __init__(self, paths: list[Path]) -> None:
        super().__init__()
        self._paths = paths

    def run(self) -> None:  # noqa: D102 - QThread-Standardmethode
        for path in self._paths:
            try:
                size_bytes = path.stat().st_size
                if size_bytes > _MAX_UPLOAD_BYTES:
                    raise RuntimeError(
                        f"Datei ist mit {size_bytes / (1024 * 1024):.1f} MB größer als das "
                        "konfigurierte Limit von 500 MB."
                    )
                link = _upload_file(path)
                self.file_finished.emit(path, True, link)
            except (OSError, urllib.error.URLError, RuntimeError, TimeoutError) as error:
                self.file_finished.emit(path, False, str(error))
        self.all_finished.emit()


class QuickShareDialog(QDialog):
    """Zeigt Upload-Fortschritt und die entstandenen Freigabe-Links an."""

    def __init__(self, paths: list[Path], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Pandora® Commander – Quick-Share")
        self.resize(620, 380)

        self._link_by_path: dict[Path, str] = {}

        self._hint_label = QLabel(
            "Hochgeladene Dateien sind über den Link öffentlich erreichbar und werden "
            "von transfer.sh nach einiger Zeit automatisch gelöscht."
        )
        self._hint_label.setWordWrap(True)
        self._hint_label.setStyleSheet("color: gray; font-size: 11px;")

        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, len(paths))
        self._progress_bar.setValue(0)

        self._result_list = QListWidget()

        self._copy_all_button = QPushButton("Alle Links kopieren")
        self._copy_all_button.setEnabled(False)
        self._copy_all_button.clicked.connect(self._on_copy_all_clicked)

        close_button = QPushButton("Schließen")
        close_button.clicked.connect(self.close)

        bottom_row = QHBoxLayout()
        bottom_row.addWidget(self._copy_all_button)
        bottom_row.addStretch(1)
        bottom_row.addWidget(close_button)

        layout = QVBoxLayout(self)
        layout.addWidget(self._hint_label)
        layout.addWidget(self._progress_bar)
        layout.addWidget(self._result_list, stretch=1)
        layout.addLayout(bottom_row)

        self._worker = _UploadWorker(paths)
        self._worker.file_finished.connect(self._on_file_finished)
        self._worker.all_finished.connect(self._on_all_finished)
        self._worker.start()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt-Überschreibung
        if self._worker.isRunning():
            self._worker.wait(50)
        super().closeEvent(event)

    def _on_file_finished(self, path: Path, success: bool, message: str) -> None:
        self._progress_bar.setValue(self._progress_bar.value() + 1)

        if success:
            self._link_by_path[path] = message
            item = QListWidgetItem(f"✅ {path.name} → {message}")
            row_widget = self._build_result_row(path.name, message)
            self._result_list.addItem(item)
            self._result_list.setItemWidget(item, row_widget)
        else:
            item = QListWidgetItem(f"❌ {path.name}: {message}")
            self._result_list.addItem(item)

    def _build_result_row(self, file_name: str, link: str) -> QWidget:
        row_widget = QWidget()
        row_layout = QHBoxLayout(row_widget)
        row_layout.setContentsMargins(2, 2, 2, 2)
        row_layout.addWidget(QLabel(f"{file_name} →"))
        link_label = QLabel(f'<a href="{link}">{link}</a>')
        link_label.setOpenExternalLinks(True)
        row_layout.addWidget(link_label, stretch=1)
        copy_button = QPushButton("Kopieren")
        copy_button.clicked.connect(lambda checked=False, url=link: self._copy_to_clipboard(url))
        row_layout.addWidget(copy_button)
        return row_widget

    def _on_all_finished(self) -> None:
        self._copy_all_button.setEnabled(bool(self._link_by_path))

    def _on_copy_all_clicked(self) -> None:
        self._copy_to_clipboard("\n".join(self._link_by_path.values()))

    @staticmethod
    def _copy_to_clipboard(text: str) -> None:
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(text)


class QuickSharePlugin(PandoraPlugin):
    """Plugin zum schnellen Hochladen markierter Dateien zu einem temporären Freigabe-Link."""

    name = "Quick-Share"
    version = "1.0"
    author = "AKI_SystemDown®"
    description = (
        "Fügt dem Kontextmenü 'Quick-Share (Link erzeugen) …' hinzu: lädt markierte "
        "Dateien zu transfer.sh hoch und zeigt die entstehenden, zeitlich begrenzten "
        "Download-Links zum Kopieren an."
    )

    def __init__(self) -> None:
        self._context: dict[str, Any] = {}
        self._open_dialogs: list[QuickShareDialog] = []

    def on_load(self, context: dict[str, Any]) -> None:
        self._context = context
        logger.info("%s geladen.", self.name)

    def on_unload(self) -> None:
        for dialog in self._open_dialogs:
            dialog.close()
        self._open_dialogs.clear()

    def build_context_menu_entries(
        self, context: dict[str, Any], selected_paths: list[Path]
    ) -> list[QAction]:
        file_paths = [path for path in selected_paths if path.is_file()]
        if not file_paths:
            return []

        main_window = context.get("main_window")
        action = QAction("Quick-Share (Link erzeugen) …", main_window)
        action.triggered.connect(
            lambda checked=False, paths=file_paths: self._start(paths)
        )
        return [action]

    def _start(self, paths: list[Path]) -> None:
        main_window = self._context.get("main_window")
        confirmed = QMessageBox.question(
            main_window,
            "Quick-Share bestätigen",
            f"{len(paths)} Datei(en) öffentlich zu transfer.sh hochladen?\n\n"
            "Jeder mit dem entstehenden Link kann die Datei(en) herunterladen.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirmed != QMessageBox.StandardButton.Yes:
            return

        dialog = QuickShareDialog(paths, parent=main_window)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.destroyed.connect(
            lambda: self._open_dialogs.remove(dialog) if dialog in self._open_dialogs else None
        )
        self._open_dialogs.append(dialog)
        dialog.show()
