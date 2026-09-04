"""Pandora® Commander – Fortschrittsdialog für Dateioperationen.

Zeigt den Fortschritt eines im Hintergrund laufenden
FileOperationWorker (Kopieren/Verschieben/Löschen) an: Fortschritts-
balken, aktuell verarbeitetes Element, laufend aktualisierte
Fehlerliste sowie einen Abbrechen-Button. Der Dialog übernimmt keine
eigene Fehlerbehandlung – er ist eine reine, wiederverwendbare
Anzeige- und Steuerungskomponente für einen bereits konfigurierten
Worker.

Verwendung (aus einer späteren Datei heraus, z. B. beim Verdrahten
von F5/F6/F8 in main_window.py):

    worker = FileOperationWorker(
        operation=OperationType.COPY,
        sources=selected_paths,
        destination=target_dir,
    )
    dialog = FileOperationProgressDialog(
        title="Kopieren", worker=worker, parent=self
    )
    result = dialog.run()  # startet den Worker und blockiert modal
    # result ist das OperationResult nach Abschluss/Abbruch
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.core.filesystem.file_operations import FileOperationWorker, OperationResult
from app.core.logging_setup import get_logger

logger = get_logger(__name__)


class FileOperationProgressDialog(QDialog):
    """Modaler Dialog, der einen FileOperationWorker startet und begleitet.

    Der Dialog schließt sich automatisch, sobald der Worker fertig
    ist (operation_finished-Signal) – entweder erfolgreich, teilweise
    fehlgeschlagen oder durch den Nutzer abgebrochen. Das Ergebnis
    bleibt über die Eigenschaft result auch nach dem Schließen
    abrufbar.

    Args:
        title: Fenstertitel und Überschrift (z. B. "Kopieren").
        worker: Bereits konfigurierter, aber noch nicht gestarteter
            FileOperationWorker.
        parent: Optionales Eltern-Widget.
    """

    def __init__(
        self,
        title: str,
        worker: FileOperationWorker,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._worker = worker
        self._result: OperationResult | None = None
        self._error_count = 0

        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(440)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)

        self._headline_label = QLabel(title)
        self._current_item_label = QLabel("Wird vorbereitet …")
        self._progress_bar = QProgressBar()
        self._error_list = QListWidget()
        self._cancel_button = QPushButton("Abbrechen")
        self._close_button = QPushButton("Schließen")

        self._setup_ui()
        self._connect_worker_signals()

    # ------------------------------------------------------------------
    # Aufbau
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        """Baut das Layout des Dialogs."""
        headline_font = self._headline_label.font()
        headline_font.setBold(True)
        headline_font.setPointSize(headline_font.pointSize() + 1)
        self._headline_label.setFont(headline_font)

        self._current_item_label.setWordWrap(True)
        self._current_item_label.setStyleSheet("color: #9a9da2;")

        self._progress_bar.setMinimum(0)
        self._progress_bar.setMaximum(100)
        self._progress_bar.setValue(0)
        self._progress_bar.setTextVisible(True)

        self._error_list.setVisible(False)
        self._error_list.setMaximumHeight(120)

        self._close_button.setVisible(False)
        self._close_button.clicked.connect(self.accept)
        self._cancel_button.clicked.connect(self._on_cancel_clicked)

        button_box = QDialogButtonBox()
        button_box.addButton(self._cancel_button, QDialogButtonBox.ButtonRole.RejectRole)
        button_box.addButton(self._close_button, QDialogButtonBox.ButtonRole.AcceptRole)

        layout = QVBoxLayout(self)
        layout.addWidget(self._headline_label)
        layout.addWidget(self._current_item_label)
        layout.addWidget(self._progress_bar)
        layout.addWidget(self._error_list)
        layout.addWidget(button_box)

    def _connect_worker_signals(self) -> None:
        """Verbindet die Signale des Workers mit den UI-Elementen."""
        self._worker.progress_changed.connect(self._on_progress_changed)
        self._worker.item_failed.connect(self._on_item_failed)
        self._worker.operation_finished.connect(self._on_operation_finished)

    # ------------------------------------------------------------------
    # Öffentliche API
    # ------------------------------------------------------------------

    def run(self) -> OperationResult | None:
        """Startet den Worker und zeigt den Dialog modal an.

        Returns:
            Das OperationResult nach Abschluss, oder None, falls der
            Dialog geschlossen wurde, bevor der Worker fertig meldete
            (sollte im Normalbetrieb nicht vorkommen, da der Nutzer
            nur über Abbrechen/Schließen interagieren kann).
        """
        self._worker.start()
        self.exec()
        return self._result

    @property
    def result(self) -> OperationResult | None:
        """Das Ergebnis der Operation, sobald sie abgeschlossen ist."""
        return self._result

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_progress_changed(self, done: int, total: int, current_name: str) -> None:
        """Aktualisiert Fortschrittsbalken und aktuellen Elementnamen."""
        percent = int((done / total) * 100) if total > 0 else 0
        self._progress_bar.setValue(min(percent, 100))
        self._progress_bar.setFormat(f"{done} / {total} – %p%")
        self._current_item_label.setText(current_name)

    def _on_item_failed(self, path_text: str, message: str) -> None:
        """Fügt einen fehlgeschlagenen Eintrag der sichtbaren Fehlerliste hinzu."""
        self._error_count += 1
        self._error_list.setVisible(True)
        item = QListWidgetItem(f"{path_text}: {message}")
        self._error_list.addItem(item)

    def _on_cancel_clicked(self) -> None:
        """Fordert einen Abbruch an und deaktiviert den Button gegen Doppelklicks."""
        self._cancel_button.setEnabled(False)
        self._cancel_button.setText("Wird abgebrochen …")
        self._worker.cancel()

    def _on_operation_finished(self, result: object) -> None:
        """Reagiert auf das Ende der Operation: zeigt Zusammenfassung an."""
        assert isinstance(result, OperationResult)
        self._result = result

        self._progress_bar.setValue(100)
        self._current_item_label.setText(result.summary_text().capitalize())

        self._cancel_button.setVisible(False)
        self._close_button.setVisible(True)
        self._close_button.setDefault(True)
        self._close_button.setFocus()

        if not result.has_errors and not result.cancelled:
            # Bei vollständigem Erfolg ohne jegliche Fehler schließt
            # sich der Dialog selbstständig, damit der Nutzer bei
            # Routineoperationen nicht extra bestätigen muss.
            self.accept()

    # ------------------------------------------------------------------
    # Schließen während laufender Operation verhindern
    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:  # noqa: ANN001, N802
        """Verhindert das Schließen per Fenster-X, solange der Worker läuft.

        Stattdessen wird ein Abbruch angefordert; der Dialog schließt
        sich dann regulär über operation_finished.
        """
        if self._worker.isRunning():
            self._worker.cancel()
            event.ignore()
            return
        super().closeEvent(event)
