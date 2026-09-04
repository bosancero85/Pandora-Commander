"""Pandora® Commander – Plugin: Berechtigungs-Editor (chmod/chown).

Fügt dem Rechtsklick-Kontextmenü der Dateipanels den Eintrag
"Berechtigungen …" hinzu und öffnet dafür einen grafischen Dialog im
Stil von ``chmod``/``chown`` – zugeschnitten auf Linux (Kali Linux /
Raspberry Pi 4B), da POSIX-Berechtigungsbits und ``chown`` unter
Windows nicht in derselben Form existieren.

Der Dialog zeigt:
    * eine 3x3-Checkbox-Matrix (Besitzer/Gruppe/Andere × Lesen/
      Schreiben/Ausführen), synchron mit einem editierbaren
      Oktal-Feld (z. B. "755") – beide Darstellungen aktualisieren
      sich gegenseitig live.
    * Besitzer- und Gruppenname als Textfelder, vorbefüllt mit den
      aktuellen Werten (Auflösung über ``pwd``/``grp``); leer lassen
      bedeutet "unverändert lassen".
    * bei markierten Ordnern eine Option "Rekursiv auf Inhalt
      anwenden".

Da das Ändern von Besitzer/Gruppe (``chown``) unter Linux in der
Regel Root-Rechte voraussetzt, werden ``PermissionError`` und
sonstige ``OSError`` beim Anwenden abgefangen und dem Nutzer als
verständliche Fehlermeldung präsentiert, statt die Anwendung
abstürzen zu lassen. Änderungen an Berechtigungsbits (``chmod``)
funktionieren dagegen für den Eigentümer der Datei ohne
Zusatzrechte.

Bei rekursiver Anwendung läuft die eigentliche Änderung in einem
Hintergrund-Thread, damit die Oberfläche bei großen Verzeichnissen
nicht einfriert.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path
from typing import Any

_IS_POSIX = sys.platform != "win32"

if _IS_POSIX:
    import grp
    import pwd
else:  # pragma: no cover - nur zur Ladbarkeit unter Windows
    grp = None  # type: ignore[assignment]
    pwd = None  # type: ignore[assignment]

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from app.core.logging_setup import get_logger
from app.plugins.plugin_manager import PandoraPlugin

logger = get_logger(__name__)

_PERMISSION_BITS: dict[tuple[str, str], int] = {
    ("owner", "read"): stat.S_IRUSR,
    ("owner", "write"): stat.S_IWUSR,
    ("owner", "execute"): stat.S_IXUSR,
    ("group", "read"): stat.S_IRGRP,
    ("group", "write"): stat.S_IWGRP,
    ("group", "execute"): stat.S_IXGRP,
    ("other", "read"): stat.S_IROTH,
    ("other", "write"): stat.S_IWOTH,
    ("other", "execute"): stat.S_IXOTH,
}
_ROWS = ("owner", "group", "other")
_ROW_LABELS = {"owner": "Besitzer", "group": "Gruppe", "other": "Andere"}
_COLUMNS = ("read", "write", "execute")
_COLUMN_LABELS = {"read": "Lesen", "write": "Schreiben", "execute": "Ausführen"}


def _mode_to_octal_string(mode: int) -> str:
    return format(stat.S_IMODE(mode), "03o")


def _resolve_owner_name(uid: int) -> str:
    if not _IS_POSIX:
        return str(uid)
    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return str(uid)


def _resolve_group_name(gid: int) -> str:
    if not _IS_POSIX:
        return str(gid)
    try:
        return grp.getgrgid(gid).gr_name
    except KeyError:
        return str(gid)


class PermissionsDialog(QDialog):
    """Grafischer chmod/chown-Dialog für eine oder mehrere markierte Dateien/Ordner."""

    def __init__(self, paths: list[Path], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Pandora® Commander – Berechtigungen")
        self._paths = paths

        first_stat = paths[0].stat()
        initial_mode = stat.S_IMODE(first_stat.st_mode)

        self._checkboxes: dict[tuple[str, str], QCheckBox] = {}
        permission_grid = QGridLayout()
        permission_grid.addWidget(QLabel(""), 0, 0)
        for column_index, column in enumerate(_COLUMNS, start=1):
            permission_grid.addWidget(QLabel(_COLUMN_LABELS[column]), 0, column_index)
        for row_index, row in enumerate(_ROWS, start=1):
            permission_grid.addWidget(QLabel(_ROW_LABELS[row]), row_index, 0)
            for column_index, column in enumerate(_COLUMNS, start=1):
                checkbox = QCheckBox()
                checkbox.setChecked(bool(initial_mode & _PERMISSION_BITS[(row, column)]))
                checkbox.stateChanged.connect(self._on_checkbox_changed)
                self._checkboxes[(row, column)] = checkbox
                permission_grid.addWidget(checkbox, row_index, column_index)

        permission_group = QGroupBox("Berechtigungen (chmod)")
        permission_group.setLayout(permission_grid)

        self._octal_edit = QLineEdit(_mode_to_octal_string(initial_mode))
        self._octal_edit.setMaxLength(3)
        self._octal_edit.textEdited.connect(self._on_octal_edited)

        octal_form = QFormLayout()
        octal_form.addRow("Oktal:", self._octal_edit)

        self._owner_edit = QLineEdit(_resolve_owner_name(first_stat.st_uid))
        self._group_edit = QLineEdit(_resolve_group_name(first_stat.st_gid))
        ownership_form = QFormLayout()
        ownership_form.addRow("Besitzer:", self._owner_edit)
        ownership_form.addRow("Gruppe:", self._group_edit)
        ownership_group = QGroupBox("Besitz (chown) – leer lassen für 'unverändert'")
        ownership_group.setLayout(ownership_form)

        self._recursive_checkbox = QCheckBox("Rekursiv auf Ordnerinhalt anwenden")
        any_directory = any(path.is_dir() for path in paths)
        self._recursive_checkbox.setVisible(any_directory)

        self._hint_label = QLabel(
            f"{len(paths)} Element(e) markiert. Werte oben basieren auf der ersten Auswahl "
            "und werden auf alle markierten Elemente angewendet."
            if len(paths) > 1
            else ""
        )
        self._hint_label.setStyleSheet("color: gray; font-size: 11px;")
        self._hint_label.setWordWrap(True)

        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(self._hint_label)
        layout.addWidget(permission_group)
        layout.addLayout(octal_form)
        layout.addWidget(ownership_group)
        layout.addWidget(self._recursive_checkbox)
        layout.addWidget(button_box)

    def _on_checkbox_changed(self) -> None:
        mode = 0
        for key, checkbox in self._checkboxes.items():
            if checkbox.isChecked():
                mode |= _PERMISSION_BITS[key]
        self._octal_edit.blockSignals(True)
        self._octal_edit.setText(_mode_to_octal_string(mode))
        self._octal_edit.blockSignals(False)

    def _on_octal_edited(self, text: str) -> None:
        try:
            mode = int(text, 8)
        except ValueError:
            return
        for key, checkbox in self._checkboxes.items():
            checkbox.blockSignals(True)
            checkbox.setChecked(bool(mode & _PERMISSION_BITS[key]))
            checkbox.blockSignals(False)

    @property
    def resulting_mode(self) -> int:
        try:
            return int(self._octal_edit.text(), 8) & 0o777
        except ValueError:
            mode = 0
            for key, checkbox in self._checkboxes.items():
                if checkbox.isChecked():
                    mode |= _PERMISSION_BITS[key]
            return mode

    @property
    def owner_name(self) -> str:
        return self._owner_edit.text().strip()

    @property
    def group_name(self) -> str:
        return self._group_edit.text().strip()

    @property
    def recursive(self) -> bool:
        return self._recursive_checkbox.isVisible() and self._recursive_checkbox.isChecked()


class _ApplyPermissionsWorker(QThread):
    """Wendet Berechtigungs-/Besitzänderungen im Hintergrund an (für rekursive Fälle)."""

    finished_ok = pyqtSignal(int, int)  # Erfolge, Gesamtanzahl
    item_failed = pyqtSignal(Path, str)

    def __init__(
        self,
        paths: list[Path],
        mode: int,
        uid: int | None,
        gid: int | None,
        recursive: bool,
    ) -> None:
        super().__init__()
        self._paths = paths
        self._mode = mode
        self._uid = uid
        self._gid = gid
        self._recursive = recursive

    def run(self) -> None:  # noqa: D102 - QThread-Standardmethode
        targets: list[Path] = []
        for path in self._paths:
            targets.append(path)
            if self._recursive and path.is_dir():
                try:
                    targets.extend(entry for entry in path.rglob("*"))
                except OSError as error:
                    self.item_failed.emit(path, f"Konnte nicht vollständig durchsucht werden: {error}")

        success_count = 0
        for target in targets:
            try:
                os.chmod(target, self._mode)
                if self._uid is not None or self._gid is not None:
                    os.chown(
                        target,
                        self._uid if self._uid is not None else -1,
                        self._gid if self._gid is not None else -1,
                    )
                success_count += 1
            except OSError as error:
                self.item_failed.emit(target, str(error))

        self.finished_ok.emit(success_count, len(targets))


class PermissionsEditorPlugin(PandoraPlugin):
    """Plugin für einen grafischen chmod/chown-Editor im Kontextmenü."""

    name = "Berechtigungs-Editor"
    version = "1.0"
    author = "AKI_SystemDown®"
    description = (
        "Fügt dem Kontextmenü 'Berechtigungen …' hinzu: grafischer chmod/chown-Editor mit "
        "3x3-Checkbox-Matrix, Oktal-Eingabe, Besitzer-/Gruppenänderung und optional "
        "rekursiver Anwendung auf Ordnerinhalte."
    )

    def __init__(self) -> None:
        self._context: dict[str, Any] = {}
        self._active_workers: list[_ApplyPermissionsWorker] = []

    def on_load(self, context: dict[str, Any]) -> None:
        self._context = context
        logger.info("%s geladen.", self.name)

    def on_unload(self) -> None:
        for worker in self._active_workers:
            worker.wait(50)
        self._active_workers.clear()

    def build_context_menu_entries(
        self, context: dict[str, Any], selected_paths: list[Path]
    ) -> list[QAction]:
        if not _IS_POSIX:
            # POSIX-Berechtigungsbits/chown ergeben unter Windows keinen Sinn
            # und pwd/grp stehen dort nicht zur Verfügung.
            return []

        existing_paths = [path for path in selected_paths if path.exists()]
        if not existing_paths:
            return []

        main_window = context.get("main_window")
        active_panel = context.get("active_panel")

        action = QAction("Berechtigungen …", main_window)
        action.triggered.connect(
            lambda checked=False, paths=existing_paths, panel=active_panel: self._open_dialog(paths, panel)
        )
        return [action]

    def _open_dialog(self, paths: list[Path], panel: Any) -> None:
        main_window = self._context.get("main_window")
        try:
            dialog = PermissionsDialog(paths, parent=main_window)
        except OSError as error:
            QMessageBox.critical(main_window, "Fehler", f"Berechtigungen konnten nicht gelesen werden: {error}")
            return

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        uid: int | None = None
        if dialog.owner_name:
            try:
                uid = pwd.getpwnam(dialog.owner_name).pw_uid
            except KeyError:
                QMessageBox.warning(main_window, "Unbekannter Benutzer", f"Benutzer '{dialog.owner_name}' existiert nicht.")
                return

        gid: int | None = None
        if dialog.group_name:
            try:
                gid = grp.getgrnam(dialog.group_name).gr_gid
            except KeyError:
                QMessageBox.warning(main_window, "Unbekannte Gruppe", f"Gruppe '{dialog.group_name}' existiert nicht.")
                return

        worker = _ApplyPermissionsWorker(paths, dialog.resulting_mode, uid, gid, dialog.recursive)
        self._failures: list[tuple[Path, str]] = []
        worker.item_failed.connect(lambda path, message: self._failures.append((path, message)))
        worker.finished_ok.connect(
            lambda success, total, panel=panel: self._on_finished(success, total, panel)
        )
        worker.finished.connect(
            lambda w=worker: self._active_workers.remove(w) if w in self._active_workers else None
        )
        self._active_workers.append(worker)
        worker.start()

    def _on_finished(self, success_count: int, total: int, panel: Any) -> None:
        main_window = self._context.get("main_window")
        if panel is not None and hasattr(panel, "refresh"):
            panel.refresh()

        if success_count == total:
            QMessageBox.information(main_window, "Fertig", f"Berechtigungen für {success_count} Element(e) angewendet.")
        else:
            error_text = "\n".join(f"{path}: {message}" for path, message in self._failures[:15])
            if len(self._failures) > 15:
                error_text += f"\n… und {len(self._failures) - 15} weitere."
            QMessageBox.warning(
                main_window,
                "Teilweise fehlgeschlagen",
                f"{success_count} von {total} Element(en) erfolgreich geändert.\n\n{error_text}",
            )
