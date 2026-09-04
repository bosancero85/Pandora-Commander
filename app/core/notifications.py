"""Pandora® Commander – Systembenachrichtigungen.

Kapselt native Desktop-Benachrichtigungen (Windows-Toasts,
Linux-Notification-Daemons via libnotify/D-Bus, macOS Notification
Center) über Qt's plattformübergreifendes QSystemTrayIcon. Damit
erhält der Nutzer auch dann eine Rückmeldung über abgeschlossene
Hintergrundoperationen (Kopieren, Verschieben, Löschen, ...), wenn
das Hauptfenster minimiert oder nicht fokussiert ist.

Verwendung:
    from app.core.notifications import NotificationManager

    notifications = NotificationManager(app_icon, parent=self)
    notifications.set_enabled(settings.notifications_enabled)
    notifications.notify_success("Kopieren", "12 Elemente kopiert.")
    notifications.notify_warning("Kopieren", "3 von 12 fehlgeschlagen.")

Der Manager ist bewusst tolerant: Ist auf der aktuellen Plattform
kein System-Tray verfügbar (z. B. in manchen minimalen Linux-
Umgebungen oder auf Build-/CI-Servern), werden Benachrichtigungen
still übersprungen statt die Anwendung zum Absturz zu bringen.
"""

from __future__ import annotations

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QSystemTrayIcon, QWidget

from app.core.logging_setup import get_logger

logger = get_logger(__name__)

#: Wie lange eine Benachrichtigung sichtbar bleibt, in Millisekunden.
_DISPLAY_DURATION_MS = 6000


class NotificationManager:
    """Zeigt native System-Benachrichtigungen für Hintergrundoperationen an.

    Args:
        app_icon: Icon der Anwendung, das für das Tray-Symbol sowie
            als Fallback-Icon der Benachrichtigungen verwendet wird.
        parent: Optionales Eltern-Widget (üblicherweise das
            Hauptfenster), an das das zugrundeliegende
            QSystemTrayIcon gebunden wird.
    """

    def __init__(self, app_icon: QIcon, parent: QWidget | None = None) -> None:
        self._enabled = True
        self._tray_icon: QSystemTrayIcon | None = None

        if QSystemTrayIcon.isSystemTrayAvailable():
            self._tray_icon = QSystemTrayIcon(app_icon, parent)
            self._tray_icon.setToolTip("Pandora® Commander")
            # Der Tray-Eintrag selbst dient hier ausschließlich als
            # Transportweg für Benachrichtigungen und wird nicht mit
            # einem eigenen Kontextmenü sichtbar gemacht, solange
            # keine Benachrichtigung ansteht.
        else:
            logger.info(
                "Kein System-Tray auf dieser Plattform verfügbar – "
                "Benachrichtigungen werden deaktiviert."
            )

    # ------------------------------------------------------------------
    # Konfiguration
    # ------------------------------------------------------------------

    def set_enabled(self, enabled: bool) -> None:
        """Schaltet Benachrichtigungen ein oder aus (aus den Einstellungen)."""
        self._enabled = enabled

    @property
    def is_available(self) -> bool:
        """Ob auf dieser Plattform überhaupt Benachrichtigungen möglich sind."""
        return self._tray_icon is not None

    # ------------------------------------------------------------------
    # Öffentliche API
    # ------------------------------------------------------------------

    def notify_success(self, title: str, message: str) -> None:
        """Zeigt eine Erfolgsbenachrichtigung an (z. B. abgeschlossene Operation)."""
        self._show(title, message, QSystemTrayIcon.MessageIcon.Information)

    def notify_warning(self, title: str, message: str) -> None:
        """Zeigt eine Warnbenachrichtigung an (z. B. Operation mit Fehlern)."""
        self._show(title, message, QSystemTrayIcon.MessageIcon.Warning)

    def notify_error(self, title: str, message: str) -> None:
        """Zeigt eine Fehlerbenachrichtigung an (z. B. fehlgeschlagene Operation)."""
        self._show(title, message, QSystemTrayIcon.MessageIcon.Critical)

    def shutdown(self) -> None:
        """Verbirgt das Tray-Icon beim Beenden der Anwendung."""
        if self._tray_icon is not None:
            self._tray_icon.hide()

    # ------------------------------------------------------------------
    # Intern
    # ------------------------------------------------------------------

    def _show(self, title: str, message: str, icon: QSystemTrayIcon.MessageIcon) -> None:
        if not self._enabled or self._tray_icon is None:
            return
        try:
            # showMessage() macht das Tray-Icon implizit kurzzeitig
            # sichtbar, sofern es das nicht bereits ist – auf manchen
            # Desktop-Umgebungen ist ein sichtbares Icon Voraussetzung
            # dafür, dass die Benachrichtigung überhaupt erscheint.
            self._tray_icon.show()
            self._tray_icon.showMessage(title, message, icon, _DISPLAY_DURATION_MS)
        except Exception:  # noqa: BLE001 - Benachrichtigungen dürfen nie abstürzen
            logger.exception("Benachrichtigung konnte nicht angezeigt werden.")
