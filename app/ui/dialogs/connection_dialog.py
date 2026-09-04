"""Pandora® Commander – Verbindungsmanager-Dialog.

Erlaubt das Anlegen, Bearbeiten, Löschen und Verbinden von
Netzwerkverbindungsprofilen (FTP/FTPS/SFTP/SMB/WebDAV). Der Dialog
selbst baut keine Verbindung auf – er liefert per Signal das gewählte
ConnectionProfile zurück, die eigentliche Verbindung wird von der
aufrufenden Stelle (main_window.py) über ConnectionManager.create_client()
hergestellt, damit blockierende Netzwerkaufrufe außerhalb des Dialogs
in einem Worker laufen können.
"""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from app.core.logging_setup import get_logger
from app.core.network.connection_manager import ConnectionManager, ConnectionProfile, ConnectionType

logger = get_logger(__name__)

_DEFAULT_PORTS = {
    ConnectionType.FTP: 21,
    ConnectionType.FTPS: 21,
    ConnectionType.SFTP: 22,
    ConnectionType.SMB: 445,
    ConnectionType.WEBDAV: 443,
}


class ConnectionDialog(QDialog):
    """Dialog zur Verwaltung gespeicherter Netzwerkverbindungen.

    Signals:
        connect_requested: Sendet das gewählte ConnectionProfile, wenn
            der Nutzer auf "Verbinden" klickt.

    Args:
        connection_manager: Zentraler ConnectionManager der Anwendung.
        parent: Optionales Eltern-Widget.
    """

    connect_requested = pyqtSignal(object)

    def __init__(self, connection_manager: ConnectionManager, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Verbindungsmanager")
        self.resize(560, 420)
        self._manager = connection_manager

        self._profile_list = QListWidget(self)
        self._profile_list.currentRowChanged.connect(self._on_selection_changed)

        self._name_edit = QLineEdit(self)
        self._type_combo = QComboBox(self)
        self._type_combo.addItems([t.value.upper() for t in ConnectionType])
        self._type_combo.currentIndexChanged.connect(self._on_type_changed)
        self._host_edit = QLineEdit(self)
        self._port_spin = QSpinBox(self)
        self._port_spin.setRange(1, 65535)
        self._username_edit = QLineEdit(self)
        self._password_edit = QLineEdit(self)
        self._password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._remote_path_edit = QLineEdit("/", self)
        self._share_edit = QLineEdit(self)
        self._key_path_edit = QLineEdit(self)
        key_browse_button = QPushButton("…", self)
        key_browse_button.setMaximumWidth(32)
        key_browse_button.clicked.connect(self._browse_key)

        new_button = QPushButton("Neu", self)
        new_button.clicked.connect(self._new_profile)
        save_button = QPushButton("Speichern", self)
        save_button.clicked.connect(self._save_profile)
        delete_button = QPushButton("Löschen", self)
        delete_button.clicked.connect(self._delete_profile)
        connect_button = QPushButton("Verbinden", self)
        connect_button.clicked.connect(self._connect)

        self._build_layout(key_browse_button, new_button, save_button, delete_button, connect_button)
        self._reload_list()
        self._on_type_changed()

    def _build_layout(self, key_browse_button, new_button, save_button, delete_button, connect_button) -> None:
        form = QFormLayout()
        form.addRow("Name:", self._name_edit)
        form.addRow("Typ:", self._type_combo)
        form.addRow("Host:", self._host_edit)
        form.addRow("Port:", self._port_spin)
        form.addRow("Benutzername:", self._username_edit)
        form.addRow("Passwort:", self._password_edit)
        form.addRow("Startverzeichnis:", self._remote_path_edit)
        form.addRow("Freigabe (SMB):", self._share_edit)

        key_row = QHBoxLayout()
        key_row.addWidget(self._key_path_edit)
        key_row.addWidget(key_browse_button)
        form.addRow("SSH-Schlüssel (SFTP):", key_row)

        button_row = QHBoxLayout()
        for button in (new_button, save_button, delete_button):
            button_row.addWidget(button)
        button_row.addStretch(1)
        button_row.addWidget(connect_button)

        main_row = QHBoxLayout()
        main_row.addWidget(self._profile_list, 1)
        form_widget_layout = QVBoxLayout()
        form_widget_layout.addLayout(form)
        main_row.addLayout(form_widget_layout, 2)

        layout = QVBoxLayout(self)
        layout.addLayout(main_row)
        layout.addLayout(button_row)

    def _reload_list(self) -> None:
        self._profile_list.clear()
        for profile in self._manager.profiles:
            item = QListWidgetItem(f"{profile.name} ({profile.connection_type.value.upper()})")
            item.setData(1000, profile.name)
            self._profile_list.addItem(item)

    def _on_type_changed(self) -> None:
        connection_type = ConnectionType(self._type_combo.currentText().lower())
        self._port_spin.setValue(_DEFAULT_PORTS.get(connection_type, 21))
        self._share_edit.setEnabled(connection_type == ConnectionType.SMB)
        self._key_path_edit.setEnabled(connection_type == ConnectionType.SFTP)

    def _on_selection_changed(self, row: int) -> None:
        if row < 0 or row >= len(self._manager.profiles):
            return
        profile = self._manager.profiles[row]
        self._name_edit.setText(profile.name)
        self._type_combo.setCurrentText(profile.connection_type.value.upper())
        self._host_edit.setText(profile.host)
        self._port_spin.setValue(profile.port)
        self._username_edit.setText(profile.username)
        self._password_edit.setText(profile.password)
        self._remote_path_edit.setText(profile.remote_path)
        self._share_edit.setText(profile.share)
        self._key_path_edit.setText(profile.key_path)

    def _browse_key(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(self, "SSH-Schlüssel wählen")
        if chosen:
            self._key_path_edit.setText(chosen)

    def _new_profile(self) -> None:
        self._profile_list.clearSelection()
        self._name_edit.clear()
        self._host_edit.clear()
        self._username_edit.clear()
        self._password_edit.clear()
        self._remote_path_edit.setText("/")
        self._share_edit.clear()
        self._key_path_edit.clear()

    def _build_profile_from_form(self) -> ConnectionProfile | None:
        name = self._name_edit.text().strip()
        host = self._host_edit.text().strip()
        if not name or not host:
            QMessageBox.warning(self, "Angaben unvollständig", "Name und Host sind erforderlich.")
            return None

        return ConnectionProfile(
            name=name,
            connection_type=ConnectionType(self._type_combo.currentText().lower()),
            host=host,
            port=self._port_spin.value(),
            username=self._username_edit.text(),
            password=self._password_edit.text(),
            remote_path=self._remote_path_edit.text() or "/",
            share=self._share_edit.text(),
            key_path=self._key_path_edit.text(),
        )

    def _save_profile(self) -> None:
        profile = self._build_profile_from_form()
        if profile is None:
            return
        self._manager.remove_profile(profile.name)
        self._manager.add_profile(profile)
        self._reload_list()

    def _delete_profile(self) -> None:
        current_item = self._profile_list.currentItem()
        if current_item is None:
            return
        name = current_item.data(1000)
        self._manager.remove_profile(name)
        self._reload_list()
        self._new_profile()

    def _connect(self) -> None:
        profile = self._build_profile_from_form()
        if profile is None:
            return
        self.connect_requested.emit(profile)
        self.accept()
