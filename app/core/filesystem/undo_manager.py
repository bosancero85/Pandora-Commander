"""Pandora® Commander – Undo/Redo für Dateioperationen.

Stellt eine zentrale, Qt-signalfähige UndoManager-Klasse sowie dazu
passende UndoableAction-Implementierungen für alle veränderten
Dateisystem-Aktionen bereit: Kopieren, Verschieben, Löschen (über den
internen Papierkorb, siehe file_operations.move_to_trash),
Umbenennen und Neuer Ordner.

Jede UndoableAction kapselt genug Zustand, um sich selbst sowohl
rückgängig zu machen (undo) als auch erneut auszuführen (redo) – und
das beliebig oft hintereinander (undo → redo → undo → …), da jede
Aktion ihren internen Zustand (z. B. den aktuellen Papierkorb-Pfad
einer gelöschten Datei) nach jedem undo()/redo() aktualisiert.

Verwendung (aus main_window.py heraus):

    self._undo_manager = UndoManager(trash_root=CONFIG_DIR / "trash")
    self._undo_manager.stack_changed.connect(self._update_undo_redo_actions)

    # Nach einer erfolgreichen Kopieraktion:
    self._undo_manager.push(CopyAction(result.succeeded_pairs))

    # Rückgängig machen:
    if self._undo_manager.can_undo:
        description = self._undo_manager.undo()
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal

from app.core.filesystem.file_operations import (
    copy_single,
    move_single,
    move_to_trash,
    remove_single,
    restore_from_trash,
)
from app.core.logging_setup import get_logger

logger = get_logger(__name__)

#: Maximale Anzahl an Aktionen, die der Undo-Stapel vorhält. Ältere
#: Aktionen fallen heraus (und ihr ggf. zugehöriger Papierkorb-Inhalt
#: wird endgültig entfernt), damit der interne Papierkorb nicht
#: unbegrenzt wächst.
MAX_UNDO_HISTORY = 50


class UndoableAction(ABC):
    """Basisklasse für eine rückgängig machbare Dateisystem-Aktion."""

    @property
    @abstractmethod
    def description(self) -> str:
        """Kurzbeschreibung für Menütexte, z. B. "Kopieren von 3 Elementen"."""

    @abstractmethod
    def undo(self) -> None:
        """Macht die Aktion rückgängig.

        Raises:
            OSError: Wenn das Rückgängigmachen fehlschlägt (z. B. weil
                der Zielpfad inzwischen anderweitig belegt ist).
        """

    @abstractmethod
    def redo(self) -> None:
        """Führt die Aktion erneut aus, nachdem sie per undo() rückgängig
        gemacht wurde.

        Raises:
            OSError: Wenn das erneute Ausführen fehlschlägt.
        """

    def discard(self) -> None:
        """Wird aufgerufen, wenn die Aktion endgültig aus der Historie
        fällt (Stapel voll oder Anwendung wird beendet).

        Für die meisten Aktionen ist das ein No-op; DeleteAction nutzt
        es, um verwaiste Papierkorb-Einträge endgültig zu entfernen.
        """


class CopyAction(UndoableAction):
    """Rückgängig machbare Kopieraktion.

    undo(): entfernt die neu erzeugten Kopien wieder.
    redo(): kopiert dieselben Quellen erneut an dieselben Zielpfade.
    """

    def __init__(self, pairs: list[tuple[Path, Path]]) -> None:
        self._pairs = list(pairs)

    @property
    def description(self) -> str:
        return f"Kopieren von {len(self._pairs)} Element(en)"

    def undo(self) -> None:
        for _source, target in self._pairs:
            remove_single(target)

    def redo(self) -> None:
        for source, target in self._pairs:
            copy_single(source, target)


class MoveAction(UndoableAction):
    """Rückgängig machbare Verschiebeaktion.

    undo(): verschiebt jedes Element von seinem Ziel zurück an seinen
        ursprünglichen Quellpfad.
    redo(): verschiebt erneut vom (wiederhergestellten) Quellpfad zum
        Zielpfad.
    """

    def __init__(self, pairs: list[tuple[Path, Path]]) -> None:
        self._pairs = list(pairs)

    @property
    def description(self) -> str:
        return f"Verschieben von {len(self._pairs)} Element(en)"

    def undo(self) -> None:
        for source, target in self._pairs:
            move_single(target, source)

    def redo(self) -> None:
        for source, target in self._pairs:
            move_single(source, target)


class DeleteAction(UndoableAction):
    """Rückgängig machbare Löschaktion (über den internen Papierkorb).

    undo(): stellt alle Elemente aus dem Papierkorb an ihrem
        ursprünglichen Pfad wieder her.
    redo(): verschiebt dieselben Elemente erneut in einen neuen
        Papierkorb-Unterordner (der alte wurde durch undo() bereits
        geleert).
    """

    def __init__(self, pairs: list[tuple[Path, Path]], trash_root: Path) -> None:
        # self._pairs bildet stets (Originalpfad, aktueller Papierkorb-
        # Pfad) ab und ist nur gültig, solange sich die Elemente auch
        # tatsächlich im Papierkorb befinden (self._in_trash).
        self._pairs = list(pairs)
        self._originals = [original for original, _trash in pairs]
        self._trash_root = trash_root
        self._in_trash = True

    @property
    def description(self) -> str:
        return f"Löschen von {len(self._originals)} Element(en)"

    def undo(self) -> None:
        restore_from_trash(self._pairs)
        self._in_trash = False
        self._pairs = []

    def redo(self) -> None:
        result = move_to_trash(self._originals, self._trash_root)
        if result.failed:
            path, message = result.failed[0]
            raise OSError(f'Wiederholen fehlgeschlagen für "{path}": {message}')
        self._pairs = list(result.succeeded_pairs)
        self._in_trash = True

    def discard(self) -> None:
        if not self._in_trash:
            return
        for _original, trash_path in self._pairs:
            try:
                remove_single(trash_path)
            except OSError:
                logger.warning("Papierkorb-Eintrag konnte nicht entfernt werden: %s", trash_path)


class RenameAction(UndoableAction):
    """Rückgängig machbare Umbenennungsaktion."""

    def __init__(self, old_path: Path, new_path: Path) -> None:
        self._old_path = old_path
        self._new_path = new_path

    @property
    def description(self) -> str:
        return f'Umbenennen in "{self._new_path.name}"'

    def undo(self) -> None:
        self._new_path.rename(self._old_path)

    def redo(self) -> None:
        self._old_path.rename(self._new_path)


class NewFolderAction(UndoableAction):
    """Rückgängig machbares Anlegen eines neuen, leeren Ordners."""

    def __init__(self, path: Path) -> None:
        self._path = path

    @property
    def description(self) -> str:
        return f'Neuer Ordner "{self._path.name}"'

    def undo(self) -> None:
        self._path.rmdir()

    def redo(self) -> None:
        self._path.mkdir(parents=False, exist_ok=False)


class UndoManager(QObject):
    """Verwaltet Undo-/Redo-Stapel für alle Dateisystem-Aktionen.

    Signals:
        stack_changed: Wird nach jeder Änderung des Undo- oder
            Redo-Stapels gesendet (push, undo, redo, clear) – für die
            Oberfläche, um Aktionstexte/aktivierten Zustand von
            "Rückgängig"/"Wiederholen" zu aktualisieren.
    """

    stack_changed = pyqtSignal()

    def __init__(self, trash_root: Path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._trash_root = trash_root
        self._undo_stack: list[UndoableAction] = []
        self._redo_stack: list[UndoableAction] = []

    @property
    def trash_root(self) -> Path:
        """Basisverzeichnis des internen Papierkorbs."""
        return self._trash_root

    @property
    def can_undo(self) -> bool:
        return bool(self._undo_stack)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo_stack)

    @property
    def undo_description(self) -> str | None:
        return self._undo_stack[-1].description if self._undo_stack else None

    @property
    def redo_description(self) -> str | None:
        return self._redo_stack[-1].description if self._redo_stack else None

    def push(self, action: UndoableAction) -> None:
        """Legt eine neu ausgeführte Aktion auf den Undo-Stapel.

        Löscht dabei den Redo-Stapel (wie bei üblichen Editoren: eine
        neue Aktion macht zuvor rückgängig gemachte Schritte
        endgültig verworfen) und begrenzt die Historie auf
        MAX_UNDO_HISTORY Einträge.

        Args:
            action: Die auszuführende, bereits abgeschlossene Aktion.
        """
        for discarded in self._redo_stack:
            discarded.discard()
        self._redo_stack.clear()

        self._undo_stack.append(action)
        while len(self._undo_stack) > MAX_UNDO_HISTORY:
            oldest = self._undo_stack.pop(0)
            oldest.discard()

        self.stack_changed.emit()

    def undo(self) -> str:
        """Macht die zuletzt ausgeführte Aktion rückgängig.

        Returns:
            Die Kurzbeschreibung der rückgängig gemachten Aktion.

        Raises:
            IndexError: Wenn der Undo-Stapel leer ist.
            OSError: Wenn das Rückgängigmachen fehlschlägt; die
                Aktion bleibt in diesem Fall auf dem Undo-Stapel.
        """
        if not self._undo_stack:
            raise IndexError("Undo-Stapel ist leer.")
        action = self._undo_stack[-1]
        action.undo()
        self._undo_stack.pop()
        self._redo_stack.append(action)
        self.stack_changed.emit()
        return action.description

    def redo(self) -> str:
        """Führt die zuletzt rückgängig gemachte Aktion erneut aus.

        Returns:
            Die Kurzbeschreibung der wiederholten Aktion.

        Raises:
            IndexError: Wenn der Redo-Stapel leer ist.
            OSError: Wenn das Wiederholen fehlschlägt; die Aktion
                bleibt in diesem Fall auf dem Redo-Stapel.
        """
        if not self._redo_stack:
            raise IndexError("Redo-Stapel ist leer.")
        action = self._redo_stack[-1]
        action.redo()
        self._redo_stack.pop()
        self._undo_stack.append(action)
        self.stack_changed.emit()
        return action.description

    def purge(self) -> None:
        """Verwirft alle Aktionen und räumt zugehörige Papierkorb-Inhalte auf.

        Für den Aufruf beim Beenden der Anwendung.
        """
        for action in (*self._undo_stack, *self._redo_stack):
            action.discard()
        self._undo_stack.clear()
        self._redo_stack.clear()
        self.stack_changed.emit()
