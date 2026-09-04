"""Pandora® Commander – Update-Dialog.

Zeigt an, dass eine neuere Version von Pandora® Commander verfügbar
ist, inklusive Changelog-Text und einem Button, der die konfigurierte
Download-Adresse im Standardbrowser öffnet. Der Dialog nimmt selbst
keine Installation vor (kein Selbst-Updater) – das entspricht dem
Sicherheitsgrundsatz "robust, fehlertolerant, sicher": ein externer
Download über den Browser lässt sich vom Nutzer jederzeit prüfen,
bevor etwas ausgeführt wird.
"""

from __future__ import annotations

from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.core.update_checker import UpdateInfo


class UpdateAvailableDialog(QDialog):
    """Informiert über eine verfügbare neuere Version.

    Args:
        current_version: Aktuell installierte Version.
        update_info: Informationen zur verfügbaren neueren Version.
        parent: Optionales Eltern-Widget.
    """

    def __init__(
        self,
        current_version: str,
        update_info: UpdateInfo,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._update_info = update_info

        self.setWindowTitle("Update verfügbar")
        self.setMinimumWidth(440)

        headline = QLabel(
            f"<b>Eine neue Version ist verfügbar: {update_info.version}</b>"
            f"<br>Installiert ist derzeit Version {current_version}."
        )
        headline.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.addWidget(headline)

        if update_info.notes:
            notes_label = QLabel("Änderungen in dieser Version:")
            layout.addWidget(notes_label)

            notes_view = QPlainTextEdit(update_info.notes)
            notes_view.setReadOnly(True)
            notes_view.setMaximumHeight(160)
            layout.addWidget(notes_view)

        buttons = QDialogButtonBox(self)
        self._later_button = buttons.addButton(
            "Später erinnern", QDialogButtonBox.ButtonRole.RejectRole
        )
        self._download_button = buttons.addButton(
            "Herunterladen …", QDialogButtonBox.ButtonRole.AcceptRole
        )
        self._download_button.setEnabled(bool(update_info.download_url))
        self._download_button.setDefault(True)

        self._later_button.clicked.connect(self.reject)
        self._download_button.clicked.connect(self._on_download_clicked)

        layout.addWidget(buttons)

    def _on_download_clicked(self) -> None:
        """Öffnet die Download-URL im Standardbrowser des Systems."""
        if self._update_info.download_url:
            QDesktopServices.openUrl(QUrl(self._update_info.download_url))
        self.accept()
