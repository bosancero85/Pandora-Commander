"""Pandora® Commander – Plugin: Datei-Verschlüsselung (AES-256-GCM).

Fügt dem Rechtsklick-Kontextmenü der Dateipanels die Einträge
"Verschlüsseln (AES-256) …" (für normale Dateien) und
"Entschlüsseln …" (für Dateien mit der Endung ``.penc``) hinzu.

Kryptografisches Format (eigenes, einfaches Container-Format):

    Offset  Länge   Inhalt
    0       11      Magic-Header b"PANDORAENC1"
    11      16      Salt für die Schlüsselableitung (zufällig, pro Datei neu)
    27      12      Nonce für AES-GCM (zufällig, pro Datei neu)
    39      -       Ciphertext inkl. angehängtem 16-Byte-Auth-Tag (AES-GCM)

Der Schlüssel wird aus dem vom Nutzer eingegebenen Passwort per
PBKDF2-HMAC-SHA256 mit 390.000 Iterationen und dem zufälligen
16-Byte-Salt abgeleitet (Empfehlung von OWASP, Stand 2023, für
PBKDF2-SHA256). AES-256-GCM liefert dabei sowohl Vertraulichkeit als
auch Integritätsschutz – eine manipulierte oder mit falschem
Passwort entschlüsselte Datei wird zuverlässig erkannt und
zurückgewiesen, statt stillschweigend Datenmüll zu erzeugen.

Verschlüsseln und Entschlüsseln laufen jeweils in einem eigenen
QThread, damit die Oberfläche bei größeren Dateien nicht einfriert.
Nach erfolgreichem Vorgang wird der Nutzer gefragt, ob die
Ausgangsdatei gelöscht werden soll; ohne explizite Bestätigung
bleibt sie unangetastet erhalten.

Abhängigkeit: Das Paket ``cryptography`` muss installiert sein
(``pip install cryptography``).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QDialog, QFormLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QVBoxLayout, QWidget

from app.core.logging_setup import get_logger
from app.plugins.plugin_manager import PandoraPlugin

logger = get_logger(__name__)

_MAGIC_HEADER = b"PANDORAENC1"
_SALT_LENGTH = 16
_NONCE_LENGTH = 12
_PBKDF2_ITERATIONS = 390_000
_ENCRYPTED_SUFFIX = ".penc"

try:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    _CRYPTOGRAPHY_AVAILABLE = True
except ImportError:  # pragma: no cover - abhängig von der Zielumgebung
    _CRYPTOGRAPHY_AVAILABLE = False


def _derive_key(password: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,  # 32 Byte = AES-256
        salt=salt,
        iterations=_PBKDF2_ITERATIONS,
    )
    return kdf.derive(password.encode("utf-8"))


class PasswordDialog(QDialog):
    """Fragt ein Passwort ab, optional mit Bestätigungsfeld (für Neuverschlüsselung)."""

    def __init__(self, title: str, require_confirmation: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self._require_confirmation = require_confirmation

        self._password_edit = QLineEdit()
        self._password_edit.setEchoMode(QLineEdit.EchoMode.Password)

        form = QFormLayout()
        form.addRow("Passwort:", self._password_edit)

        self._confirm_edit: QLineEdit | None = None
        if require_confirmation:
            self._confirm_edit = QLineEdit()
            self._confirm_edit.setEchoMode(QLineEdit.EchoMode.Password)
            form.addRow("Wiederholen:", self._confirm_edit)

        self._error_label = QLabel()
        self._error_label.setStyleSheet("color: #e74c3c;")

        ok_button = QPushButton("OK")
        ok_button.clicked.connect(self._on_ok_clicked)
        cancel_button = QPushButton("Abbrechen")
        cancel_button.clicked.connect(self.reject)

        button_row = QVBoxLayout()
        button_row.addWidget(ok_button)
        button_row.addWidget(cancel_button)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self._error_label)
        layout.addLayout(button_row)

        self.password: str = ""

    def _on_ok_clicked(self) -> None:
        password = self._password_edit.text()
        if not password:
            self._error_label.setText("Passwort darf nicht leer sein.")
            return
        if self._require_confirmation and self._confirm_edit is not None:
            if password != self._confirm_edit.text():
                self._error_label.setText("Die eingegebenen Passwörter stimmen nicht überein.")
                return
        self.password = password
        self.accept()


class _EncryptWorker(QThread):
    """Verschlüsselt eine einzelne Datei im Hintergrund."""

    finished_ok = pyqtSignal(Path, Path)
    failed = pyqtSignal(Path, str)

    def __init__(self, source_path: Path, password: str) -> None:
        super().__init__()
        self._source_path = source_path
        self._password = password

    def run(self) -> None:  # noqa: D102 - QThread-Standardmethode
        try:
            plaintext = self._source_path.read_bytes()
            salt = os.urandom(_SALT_LENGTH)
            nonce = os.urandom(_NONCE_LENGTH)
            key = _derive_key(self._password, salt)
            ciphertext = AESGCM(key).encrypt(nonce, plaintext, associated_data=None)

            target_path = self._source_path.with_name(self._source_path.name + _ENCRYPTED_SUFFIX)
            with target_path.open("wb") as handle:
                handle.write(_MAGIC_HEADER)
                handle.write(salt)
                handle.write(nonce)
                handle.write(ciphertext)
        except OSError as error:
            self.failed.emit(self._source_path, str(error))
            return

        self.finished_ok.emit(self._source_path, target_path)


class _DecryptWorker(QThread):
    """Entschlüsselt eine einzelne ``.penc``-Datei im Hintergrund."""

    finished_ok = pyqtSignal(Path, Path)
    failed = pyqtSignal(Path, str)

    def __init__(self, source_path: Path, password: str) -> None:
        super().__init__()
        self._source_path = source_path
        self._password = password

    def run(self) -> None:  # noqa: D102 - QThread-Standardmethode
        try:
            raw = self._source_path.read_bytes()
        except OSError as error:
            self.failed.emit(self._source_path, str(error))
            return

        header_length = len(_MAGIC_HEADER) + _SALT_LENGTH + _NONCE_LENGTH
        if len(raw) < header_length or not raw.startswith(_MAGIC_HEADER):
            self.failed.emit(
                self._source_path,
                "Ungültiges Dateiformat (kein Pandora-Verschlüsselungscontainer).",
            )
            return

        offset = len(_MAGIC_HEADER)
        salt = raw[offset : offset + _SALT_LENGTH]
        offset += _SALT_LENGTH
        nonce = raw[offset : offset + _NONCE_LENGTH]
        offset += _NONCE_LENGTH
        ciphertext = raw[offset:]

        try:
            key = _derive_key(self._password, salt)
            plaintext = AESGCM(key).decrypt(nonce, ciphertext, associated_data=None)
        except InvalidTag:
            self.failed.emit(
                self._source_path, "Falsches Passwort oder beschädigte/manipulierte Datei."
            )
            return
        except (ValueError, OSError) as error:
            self.failed.emit(self._source_path, str(error))
            return

        target_name = self._source_path.name
        if target_name.endswith(_ENCRYPTED_SUFFIX):
            target_name = target_name[: -len(_ENCRYPTED_SUFFIX)]
        target_path = self._source_path.with_name(target_name)

        counter = 1
        while target_path.exists():
            stem = Path(target_name).stem
            suffix = Path(target_name).suffix
            target_path = self._source_path.with_name(f"{stem} ({counter}){suffix}")
            counter += 1

        try:
            target_path.write_bytes(plaintext)
        except OSError as error:
            self.failed.emit(self._source_path, str(error))
            return

        self.finished_ok.emit(self._source_path, target_path)


class FileEncryptionPlugin(PandoraPlugin):
    """Plugin zur AES-256-GCM-Verschlüsselung und -Entschlüsselung markierter Dateien."""

    name = "Datei-Verschlüsselung"
    version = "1.0"
    author = "AKI_SystemDown®"
    description = (
        "Fügt dem Kontextmenü 'Verschlüsseln (AES-256) …' und 'Entschlüsseln …' hinzu. "
        "Nutzt AES-256-GCM mit PBKDF2-HMAC-SHA256-Schlüsselableitung (390.000 Iterationen); "
        "benötigt das Paket 'cryptography'."
    )

    def __init__(self) -> None:
        self._context: dict[str, Any] = {}
        self._active_workers: list[QThread] = []

    def on_load(self, context: dict[str, Any]) -> None:
        self._context = context
        if not _CRYPTOGRAPHY_AVAILABLE:
            logger.warning(
                "%s: Paket 'cryptography' nicht gefunden – Verschlüsselung/Entschlüsselung "
                "ist deaktiviert, bis es installiert wird.",
                self.name,
            )
        logger.info("%s geladen.", self.name)

    def on_unload(self) -> None:
        for worker in self._active_workers:
            worker.wait(50)
        self._active_workers.clear()

    def build_context_menu_entries(
        self, context: dict[str, Any], selected_paths: list[Path]
    ) -> list[QAction]:
        if not _CRYPTOGRAPHY_AVAILABLE:
            return []

        file_paths = [path for path in selected_paths if path.is_file()]
        if len(file_paths) != 1:
            return []

        main_window = context.get("main_window")
        active_panel = context.get("active_panel")
        target_path = file_paths[0]

        if target_path.suffix == _ENCRYPTED_SUFFIX:
            action = QAction("Entschlüsseln …", main_window)
            action.triggered.connect(
                lambda checked=False, path=target_path, panel=active_panel: self._start_decrypt(path, panel)
            )
        else:
            action = QAction("Verschlüsseln (AES-256) …", main_window)
            action.triggered.connect(
                lambda checked=False, path=target_path, panel=active_panel: self._start_encrypt(path, panel)
            )
        return [action]

    def _start_encrypt(self, path: Path, panel: Any) -> None:
        main_window = self._context.get("main_window")
        dialog = PasswordDialog("Datei verschlüsseln", require_confirmation=True, parent=main_window)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        worker = _EncryptWorker(path, dialog.password)
        worker.finished_ok.connect(
            lambda source, target, panel=panel: self._on_encrypt_finished(source, target, panel)
        )
        worker.failed.connect(self._on_operation_failed)
        worker.finished.connect(
            lambda w=worker: self._active_workers.remove(w) if w in self._active_workers else None
        )
        self._active_workers.append(worker)
        worker.start()

    def _start_decrypt(self, path: Path, panel: Any) -> None:
        main_window = self._context.get("main_window")
        dialog = PasswordDialog("Datei entschlüsseln", require_confirmation=False, parent=main_window)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        worker = _DecryptWorker(path, dialog.password)
        worker.finished_ok.connect(
            lambda source, target, panel=panel: self._on_decrypt_finished(source, target, panel)
        )
        worker.failed.connect(self._on_operation_failed)
        worker.finished.connect(
            lambda w=worker: self._active_workers.remove(w) if w in self._active_workers else None
        )
        self._active_workers.append(worker)
        worker.start()

    def _on_encrypt_finished(self, source_path: Path, target_path: Path, panel: Any) -> None:
        main_window = self._context.get("main_window")
        if panel is not None and hasattr(panel, "refresh"):
            panel.refresh()

        delete_original = QMessageBox.question(
            main_window,
            "Verschlüsselung abgeschlossen",
            f"'{target_path.name}' wurde erstellt.\n\n"
            f"Ursprüngliche Datei '{source_path.name}' jetzt löschen?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if delete_original == QMessageBox.StandardButton.Yes:
            try:
                source_path.unlink()
            except OSError as error:
                QMessageBox.warning(
                    main_window, "Löschen fehlgeschlagen", f"Ursprungsdatei konnte nicht gelöscht werden: {error}"
                )
            if panel is not None and hasattr(panel, "refresh"):
                panel.refresh()

    def _on_decrypt_finished(self, source_path: Path, target_path: Path, panel: Any) -> None:
        main_window = self._context.get("main_window")
        if panel is not None and hasattr(panel, "refresh"):
            panel.refresh()
        QMessageBox.information(
            main_window, "Entschlüsselung abgeschlossen", f"Datei wurde als '{target_path.name}' wiederhergestellt."
        )

    def _on_operation_failed(self, path: Path, message: str) -> None:
        main_window = self._context.get("main_window")
        QMessageBox.critical(main_window, "Vorgang fehlgeschlagen", f"{path.name}: {message}")
