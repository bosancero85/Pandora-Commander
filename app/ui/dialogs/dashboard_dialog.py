"""Pandora® Commander – Dashboard mit Systeminformationen.

Nicht-modaler Dialog, der laufend aktualisierte Kennzahlen zur
Anwendung und zum System anzeigt: App-Version und Laufzeit,
Betriebssystem, Python-Version, CPU-Kerne, Arbeitsspeicherbelegung
sowie die Datenträgerbelegung der aktuell in beiden Panels
angezeigten Verzeichnisse. Ersetzt das bisherige, optionale
Beispiel-Plugin durch eine fest in die Anwendung eingebaute Funktion
(erreichbar über Extras -> Dashboard).

Der Dialog ist bewusst nicht-modal (``show()`` statt ``exec()``),
damit während der Beobachtung normal weitergearbeitet werden kann;
die Aktualisierung läuft über einen QTimer und stoppt automatisch,
sobald der Dialog geschlossen wird.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import (
    QDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QLabel,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from app.core.filesystem.file_model import format_size
from app.core.logging_setup import get_logger
from app.core.system_info import (
    get_cpu_count,
    get_disk_info,
    get_memory_info,
    get_os_info,
    get_python_info,
)

logger = get_logger(__name__)

#: Wie oft die angezeigten Werte aktualisiert werden, in Millisekunden.
_REFRESH_INTERVAL_MS = 2000


class DashboardDialog(QDialog):
    """Zeigt ein sich automatisch aktualisierendes System-Dashboard an.

    Args:
        app_name: Anzeigename der Anwendung (für die Kopfzeile).
        app_version: Aktuell installierte Version.
        uptime_provider: Aufrufbares Objekt ohne Argumente, das die
            bisherige Laufzeit der Anwendung als formatierten String
            liefert (siehe MainWindow._format_uptime).
        watched_paths_provider: Aufrufbares Objekt ohne Argumente, das
            die aktuell zu beobachtenden Pfade liefert (typischerweise
            die aktuellen Verzeichnisse beider Panels). Wird bei jeder
            Aktualisierung neu abgefragt, damit das Dashboard auch bei
            geöffnetem Dialog der Panel-Navigation folgt.
        plugin_count_provider: Aufrufbares Objekt ohne Argumente, das
            ein (geladen, gesamt)-Tupel für die Plugin-Anzahl liefert.
        parent: Optionales Eltern-Widget.
    """

    def __init__(
        self,
        app_name: str,
        app_version: str,
        uptime_provider,
        watched_paths_provider,
        plugin_count_provider,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._uptime_provider = uptime_provider
        self._watched_paths_provider = watched_paths_provider
        self._plugin_count_provider = plugin_count_provider

        self.setWindowTitle("Dashboard")
        self.setMinimumWidth(420)

        self._app_label = QLabel()
        self._uptime_label = QLabel()
        self._plugin_label = QLabel()
        self._os_label = QLabel(get_os_info())
        self._python_label = QLabel(get_python_info())
        self._cpu_label = QLabel(str(get_cpu_count()))
        self._memory_bar = QProgressBar()
        self._memory_label = QLabel()
        self._disks_layout = QVBoxLayout()

        self._build_ui(app_name, app_version)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(_REFRESH_INTERVAL_MS)
        self._refresh()

    # ------------------------------------------------------------------
    # Aufbau
    # ------------------------------------------------------------------

    def _build_ui(self, app_name: str, app_version: str) -> None:
        layout = QVBoxLayout(self)

        app_group = QGroupBox("Anwendung", self)
        app_form = QFormLayout(app_group)
        self._app_label.setText(f"{app_name} {app_version}")
        app_form.addRow("Version:", self._app_label)
        app_form.addRow("Laufzeit:", self._uptime_label)
        app_form.addRow("Plugins:", self._plugin_label)
        layout.addWidget(app_group)

        system_group = QGroupBox("System", self)
        system_form = QFormLayout(system_group)
        system_form.addRow("Betriebssystem:", self._os_label)
        system_form.addRow("Python:", self._python_label)
        system_form.addRow("CPU-Kerne:", self._cpu_label)
        system_form.addRow("Arbeitsspeicher:", self._memory_bar)
        system_form.addRow("", self._memory_label)
        layout.addWidget(system_group)

        storage_group = QGroupBox("Datenträger", self)
        storage_group.setLayout(self._disks_layout)
        layout.addWidget(storage_group)

        layout.addStretch(1)

    # ------------------------------------------------------------------
    # Aktualisierung
    # ------------------------------------------------------------------

    def _refresh(self) -> None:
        """Fragt alle Provider erneut ab und aktualisiert die Anzeige."""
        try:
            self._uptime_label.setText(self._uptime_provider())

            loaded, total = self._plugin_count_provider()
            self._plugin_label.setText(f"{loaded} von {total} geladen")

            memory = get_memory_info()
            if memory.total > 0:
                self._memory_bar.setMaximum(100)
                self._memory_bar.setValue(round(memory.percent_used))
                self._memory_bar.setFormat(f"{memory.percent_used:.0f}%")
                self._memory_label.setText(
                    f"{format_size(memory.used)} von {format_size(memory.total)} belegt"
                )
            else:
                self._memory_bar.setValue(0)
                self._memory_bar.setFormat("nicht verfügbar")
                self._memory_label.setText("Konnte auf dieser Plattform nicht ermittelt werden.")

            self._refresh_disks()
        except Exception:  # noqa: BLE001 - Dashboard darf nie abstürzen
            logger.exception("Dashboard-Aktualisierung fehlgeschlagen.")

    def _refresh_disks(self) -> None:
        """Baut die Liste der Datenträger-Fortschrittsbalken neu auf.

        Wird bei jeder Aktualisierung komplett neu erzeugt, da sich
        die beobachteten Pfade (Panel-Navigation) zwischenzeitlich
        geändert haben können.
        """
        while self._disks_layout.count():
            item = self._disks_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        seen: set[Path] = set()
        for path in self._watched_paths_provider():
            if path in seen:
                continue
            seen.add(path)

            info = get_disk_info(path)
            row = QFrame(self)
            row_layout = QFormLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 8)

            if info is None:
                row_layout.addRow(str(path), QLabel("nicht verfügbar"))
            else:
                bar = QProgressBar(row)
                bar.setMaximum(100)
                bar.setValue(round(info.percent_used))
                bar.setFormat(f"{info.percent_used:.0f}%")
                row_layout.addRow(str(path), bar)
                row_layout.addRow(
                    "",
                    QLabel(f"{format_size(info.free)} frei von {format_size(info.total)}"),
                )
            self._disks_layout.addWidget(row)

    # ------------------------------------------------------------------
    # Aufräumen
    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:  # noqa: ANN001, N802
        """Stoppt den Aktualisierungs-Timer beim Schließen des Dialogs."""
        self._timer.stop()
        super().closeEvent(event)
