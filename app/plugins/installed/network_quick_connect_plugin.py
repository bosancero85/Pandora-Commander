"""Pandora® Commander – Plugin: Netzwerk-Panel-Erweiterung (SFTP/FTP-Schnellzugriff).

Der Kern von Pandora Commander verwaltet bereits Verbindungsprofile
(FTP/FTPS/SFTP/SMB/WebDAV) über den "Verbindungsmanager …"-Dialog im
Netzwerk-Menü – dort lassen sich Profile anlegen und die Erreichbarkeit
testen. Eine direkte, durchsuchbare Anzeige der Remote-Inhalte *in*
einem FilePanel ist laut Code-Kommentar im Hauptfenster bewusst als
"folgt in einer eigenen Erweiterung" vorgesehen. Dieses Plugin liefert
genau das nach, ohne den Core anzufassen:

    * Ein Untermenü "Schnellverbindung" im Plugins-Menü listet alle
      gespeicherten Verbindungsprofile (FTP/FTPS/SFTP) auf – ein Klick
      verbindet sofort, ohne den Verbindungsmanager-Dialog zu öffnen.
    * Nach dem Verbinden öffnet sich ein eigenständiger
      Remote-Browser-Dialog: Verzeichnisliste mit Navigation
      (Doppelklick auf Ordner, "..", Breadcrumb-Pfadfeld), sowie
      "Herunterladen" (markierte Remote-Dateien in das aktuelle
      Verzeichnis des aktiven lokalen Panels) und "Hochladen"
      (markierte lokale Dateien aus dem aktiven Panel in das aktuelle
      Remote-Verzeichnis).

SMB und WebDAV werden hier bewusst ausgeklammert, da der Core dafür
bislang keine ``list_dir``/``download_file``/``upload_file``-Clients
mit demselben Interface wie FTP/SFTP bereitstellt; das Untermenü
zeigt solche Profile daher deaktiviert mit entsprechendem Hinweis an.

Up-/Downloads laufen jeweils in einem Hintergrund-Thread, damit die
Oberfläche bei größeren Übertragungen nicht einfriert.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.core.filesystem.file_model import format_size
from app.core.logging_setup import get_logger
from app.core.network.connection_manager import ConnectionManager, ConnectionProfile, ConnectionType
from app.plugins.plugin_manager import PandoraPlugin

logger = get_logger(__name__)

_BROWSABLE_TYPES = (ConnectionType.FTP, ConnectionType.FTPS, ConnectionType.SFTP)
_COLUMN_NAME = 0
_COLUMN_SIZE = 1


class _TransferWorker(QThread):
    """Führt eine Liste von Download- oder Upload-Operationen im Hintergrund aus."""

    item_finished = pyqtSignal(str, bool, str)  # Name, Erfolg, Fehlermeldung
    all_finished = pyqtSignal(int, int)

    def __init__(self, operations: list[tuple[str, Any]]) -> None:
        """``operations``: Liste von (Anzeigename, Callable ohne Argumente)."""
        super().__init__()
        self._operations = operations

    def run(self) -> None:  # noqa: D102 - QThread-Standardmethode
        success_count = 0
        for display_name, action in self._operations:
            try:
                action()
                success_count += 1
                self.item_finished.emit(display_name, True, "")
            except Exception as error:  # noqa: BLE001 - Client-spezifische Exceptions
                self.item_finished.emit(display_name, False, str(error))
        self.all_finished.emit(success_count, len(self._operations))


class RemoteBrowserDialog(QDialog):
    """Einfacher Remote-Dateibrowser für eine bestehende FTP/SFTP-Verbindung."""

    def __init__(self, profile: ConnectionProfile, client: Any, local_panel: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Pandora® Commander – Verbunden mit '{profile.name}'")
        self.resize(720, 520)

        self._profile = profile
        self._client = client
        self._local_panel = local_panel
        self._current_path = profile.remote_path or "/"
        self._active_workers: list[_TransferWorker] = []

        self._path_edit = QLineEdit(self._current_path)
        self._path_edit.returnPressed.connect(self._on_path_entered)
        go_button = QPushButton("Wechseln")
        go_button.clicked.connect(self._on_path_entered)
        up_button = QPushButton("⬆ Ebene hoch")
        up_button.clicked.connect(self._on_go_up)

        path_row = QHBoxLayout()
        path_row.addWidget(QLabel("Pfad:"))
        path_row.addWidget(self._path_edit, stretch=1)
        path_row.addWidget(go_button)
        path_row.addWidget(up_button)

        self._table = QTableWidget(0, 2)
        self._table.setHorizontalHeaderLabels(["Name", "Größe"])
        self._table.setColumnWidth(0, 420)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.cellDoubleClicked.connect(self._on_cell_double_clicked)

        self._download_button = QPushButton("⬇ Markierte herunterladen (in aktives lokales Panel)")
        self._download_button.clicked.connect(self._on_download_clicked)
        self._upload_button = QPushButton("⬆ Markierte lokale Dateien hochladen")
        self._upload_button.clicked.connect(self._on_upload_clicked)

        self._progress_bar = QProgressBar()
        self._progress_bar.setVisible(False)

        transfer_row = QHBoxLayout()
        transfer_row.addWidget(self._download_button)
        transfer_row.addWidget(self._upload_button)

        self._status_label = QLabel(f"Verbunden mit {profile.host} als '{profile.username or 'anonymous'}'.")

        disconnect_button = QPushButton("Trennen und schließen")
        disconnect_button.clicked.connect(self._on_disconnect_clicked)

        layout = QVBoxLayout(self)
        layout.addLayout(path_row)
        layout.addWidget(self._table, stretch=1)
        layout.addLayout(transfer_row)
        layout.addWidget(self._progress_bar)
        layout.addWidget(self._status_label)
        layout.addWidget(disconnect_button)

        self._refresh_listing()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt-Überschreibung
        self._disconnect_quietly()
        super().closeEvent(event)

    def _disconnect_quietly(self) -> None:
        try:
            self._client.disconnect()
        except Exception:  # noqa: BLE001
            pass

    def _on_disconnect_clicked(self) -> None:
        self._disconnect_quietly()
        self.close()

    def _refresh_listing(self) -> None:
        try:
            entries = self._client.list_dir(self._current_path)
        except Exception as error:  # noqa: BLE001 - Client-spezifische Exceptions
            QMessageBox.critical(self, "Fehler", f"Verzeichnis konnte nicht gelesen werden: {error}")
            return

        self._path_edit.setText(self._current_path)
        sorted_entries = sorted(entries, key=lambda entry: (not entry.is_dir, entry.name.lower()))
        self._table.setRowCount(len(sorted_entries))
        for row, entry in enumerate(sorted_entries):
            name_item = QTableWidgetItem(("📁 " if entry.is_dir else "📄 ") + entry.name)
            name_item.setData(1000, entry.is_dir)
            name_item.setData(1001, entry.name)
            size_text = "" if entry.is_dir or entry.size_bytes < 0 else format_size(entry.size_bytes)
            size_item = QTableWidgetItem(size_text)
            self._table.setItem(row, _COLUMN_NAME, name_item)
            self._table.setItem(row, _COLUMN_SIZE, size_item)

    def _on_path_entered(self) -> None:
        self._current_path = self._path_edit.text() or "/"
        self._refresh_listing()

    def _on_go_up(self) -> None:
        if self._current_path in ("/", ""):
            return
        parent = self._current_path.rstrip("/").rsplit("/", 1)[0]
        self._current_path = parent or "/"
        self._refresh_listing()

    def _on_cell_double_clicked(self, row: int, _column: int) -> None:
        name_item = self._table.item(row, _COLUMN_NAME)
        if name_item is None:
            return
        is_dir = name_item.data(1000)
        entry_name = name_item.data(1001)
        if is_dir:
            separator = "" if self._current_path.endswith("/") else "/"
            self._current_path = f"{self._current_path}{separator}{entry_name}"
            self._refresh_listing()

    def _selected_remote_entries(self) -> list[tuple[str, bool]]:
        rows = sorted({index.row() for index in self._table.selectionModel().selectedRows()})
        result = []
        for row in rows:
            item = self._table.item(row, _COLUMN_NAME)
            if item is not None:
                result.append((item.data(1001), item.data(1000)))
        return result

    def _on_download_clicked(self) -> None:
        selected = [(name, is_dir) for name, is_dir in self._selected_remote_entries() if not is_dir]
        if not selected:
            QMessageBox.information(self, "Keine Auswahl", "Bitte mindestens eine Remote-Datei markieren.")
            return

        local_directory = getattr(self._local_panel, "current_directory", None) or Path.home()
        separator = "" if self._current_path.endswith("/") else "/"

        operations = []
        for name, _is_dir in selected:
            remote_path = f"{self._current_path}{separator}{name}"
            local_path = local_directory / name
            operations.append((name, lambda r=remote_path, l=local_path: self._client.download_file(r, l)))

        self._run_transfer(operations, "heruntergeladen")

    def _on_upload_clicked(self) -> None:
        local_paths = getattr(self._local_panel, "selected_paths", lambda: [])()
        local_files = [path for path in local_paths if path.is_file()]
        if not local_files:
            QMessageBox.information(
                self, "Keine Auswahl", "Bitte im aktiven lokalen Panel mindestens eine Datei markieren."
            )
            return

        separator = "" if self._current_path.endswith("/") else "/"
        operations = []
        for local_path in local_files:
            remote_path = f"{self._current_path}{separator}{local_path.name}"
            operations.append(
                (local_path.name, lambda l=local_path, r=remote_path: self._client.upload_file(l, r))
            )

        self._run_transfer(operations, "hochgeladen")

    def _run_transfer(self, operations: list[tuple[str, Any]], action_label: str) -> None:
        self._progress_bar.setVisible(True)
        self._progress_bar.setRange(0, len(operations))
        self._progress_bar.setValue(0)
        self._download_button.setEnabled(False)
        self._upload_button.setEnabled(False)

        self._failures: list[tuple[str, str]] = []
        worker = _TransferWorker(operations)
        worker.item_finished.connect(self._on_item_finished)
        worker.all_finished.connect(
            lambda success, total, label=action_label: self._on_transfer_finished(success, total, label)
        )
        worker.finished.connect(
            lambda w=worker: self._active_workers.remove(w) if w in self._active_workers else None
        )
        self._active_workers.append(worker)
        worker.start()

    def _on_item_finished(self, name: str, success: bool, message: str) -> None:
        self._progress_bar.setValue(self._progress_bar.value() + 1)
        if not success:
            self._failures.append((name, message))

    def _on_transfer_finished(self, success_count: int, total: int, action_label: str) -> None:
        self._progress_bar.setVisible(False)
        self._download_button.setEnabled(True)
        self._upload_button.setEnabled(True)

        if hasattr(self._local_panel, "refresh"):
            self._local_panel.refresh()
        if action_label == "hochgeladen":
            self._refresh_listing()

        if success_count == total:
            self._status_label.setText(f"{success_count} Datei(en) erfolgreich {action_label}.")
        else:
            error_text = "; ".join(f"{name}: {message}" for name, message in self._failures[:5])
            self._status_label.setText(
                f"{success_count} von {total} Datei(en) {action_label} – Fehler: {error_text}"
            )


class NetworkQuickConnectPlugin(PandoraPlugin):
    """Plugin für Schnellverbindungen zu gespeicherten FTP/SFTP-Profilen mit Remote-Browser."""

    name = "Netzwerk-Schnellverbindung"
    version = "1.0"
    author = "AKI_SystemDown®"
    description = (
        "Fügt dem Plugins-Menü ein Untermenü 'Schnellverbindung' mit allen gespeicherten "
        "FTP/FTPS/SFTP-Profilen hinzu und öffnet nach dem Verbinden einen Remote-Browser "
        "zum Herunterladen/Hochladen in bzw. aus dem aktiven lokalen Panel."
    )

    def __init__(self) -> None:
        self._context: dict[str, Any] = {}
        self._open_dialogs: list[RemoteBrowserDialog] = []

    def on_load(self, context: dict[str, Any]) -> None:
        self._context = context
        logger.info("%s geladen.", self.name)

    def on_unload(self) -> None:
        for dialog in self._open_dialogs:
            dialog.close()
        self._open_dialogs.clear()

    def register_menu_actions(self, context: dict[str, Any]) -> list[QAction]:
        main_window = context.get("main_window")
        connection_manager: ConnectionManager | None = context.get("connection_manager")

        submenu = QMenu("Schnellverbindung", main_window)
        if connection_manager is None or not connection_manager.profiles:
            placeholder = submenu.addAction("(keine gespeicherten Profile)")
            placeholder.setEnabled(False)
        else:
            for profile in connection_manager.profiles:
                action = QAction(f"{profile.name} ({profile.connection_type.value.upper()})", main_window)
                action.setEnabled(profile.connection_type in _BROWSABLE_TYPES)
                if profile.connection_type not in _BROWSABLE_TYPES:
                    action.setToolTip("SMB/WebDAV werden von der Schnellverbindung derzeit nicht unterstützt.")
                action.triggered.connect(
                    lambda checked=False, p=profile: self._connect_and_browse(p)
                )
                submenu.addAction(action)

        return [submenu.menuAction()]

    def _connect_and_browse(self, profile: ConnectionProfile) -> None:
        main_window = self._context.get("main_window")
        connection_manager: ConnectionManager = self._context.get("connection_manager")
        left_panel = self._context.get("left_panel")

        client = connection_manager.create_client(profile)
        try:
            client.connect()
        except Exception as error:  # noqa: BLE001 - Client-spezifische Exceptions
            QMessageBox.critical(main_window, "Verbindung fehlgeschlagen", f"'{profile.name}':\n{error}")
            return

        dialog = RemoteBrowserDialog(profile, client, left_panel, parent=main_window)
        dialog.destroyed.connect(
            lambda: self._open_dialogs.remove(dialog) if dialog in self._open_dialogs else None
        )
        self._open_dialogs.append(dialog)
        dialog.show()
