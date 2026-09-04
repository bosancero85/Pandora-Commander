"""Pandora® Commander – Warteschlange für Datei-Hintergrundoperationen.

Erlaubt es, mehrere Kopier-/Verschiebe-/Löschvorgänge nacheinander
oder auch mehrere davon gleichzeitig im Hintergrund laufen zu lassen,
anstatt jede Operation einzeln in einem modalen Dialog zu blockieren.

Der OperationQueueManager verwaltet dazu eine Liste von
QueuedOperation-Einträgen. Jeder Eintrag kapselt einen bereits
konfigurierten, aber noch nicht gestarteten FileOperationWorker.
Neu hinzugefügte Operationen werden sofort gestartet, solange die
konfigurierbare Obergrenze gleichzeitig laufender Operationen
(Settings.max_concurrent_operations) noch nicht erreicht ist;
andernfalls verbleiben sie in der Warteliste, bis ein Platz frei wird.

Verwendung (aus main_window.py heraus):

    queue_manager = OperationQueueManager(
        max_concurrent=settings.max_concurrent_operations
    )
    queue_manager.job_added.connect(...)
    queue_manager.job_progress_changed.connect(...)
    queue_manager.job_state_changed.connect(...)
    queue_manager.job_finished.connect(...)

    worker = FileOperationWorker(
        operation=OperationType.COPY, sources=[...], destination=...
    )
    job_id = queue_manager.enqueue(title="Kopiere 3 Element(e)", worker=worker)
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from enum import Enum

from PyQt6.QtCore import QObject, pyqtSignal

from app.core.filesystem.file_operations import FileOperationWorker, OperationResult
from app.core.logging_setup import get_logger

logger = get_logger(__name__)

#: Prozessweiter, fortlaufender Zähler für eindeutige Job-IDs. Bewusst
#: modulglobal statt Instanzattribut, damit IDs auch über mehrere
#: OperationQueueManager-Instanzen hinweg (z. B. in Tests) eindeutig
#: bleiben.
_id_counter = itertools.count(1)


class QueueItemState(str, Enum):
    """Zustand eines einzelnen Warteschlangeneintrags."""

    QUEUED = "queued"
    RUNNING = "running"
    FINISHED = "finished"
    CANCELLED = "cancelled"


@dataclass
class QueuedOperation:
    """Ein einzelner Eintrag der Operations-Warteschlange.

    Attributes:
        job_id: Fortlaufende, innerhalb des Programmlaufs eindeutige ID.
        title: Für die Anzeige aufbereiteter Titel (z. B. "Kopiere 3
            Element(e)").
        worker: Der zugrunde liegende, bereits konfigurierte
            FileOperationWorker.
        state: Aktueller Zustand des Eintrags.
        done: Anzahl bisher verarbeiteter Elemente (Fortschrittsanzeige).
        total: Gesamtanzahl zu verarbeitender Elemente.
        current_item: Name des aktuell verarbeiteten Elements.
        result: Ergebnis, sobald die Operation abgeschlossen ist.
    """

    job_id: int
    title: str
    worker: FileOperationWorker
    state: QueueItemState = QueueItemState.QUEUED
    done: int = 0
    total: int = 0
    current_item: str = ""
    result: OperationResult | None = None


class OperationQueueManager(QObject):
    """Verwaltet mehrere Datei-Hintergrundoperationen als Warteschlange.

    Startet neu hinzugefügte Operationen sofort, sofern die Obergrenze
    gleichzeitig laufender Operationen (max_concurrent) noch nicht
    erreicht ist. Andernfalls werden sie in der Reihenfolge ihres
    Eintreffens gestartet, sobald ein Platz frei wird. Alle
    Zustandsänderungen werden über Qt-Signale gemeldet, sodass eine
    nicht-modale UI-Komponente wie
    app.ui.dialogs.operations_queue_dialog beliebig viele Einträge
    gleichzeitig anzeigen kann, ohne die Hauptoberfläche zu blockieren.

    Signals:
        job_added: (job_id, title) – neuer Eintrag hinzugefügt
            (Zustand zu diesem Zeitpunkt QUEUED oder, falls sofort
            gestartet, bereits RUNNING).
        job_state_changed: (job_id, state) – Zustandswechsel eines
            Eintrags, state als str-Wert von QueueItemState.
        job_progress_changed: (job_id, done, total, current_item) –
            Fortschritt eines laufenden Eintrags.
        job_finished: (job_id, result) – ein Eintrag ist fertig
            (erfolgreich, mit Fehlern oder abgebrochen). result ist
            ein OperationResult.
        queue_idle: Gesendet, sobald keine Operation mehr läuft und
            die Warteliste leer ist.
    """

    job_added = pyqtSignal(int, str)
    job_state_changed = pyqtSignal(int, str)
    job_progress_changed = pyqtSignal(int, int, int, str)
    job_finished = pyqtSignal(int, object)
    queue_idle = pyqtSignal()

    def __init__(self, max_concurrent: int = 2, parent: QObject | None = None) -> None:
        """Initialisiert den Manager.

        Args:
            max_concurrent: Maximale Anzahl gleichzeitig laufender
                Operationen. Werte kleiner als 1 werden auf 1
                angehoben.
            parent: Optionales Eltern-QObject.
        """
        super().__init__(parent)
        self._max_concurrent = max(1, max_concurrent)
        self._jobs: dict[int, QueuedOperation] = {}
        self._pending: list[int] = []
        self._running: set[int] = set()

    # ------------------------------------------------------------------
    # Öffentliche API
    # ------------------------------------------------------------------

    @property
    def max_concurrent(self) -> int:
        """Aktuell konfigurierte Obergrenze gleichzeitig laufender Operationen."""
        return self._max_concurrent

    def set_max_concurrent(self, value: int) -> None:
        """Ändert die Obergrenze gleichzeitig laufender Operationen zur Laufzeit.

        Wird der Wert erhöht, werden sofort weitere wartende Einträge
        gestartet, sofern vorhanden.

        Args:
            value: Neue Obergrenze; Werte kleiner als 1 werden auf 1
                angehoben.
        """
        self._max_concurrent = max(1, value)
        self._start_pending_if_possible()

    def active_count(self) -> int:
        """Anzahl aktuell laufender Operationen."""
        return len(self._running)

    def pending_count(self) -> int:
        """Anzahl noch wartender (nicht gestarteter) Operationen."""
        return len(self._pending)

    def jobs(self) -> list[QueuedOperation]:
        """Alle Einträge in der Reihenfolge ihres Hinzufügens."""
        return sorted(self._jobs.values(), key=lambda job: job.job_id)

    def enqueue(self, title: str, worker: FileOperationWorker) -> int:
        """Fügt eine neue Operation zur Warteschlange hinzu.

        Die Operation wird sofort gestartet, wenn gemäß
        max_concurrent noch ein Platz frei ist, andernfalls in die
        Warteliste eingereiht.

        Args:
            title: Für die Anzeige aufbereiteter Titel.
            worker: Bereits konfigurierter, noch nicht gestarteter
                FileOperationWorker.

        Returns:
            Die vergebene job_id, über die der Eintrag später
            identifiziert werden kann (z. B. für cancel()).
        """
        job_id = next(_id_counter)
        job = QueuedOperation(job_id=job_id, title=title, worker=worker)
        self._jobs[job_id] = job

        worker.progress_changed.connect(
            lambda done, total, name, jid=job_id: self._on_worker_progress(
                jid, done, total, name
            )
        )
        worker.operation_finished.connect(
            lambda result, jid=job_id: self._on_worker_finished(jid, result)
        )

        self._pending.append(job_id)
        self.job_added.emit(job_id, title)
        logger.info(
            "Operation zur Warteschlange hinzugefügt: %s (job_id=%d)", title, job_id
        )

        self._start_pending_if_possible()
        return job_id

    def cancel(self, job_id: int) -> None:
        """Bricht einen Eintrag ab.

        Läuft die Operation bereits, wird der Worker kooperativ zum
        Abbruch aufgefordert (meldet sich regulär über job_finished
        mit result.cancelled == True). Wartet der Eintrag noch in der
        Warteliste, wird er sofort entfernt, ohne je gestartet worden
        zu sein.

        Args:
            job_id: Eine über enqueue() erhaltene ID.
        """
        job = self._jobs.get(job_id)
        if job is None:
            return

        if job_id in self._pending:
            self._pending.remove(job_id)
            job.state = QueueItemState.CANCELLED
            self.job_state_changed.emit(job_id, job.state.value)
            self._maybe_emit_idle()
            return

        if job_id in self._running:
            job.worker.cancel()

    def cancel_all_pending(self) -> None:
        """Entfernt alle noch nicht gestarteten Einträge aus der Warteliste."""
        for job_id in list(self._pending):
            self.cancel(job_id)

    def clear_finished(self) -> None:
        """Entfernt abgeschlossene/abgebrochene Einträge aus der internen Liste.

        Rein aufräumend, für die UI gedacht (z. B. ein
        "Abgeschlossene entfernen"-Button). Laufende oder wartende
        Einträge bleiben unberührt.
        """
        finished_ids = [
            jid
            for jid, job in self._jobs.items()
            if job.state in (QueueItemState.FINISHED, QueueItemState.CANCELLED)
        ]
        for jid in finished_ids:
            del self._jobs[jid]

    # ------------------------------------------------------------------
    # Interna
    # ------------------------------------------------------------------

    def _start_pending_if_possible(self) -> None:
        """Startet so viele wartende Einträge, wie das Limit noch zulässt."""
        while self._pending and len(self._running) < self._max_concurrent:
            job_id = self._pending.pop(0)
            job = self._jobs[job_id]
            job.state = QueueItemState.RUNNING
            self._running.add(job_id)
            self.job_state_changed.emit(job_id, job.state.value)
            logger.info(
                "Starte Operation aus Warteschlange: %s (job_id=%d)", job.title, job_id
            )
            job.worker.start()

    def _on_worker_progress(
        self, job_id: int, done: int, total: int, current_item: str
    ) -> None:
        job = self._jobs.get(job_id)
        if job is None:
            return
        job.done = done
        job.total = total
        job.current_item = current_item
        self.job_progress_changed.emit(job_id, done, total, current_item)

    def _on_worker_finished(self, job_id: int, result: object) -> None:
        job = self._jobs.get(job_id)
        if job is None:
            return
        assert isinstance(result, OperationResult)
        job.result = result
        job.state = (
            QueueItemState.CANCELLED if result.cancelled else QueueItemState.FINISHED
        )
        self._running.discard(job_id)

        self.job_state_changed.emit(job_id, job.state.value)
        self.job_finished.emit(job_id, result)
        logger.info(
            "Operation aus Warteschlange abgeschlossen: %s (job_id=%d) – %s",
            job.title,
            job_id,
            result.summary_text(),
        )

        self._start_pending_if_possible()
        self._maybe_emit_idle()

    def _maybe_emit_idle(self) -> None:
        if not self._running and not self._pending:
            self.queue_idle.emit()
