"""Pandora® Commander – Eigenschaften-Dialog.

Zeigt Name, Pfad, Typ, Größe, Änderungsdatum und Zugriffsrechte einer
Auswahl von Dateien/Ordnern an. Bei einzelnen Ordnern wird die
Gesamtgröße rekursiv im Hintergrund berechnet (PropertiesSizeWorker),
damit die Oberfläche bei großen Verzeichnisbäumen nicht einfriert –
mit Abbrechen-Möglichkeit, analog zu HashWorker/FileOperationWorker.

Bei genau einem ausgewählten Eintrag lässt sich zusätzlich das
"Schreibgeschützt"-Attribut (Owner-Schreibrecht) direkt umschalten.
"""

from __future__ import annotations

import stat
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.core.filesystem.file_model import format_size
from app.core.logging_setup import get_logger
from app.themes.dark_theme import PALETTE

logger = get_logger(__name__)


@dataclass(frozen=True)
class _FolderStats:
    """Ergebnis einer rekursiven Ordnerauswertung.

    Attributes:
        total_size: Summe aller Dateigrößen in Bytes.
        file_count: Anzahl gefundener Dateien.
        folder_count: Anzahl gefundener Unterordner.
        unreadable_count: Anzahl Einträge, die nicht gelesen werden konnten.
    """

    total_size: int
    file_count: int
    folder_count: int
    unreadable_count: int


class PropertiesSizeWorker(QThread):
    """Ermittelt die Gesamtgröße eines Ordners rekursiv im Hintergrund.

    Signals:
        progress_changed: Anzahl bisher gezählter Einträge (für eine
            unbestimmte Fortschrittsanzeige).
        finished_stats: Endergebnis als _FolderStats.
    """

    progress_changed = pyqtSignal(int)
    finished_stats = pyqtSignal(object)

    def __init__(self, root: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._root = root
        self._cancelled = False

    def cancel(self) -> None:
        """Fordert den Abbruch der laufenden Berechnung an."""
        self._cancelled = True

    def run(self) -> None:  # noqa: D102 - Qt-Override
        total_size = 0
        file_count = 0
        folder_count = 0
        unreadable_count = 0
        counted = 0

        try:
            iterator = self._root.rglob("*")
        except OSError:
            self.finished_stats.emit(_FolderStats(0, 0, 0, 1))
            return

        for entry in iterator:
            if self._cancelled:
                logger.info("Größenberechnung für '%s' abgebrochen", self._root)
                break
            try:
                if entry.is_dir():
                    folder_count += 1
                elif entry.is_file():
                    file_count += 1
                    total_size += entry.stat().st_size
            except OSError:
                unreadable_count += 1

            counted += 1
            if counted % 200 == 0:
                self.progress_changed.emit(counted)

        self.finished_stats.emit(
            _FolderStats(total_size, file_count, folder_count, unreadable_count)
        )


class PropertiesDialog(QDialog):
    """Eigenschaften-Dialog für eine Auswahl von Dateien/Ordnern.

    Args:
        paths: Ausgewählte Pfade (mindestens ein Eintrag).
        parent: Optionales Eltern-Widget.
    """

    def __init__(self, paths: list[Path], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        if not paths:
            raise ValueError("PropertiesDialog benötigt mindestens einen Pfad.")

        self._paths = paths
        self._single = len(paths) == 1
        self._size_worker: PropertiesSizeWorker | None = None
        self._pending_folder_paths: list[Path] = []
        self._aggregate_total_size = 0
        self._aggregate_file_count = 0
        self._aggregate_folder_count = 0

        title = paths[0].name if self._single else f"{len(paths)} Elemente"
        self.setWindowTitle(f"Eigenschaften – {title}")
        self.setMinimumWidth(460)

        self._name_label = QLabel(self)
        self._path_label = QLabel(self)
        self._path_label.setWordWrap(True)
        self._type_label = QLabel(self)
        self._size_label = QLabel(self)
        self._modified_label = QLabel(self)
        self._permissions_label = QLabel(self)

        self._progress_bar = QProgressBar(self)
        self._progress_bar.setRange(0, 0)  # unbestimmt, bis Ergebnis vorliegt
        self._progress_bar.setVisible(False)
        self._cancel_button = QPushButton("Abbrechen", self)
        self._cancel_button.setVisible(False)
        self._cancel_button.clicked.connect(self._cancel_size_calculation)

        self._readonly_checkbox = QCheckBox("Schreibgeschützt (Owner-Schreibrecht entfernen)", self)
        self._readonly_checkbox.setVisible(False)
        self._readonly_checkbox.toggled.connect(self._on_readonly_toggled)
        self._suppress_readonly_signal = False

        self._button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        self._button_box.rejected.connect(self.reject)
        self._button_box.accepted.connect(self.accept)
        self._button_box.button(QDialogButtonBox.StandardButton.Close).clicked.connect(self.accept)

        self._build_layout()
        self._populate()

    # ------------------------------------------------------------------
    # Aufbau
    # ------------------------------------------------------------------

    def _build_layout(self) -> None:
        form = QFormLayout()
        form.addRow("Name:", self._name_label)
        form.addRow("Pfad:", self._path_label)
        form.addRow("Typ:", self._type_label)
        form.addRow("Größe:", self._size_label)
        form.addRow("Geändert:", self._modified_label)
        form.addRow("Rechte:", self._permissions_label)

        progress_row = QHBoxLayout()
        progress_row.addWidget(self._progress_bar, 1)
        progress_row.addWidget(self._cancel_button)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addLayout(progress_row)
        layout.addWidget(self._readonly_checkbox)
        layout.addStretch(1)
        layout.addWidget(self._button_box)

        self.setStyleSheet(
            f"QDialog {{ background-color: {PALETTE.surface}; color: {PALETTE.text_primary}; }}"
            f"QLabel {{ color: {PALETTE.text_primary}; }}"
        )

    # ------------------------------------------------------------------
    # Befüllen
    # ------------------------------------------------------------------

    def _populate(self) -> None:
        if self._single:
            self._populate_single(self._paths[0])
        else:
            self._populate_multiple(self._paths)

    def _populate_single(self, path: Path) -> None:
        self._name_label.setText(path.name or str(path))
        self._path_label.setText(str(path))

        try:
            stat_result = path.stat()
        except OSError as error:
            self._type_label.setText("Nicht lesbar")
            self._size_label.setText("—")
            self._modified_label.setText("—")
            self._permissions_label.setText(str(error))
            return

        is_directory = path.is_dir()
        self._modified_label.setText(
            datetime.fromtimestamp(stat_result.st_mtime).strftime("%d.%m.%Y %H:%M:%S")
        )
        self._permissions_label.setText(stat.filemode(stat_result.st_mode))

        if is_directory:
            self._type_label.setText("Ordner")
            self._size_label.setText("Wird berechnet …")
            self._start_size_calculation(path)
        else:
            self._type_label.setText("Datei" + (f" (.{path.suffix.lstrip('.')})" if path.suffix else ""))
            self._size_label.setText(format_size(stat_result.st_size))

        # Schreibschutz-Checkbox nur sinnvoll bei genau einem Eintrag.
        owner_writable = bool(stat_result.st_mode & stat.S_IWUSR)
        self._suppress_readonly_signal = True
        self._readonly_checkbox.setChecked(not owner_writable)
        self._suppress_readonly_signal = False
        self._readonly_checkbox.setVisible(True)

    def _populate_multiple(self, paths: list[Path]) -> None:
        self._name_label.setText(f"{len(paths)} Elemente ausgewählt")
        self._path_label.setText(str(paths[0].parent))
        self._permissions_label.setText("—")
        self._modified_label.setText("—")
        self._type_label.setText("Gemischte Auswahl")
        self._size_label.setText("Wird berechnet …")

        self._aggregate_total_size = 0
        self._aggregate_file_count = 0
        self._aggregate_folder_count = 0
        self._pending_folder_paths = [p for p in paths if p.is_dir()]
        for entry in paths:
            if entry.is_file():
                try:
                    self._aggregate_total_size += entry.stat().st_size
                    self._aggregate_file_count += 1
                except OSError:
                    pass

        if not self._pending_folder_paths:
            self._finish_multi_selection_display()
        else:
            self._progress_bar.setVisible(True)
            self._cancel_button.setVisible(True)
            self._process_next_pending_folder()

    def _process_next_pending_folder(self) -> None:
        if not self._pending_folder_paths:
            self._finish_multi_selection_display()
            return

        folder = self._pending_folder_paths.pop(0)
        self._aggregate_folder_count += 1
        self._size_worker = PropertiesSizeWorker(folder, self)
        self._size_worker.finished_stats.connect(self._on_multi_folder_finished)
        self._size_worker.start()

    def _on_multi_folder_finished(self, stats: _FolderStats) -> None:
        self._aggregate_total_size += stats.total_size
        self._aggregate_file_count += stats.file_count
        self._aggregate_folder_count += stats.folder_count
        self._process_next_pending_folder()

    def _finish_multi_selection_display(self) -> None:
        self._progress_bar.setVisible(False)
        self._cancel_button.setVisible(False)
        self._size_label.setText(format_size(self._aggregate_total_size))
        self._type_label.setText(
            f"{self._aggregate_file_count} Datei(en), {self._aggregate_folder_count} Ordner"
        )

    # ------------------------------------------------------------------
    # Hintergrundberechnung (Einzelordner)
    # ------------------------------------------------------------------

    def _start_size_calculation(self, path: Path) -> None:
        self._progress_bar.setVisible(True)
        self._cancel_button.setVisible(True)
        self._size_worker = PropertiesSizeWorker(path, self)
        self._size_worker.finished_stats.connect(self._on_single_folder_finished)
        self._size_worker.start()

    def _on_single_folder_finished(self, stats: _FolderStats) -> None:
        self._progress_bar.setVisible(False)
        self._cancel_button.setVisible(False)
        self._size_label.setText(format_size(stats.total_size))
        suffix = f" ({stats.unreadable_count} nicht lesbar)" if stats.unreadable_count else ""
        self._type_label.setText(
            f"Ordner ({stats.file_count} Datei(en), {stats.folder_count} Unterordner){suffix}"
        )

    def _cancel_size_calculation(self) -> None:
        if self._size_worker is not None and self._size_worker.isRunning():
            self._size_worker.cancel()
        self._progress_bar.setVisible(False)
        self._cancel_button.setVisible(False)
        self._size_label.setText("Abgebrochen.")

    # ------------------------------------------------------------------
    # Schreibschutz umschalten
    # ------------------------------------------------------------------

    def _on_readonly_toggled(self, checked: bool) -> None:
        if self._suppress_readonly_signal or not self._single:
            return

        path = self._paths[0]
        try:
            current_mode = path.stat().st_mode
            if checked:
                new_mode = current_mode & ~stat.S_IWUSR
            else:
                new_mode = current_mode | stat.S_IWUSR
            path.chmod(new_mode)
            self._permissions_label.setText(stat.filemode(path.stat().st_mode))
        except OSError as error:
            QMessageBox.warning(
                self, "Schreibgeschützt", f"Berechtigung konnte nicht geändert werden:\n{error}"
            )
            self._suppress_readonly_signal = True
            self._readonly_checkbox.setChecked(not checked)
            self._suppress_readonly_signal = False

    # ------------------------------------------------------------------
    # Aufräumen
    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:  # noqa: D102 - Qt-Override
        if self._size_worker is not None and self._size_worker.isRunning():
            self._size_worker.cancel()
            self._size_worker.wait(2000)
        super().closeEvent(event)
