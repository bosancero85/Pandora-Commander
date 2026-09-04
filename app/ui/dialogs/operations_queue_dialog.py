"""Pandora® Commander – Anzeige der Operations-Warteschlange.

Nicht-modales Fenster, das alle Einträge eines OperationQueueManager
als Tabelle anzeigt: eine Zeile pro Kopier-/Verschiebe-/Löschvorgang,
jeweils mit eigener Fortschrittsanzeige, Status und einem
Abbrechen-Button. Im Unterschied zu einem klassischen, modalen
Fortschrittsdialog blockiert dieses Fenster die Hauptoberfläche
nicht: es wird über show() statt exec() angezeigt, sodass in beiden
Panels weitergearbeitet und zusätzliche Operationen gestartet werden
können, während bereits welche laufen.

Das Fenster ist als Singleton gedacht (eine Instanz pro
MainWindow, siehe dort _on_toggle_operations_queue): Schließen über
das Fenster-X versteckt es lediglich, es wird nicht zerstört – die
zugrunde liegende Warteschlange läuft davon vollkommen unbeeinflusst
im Hintergrund weiter.

Verwendung:

    dialog = OperationsQueueDialog(queue_manager=self._operation_queue, parent=self)
    dialog.show()
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.core.filesystem.file_operations import OperationResult
from app.core.filesystem.operation_queue import OperationQueueManager, QueueItemState
from app.core.logging_setup import get_logger

logger = get_logger(__name__)

_COLUMN_TITLE = 0
_COLUMN_PROGRESS = 1
_COLUMN_STATUS = 2
_COLUMN_ACTION = 3

_STATUS_TEXT: dict[QueueItemState, str] = {
    QueueItemState.QUEUED: "Wartet …",
    QueueItemState.RUNNING: "Läuft …",
    QueueItemState.FINISHED: "Abgeschlossen",
    QueueItemState.CANCELLED: "Abgebrochen",
}


class OperationsQueueDialog(QDialog):
    """Nicht-modales Fenster mit einer Zeile pro Warteschlangeneintrag.

    Args:
        queue_manager: Der zentrale OperationQueueManager, dessen
            Einträge angezeigt werden.
        parent: Optionales Eltern-Widget.
    """

    def __init__(self, queue_manager: OperationQueueManager, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._queue_manager = queue_manager
        self._row_by_job_id: dict[int, int] = {}
        self._progress_bar_by_job_id: dict[int, QProgressBar] = {}
        self._status_label_by_job_id: dict[int, QLabel] = {}
        self._cancel_button_by_job_id: dict[int, QPushButton] = {}

        self.setWindowTitle("Operationen-Warteschlange")
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.resize(640, 360)

        self._setup_ui()
        self._connect_queue_signals()
        self._populate_existing_jobs()

    # ------------------------------------------------------------------
    # Aufbau
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        """Baut das Layout: Zusammenfassungszeile, Tabelle, Aktionsleiste."""
        layout = QVBoxLayout(self)

        self._summary_label = QLabel(self)
        self._summary_label.setStyleSheet("color: #9a9da2;")
        layout.addWidget(self._summary_label)

        self._table = QTableWidget(0, 4, self)
        self._table.setHorizontalHeaderLabels(
            ["Operation", "Fortschritt", "Status", ""]
        )
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(_COLUMN_TITLE, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(_COLUMN_PROGRESS, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(_COLUMN_STATUS, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(_COLUMN_ACTION, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self._table)

        button_row = QHBoxLayout()
        self._clear_finished_button = QPushButton("Abgeschlossene entfernen", self)
        self._clear_finished_button.clicked.connect(self._on_clear_finished_clicked)
        button_row.addWidget(self._clear_finished_button)

        self._cancel_pending_button = QPushButton("Alle wartenden abbrechen", self)
        self._cancel_pending_button.clicked.connect(self._on_cancel_pending_clicked)
        button_row.addWidget(self._cancel_pending_button)

        button_row.addStretch(1)

        close_button = QPushButton("Schließen", self)
        close_button.clicked.connect(self.close)
        button_row.addWidget(close_button)

        layout.addLayout(button_row)

    def _connect_queue_signals(self) -> None:
        """Verbindet die Signale des OperationQueueManager mit dieser Ansicht."""
        self._queue_manager.job_added.connect(self._on_job_added)
        self._queue_manager.job_progress_changed.connect(self._on_job_progress_changed)
        self._queue_manager.job_state_changed.connect(self._on_job_state_changed)
        self._queue_manager.job_finished.connect(self._on_job_finished)

    def _populate_existing_jobs(self) -> None:
        """Baut beim Öffnen des Fensters Zeilen für bereits vorhandene Einträge.

        Notwendig, da die Warteschlange (und damit ggf. bereits
        laufende Operationen) unabhängig von diesem Fenster existiert
        – der Nutzer kann das Fenster jederzeit schließen und später
        wieder öffnen, ohne dass laufende Operationen verlorengehen.
        """
        for job in self._queue_manager.jobs():
            self._on_job_added(job.job_id, job.title)
            if job.total:
                self._on_job_progress_changed(job.job_id, job.done, job.total, job.current_item)
            self._on_job_state_changed(job.job_id, job.state.value)
            if job.result is not None:
                self._on_job_finished(job.job_id, job.result)

    # ------------------------------------------------------------------
    # Slots: Warteschlangen-Signale
    # ------------------------------------------------------------------

    def _on_job_added(self, job_id: int, title: str) -> None:
        """Fügt eine neue Zeile für den angegebenen Eintrag hinzu."""
        if job_id in self._row_by_job_id:
            return

        row = self._table.rowCount()
        self._table.insertRow(row)
        self._row_by_job_id[job_id] = row

        title_item = QTableWidgetItem(title)
        self._table.setItem(row, _COLUMN_TITLE, title_item)

        progress_bar = QProgressBar(self._table)
        progress_bar.setRange(0, 100)
        progress_bar.setValue(0)
        progress_bar.setTextVisible(True)
        self._table.setCellWidget(row, _COLUMN_PROGRESS, progress_bar)
        self._progress_bar_by_job_id[job_id] = progress_bar

        status_label = QLabel(_STATUS_TEXT[QueueItemState.QUEUED], self._table)
        self._table.setCellWidget(row, _COLUMN_STATUS, status_label)
        self._status_label_by_job_id[job_id] = status_label

        cancel_button = QPushButton("Abbrechen", self._table)
        cancel_button.clicked.connect(lambda: self._queue_manager.cancel(job_id))
        self._table.setCellWidget(row, _COLUMN_ACTION, cancel_button)
        self._cancel_button_by_job_id[job_id] = cancel_button

        self._update_summary_label()

    def _on_job_progress_changed(self, job_id: int, done: int, total: int, current_item: str) -> None:
        """Aktualisiert die Fortschrittsanzeige einer laufenden Zeile."""
        progress_bar = self._progress_bar_by_job_id.get(job_id)
        if progress_bar is None:
            return
        percent = int((done / total) * 100) if total > 0 else 0
        progress_bar.setValue(min(percent, 100))
        progress_bar.setFormat(f"{done} / {total} – %p%")
        progress_bar.setToolTip(current_item)

    def _on_job_state_changed(self, job_id: int, state_value: str) -> None:
        """Aktualisiert die Statusspalte und den Abbrechen-Button einer Zeile."""
        status_label = self._status_label_by_job_id.get(job_id)
        if status_label is None:
            return
        state = QueueItemState(state_value)
        status_label.setText(_STATUS_TEXT[state])

        cancel_button = self._cancel_button_by_job_id.get(job_id)
        if cancel_button is not None:
            cancel_button.setEnabled(state in (QueueItemState.QUEUED, QueueItemState.RUNNING))

        self._update_summary_label()

    def _on_job_finished(self, job_id: int, result: object) -> None:
        """Zeigt nach Abschluss eines Eintrags dessen Zusammenfassung an."""
        assert isinstance(result, OperationResult)
        status_label = self._status_label_by_job_id.get(job_id)
        if status_label is None:
            return

        summary = result.summary_text().capitalize()
        status_label.setText(summary)
        if result.has_errors:
            status_label.setStyleSheet("color: #e5484d;")
        elif result.cancelled:
            status_label.setStyleSheet("color: #9a9da2;")
        else:
            status_label.setStyleSheet("color: #3fb950;")

        progress_bar = self._progress_bar_by_job_id.get(job_id)
        if progress_bar is not None and not result.cancelled:
            progress_bar.setValue(100)

        self._update_summary_label()

    # ------------------------------------------------------------------
    # Slots: Bedienelemente
    # ------------------------------------------------------------------

    def _on_clear_finished_clicked(self) -> None:
        """Entfernt alle abgeschlossenen/abgebrochenen Zeilen aus der Tabelle."""
        removable_job_ids = [
            job_id
            for job_id, label in self._status_label_by_job_id.items()
            if label.text() != _STATUS_TEXT[QueueItemState.QUEUED]
            and label.text() != _STATUS_TEXT[QueueItemState.RUNNING]
        ]
        for job_id in sorted(removable_job_ids, key=lambda jid: self._row_by_job_id[jid], reverse=True):
            row = self._row_by_job_id.pop(job_id)
            self._table.removeRow(row)
            self._progress_bar_by_job_id.pop(job_id, None)
            self._status_label_by_job_id.pop(job_id, None)
            self._cancel_button_by_job_id.pop(job_id, None)
            self._shift_rows_after_removal(row)

        self._queue_manager.clear_finished()
        self._update_summary_label()

    def _shift_rows_after_removal(self, removed_row: int) -> None:
        """Korrigiert die gespeicherten Zeilenindizes nach dem Entfernen einer Zeile."""
        for job_id, row in list(self._row_by_job_id.items()):
            if row > removed_row:
                self._row_by_job_id[job_id] = row - 1

    def _on_cancel_pending_clicked(self) -> None:
        """Bricht alle noch nicht gestarteten Einträge ab."""
        self._queue_manager.cancel_all_pending()

    def _update_summary_label(self) -> None:
        """Aktualisiert die Zusammenfassungszeile (aktiv/wartend)."""
        active = self._queue_manager.active_count()
        pending = self._queue_manager.pending_count()
        self._summary_label.setText(
            f"{active} aktiv, {pending} wartend – max. {self._queue_manager.max_concurrent} gleichzeitig"
        )

    # ------------------------------------------------------------------
    # Schließen: nur verstecken, Warteschlange läuft unbeeinflusst weiter
    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:  # noqa: ANN001, N802
        """Versteckt das Fenster statt es zu zerstören.

        Die Warteschlange gehört dem MainWindow, nicht diesem Dialog
        – laufende oder wartende Operationen werden durch das
        Schließen dieses Fensters in keiner Weise beeinflusst.
        """
        event.ignore()
        self.hide()
