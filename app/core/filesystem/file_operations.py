"""Pandora® Commander – Dateioperationen (Kopieren, Verschieben, Löschen).

Kapselt alle verändernden Dateisystem-Operationen in einer robusten,
fehlertoleranten Implementierung sowie einem QThread-Worker, der diese
Operationen im Hintergrund ausführt. Damit friert die Oberfläche bei
größeren Kopier-/Verschiebe-/Löschvorgängen niemals ein (siehe
Abschnitt "Performance" im Lastenheft).

Zwei Ebenen:
    * Reine Python-Funktionen (copy_paths, move_paths, delete_paths),
      unabhängig von Qt und einzeln testbar.
    * FileOperationWorker(QThread), der diese Funktionen in einem
      eigenen Thread ausführt und Fortschritt/Fehler/Ergebnis über
      Qt-Signale an die Oberfläche meldet.

Verwendung (aus einer späteren UI-Datei heraus, z. B. beim Verdrahten
von F5/F6/F8):

    worker = FileOperationWorker(
        operation=OperationType.COPY,
        sources=[Path("/pfad/a.txt")],
        destination=Path("/ziel"),
        collision_policy=CollisionPolicy.RENAME,
    )
    worker.progress_changed.connect(...)
    worker.item_failed.connect(...)
    worker.operation_finished.connect(...)
    worker.start()
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal

from app.core.logging_setup import get_logger

logger = get_logger(__name__)


class OperationType(str, Enum):
    """Art der auszuführenden Dateioperation."""

    COPY = "copy"
    MOVE = "move"
    DELETE = "delete"
    #: Löschen "in den Papierkorb" (internes Trash-Verzeichnis) statt
    #: endgültigem Entfernen – macht das Löschen über den UndoManager
    #: rückgängig machbar (siehe app.core.filesystem.undo_manager).
    DELETE_TO_TRASH = "delete_to_trash"


class CollisionPolicy(str, Enum):
    """Verhalten bei bereits vorhandenem Ziel gleichen Namens.

    ASK wird bewusst nicht als Option angeboten: Der Worker läuft in
    einem Hintergrund-Thread und kann keinen blockierenden Dialog auf
    dem UI-Thread anzeigen. Eine spätere UI-Datei kann stattdessen vor
    dem Start des Workers per Vorabprüfung (siehe
    ``find_existing_collisions``) einen Dialog anzeigen und daraus
    eine der drei folgenden Policies ableiten.
    """

    OVERWRITE = "overwrite"
    SKIP = "skip"
    RENAME = "rename"


class OperationCancelled(Exception):
    """Wird intern ausgelöst, wenn der Nutzer eine Operation abbricht."""


@dataclass
class OperationResult:
    """Ergebnis einer abgeschlossenen (oder abgebrochenen) Operation.

    Attributes:
        operation: Die ausgeführte Operationsart.
        succeeded: Erfolgreich verarbeitete Zielpfade.
        skipped: Übersprungene Quellpfade (Kollision mit SKIP-Policy).
        failed: Liste aus (Quellpfad, Fehlermeldung) für fehlgeschlagene
            Einträge. Einzelne Fehler brechen die Gesamtoperation
            nicht ab (fehlertolerant gemäß Sicherheits-Vorgabe).
        cancelled: Ob die Operation vom Nutzer abgebrochen wurde,
            bevor alle Quellen verarbeitet wurden.
        succeeded_pairs: Für COPY/MOVE (Quelle, Ziel), für
            DELETE_TO_TRASH (ursprünglicher Pfad, Papierkorb-Pfad) –
            je erfolgreich verarbeitetem obersten Element. Wird vom
            UndoManager benötigt, um Operationen exakt rückgängig
            machen bzw. wiederholen (redo) zu können. Bei einfachem
            DELETE (endgültig, ohne Papierkorb) bleibt die Liste leer,
            da ein Undo dafür nicht möglich ist.
    """

    operation: OperationType
    succeeded: list[Path] = field(default_factory=list)
    skipped: list[Path] = field(default_factory=list)
    failed: list[tuple[Path, str]] = field(default_factory=list)
    cancelled: bool = False
    succeeded_pairs: list[tuple[Path, Path]] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        """True, wenn mindestens ein Eintrag fehlgeschlagen ist."""
        return len(self.failed) > 0

    def summary_text(self) -> str:
        """Baut eine kurze, für die Statusleiste geeignete Zusammenfassung."""
        parts = [f"{len(self.succeeded)} erfolgreich"]
        if self.skipped:
            parts.append(f"{len(self.skipped)} übersprungen")
        if self.failed:
            parts.append(f"{len(self.failed)} fehlgeschlagen")
        if self.cancelled:
            parts.append("abgebrochen")
        return ", ".join(parts)


def find_existing_collisions(sources: list[Path], destination: Path) -> list[Path]:
    """Ermittelt, welche der obersten Quellnamen im Ziel bereits existieren.

    Für den Aufruf durch die UI *vor* dem Start eines Kopier-/
    Verschiebevorgangs, um interaktiv nach der gewünschten
    CollisionPolicy zu fragen.

    Args:
        sources: Zu verschiebende/kopierende Quellpfade.
        destination: Zielverzeichnis.

    Returns:
        Teilliste von sources, deren Zielname im Zielverzeichnis
        bereits existiert.
    """
    return [src for src in sources if (destination / src.name).exists()]


def _unique_destination(destination_dir: Path, name: str) -> Path:
    """Findet einen im Zielverzeichnis noch unbenutzten Dateinamen.

    Fügt bei Kollision fortlaufend " (1)", " (2)", … vor der Endung
    ein, analog zum Verhalten üblicher Dateimanager.

    Args:
        destination_dir: Zielverzeichnis.
        name: Ursprünglicher Datei-/Ordnername.

    Returns:
        Ein Pfad innerhalb destination_dir, der noch nicht existiert.
    """
    stem = Path(name).stem
    suffix = Path(name).suffix
    candidate = destination_dir / name
    counter = 1
    while candidate.exists():
        candidate = destination_dir / f"{stem} ({counter}){suffix}"
        counter += 1
    return candidate


def _resolve_target(
    source: Path,
    destination_dir: Path,
    collision_policy: CollisionPolicy,
) -> Path | None:
    """Bestimmt den endgültigen Zielpfad für eine oberste Quelle.

    Args:
        source: Zu verarbeitende Quelle (Datei oder Ordner).
        destination_dir: Zielverzeichnis.
        collision_policy: Verhalten bei bereits vorhandenem Ziel.

    Returns:
        Den zu verwendenden Zielpfad, oder None, wenn der Eintrag
        gemäß SKIP-Policy übersprungen werden soll.
    """
    target = destination_dir / source.name
    if not target.exists():
        return target

    if collision_policy == CollisionPolicy.SKIP:
        return None
    if collision_policy == CollisionPolicy.RENAME:
        return _unique_destination(destination_dir, source.name)
    # OVERWRITE: vorhandenes Ziel vor dem Kopieren/Verschieben entfernen.
    _remove_path(target)
    return target


def _remove_path(path: Path) -> None:
    """Entfernt eine Datei, einen Symlink oder einen Ordner rekursiv."""
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def _count_items(paths: list[Path]) -> int:
    """Zählt die Gesamtzahl an Dateisystem-Einträgen (für den Fortschritt).

    Jede Quelle selbst zählt als ein Element; Ordner werden rekursiv
    mitgezählt, damit der Fortschrittsbalken bei großen Ordnerbäumen
    nicht bei 0 % hängen bleibt.

    Args:
        paths: Zu verarbeitende Pfade.

    Returns:
        Gesamtanzahl der zu verarbeitenden Einträge (mindestens 1).
    """
    total = 0
    for path in paths:
        total += 1
        if path.is_dir() and not path.is_symlink():
            try:
                total += sum(1 for _ in path.rglob("*"))
            except OSError:
                pass
    return max(total, 1)


def copy_paths(
    sources: list[Path],
    destination: Path,
    collision_policy: CollisionPolicy = CollisionPolicy.RENAME,
    progress_callback: "callable[[int, int, str], None] | None" = None,
    cancel_check: "callable[[], bool] | None" = None,
) -> OperationResult:
    """Kopiert eine Liste von Dateien/Ordnern in ein Zielverzeichnis.

    Kopiert dateiweise (statt shutil.copytree in einem Rutsch), damit
    Fortschritt gemeldet und ein Abbruch mitten in großen Ordnern
    erkannt werden kann. Metadaten (Zeitstempel, Berechtigungen)
    werden über shutil.copy2 erhalten.

    Args:
        sources: Zu kopierende Quellpfade.
        destination: Zielverzeichnis. Wird bei Bedarf angelegt.
        collision_policy: Verhalten bei bereits vorhandenem Ziel.
        progress_callback: Optional aufgerufen als
            (erledigt, gesamt, aktueller_name) nach jedem Element.
        cancel_check: Optional aufgerufen vor jedem Element; liefert
            True zurück, wenn abgebrochen werden soll.

    Returns:
        OperationResult mit Erfolgen, Übersprüngen und Fehlern.
    """
    result = OperationResult(operation=OperationType.COPY)
    destination.mkdir(parents=True, exist_ok=True)

    total = _count_items(sources)
    done = 0

    def report(name: str) -> None:
        nonlocal done
        done += 1
        if progress_callback is not None:
            progress_callback(done, total, name)

    try:
        for source in sources:
            if cancel_check is not None and cancel_check():
                result.cancelled = True
                break

            if not source.exists() and not source.is_symlink():
                result.failed.append((source, "Quelle existiert nicht mehr."))
                continue

            target = _resolve_target(source, destination, collision_policy)
            if target is None:
                result.skipped.append(source)
                report(source.name)
                continue

            try:
                _copy_one(source, target, report, cancel_check)
                result.succeeded.append(target)
                result.succeeded_pairs.append((source, target))
            except OperationCancelled:
                result.cancelled = True
                break
            except OSError as error:
                logger.warning("Kopieren fehlgeschlagen: %s -> %s (%s)", source, target, error)
                result.failed.append((source, str(error)))
    except OperationCancelled:
        result.cancelled = True

    return result


def _copy_one(
    source: Path,
    target: Path,
    report: "callable[[str], None]",
    cancel_check: "callable[[], bool] | None",
) -> None:
    """Kopiert eine einzelne Quelle (Datei, Symlink oder Ordner) rekursiv.

    Args:
        source: Quellpfad.
        target: Bereits aufgelöster Zielpfad (Kollision ist behandelt).
        report: Callback, das nach jedem verarbeiteten Element mit
            dessen Namen aufgerufen wird.
        cancel_check: Liefert True, wenn abgebrochen werden soll.

    Raises:
        OperationCancelled: Wenn cancel_check() True liefert.
        OSError: Bei nicht behebbaren Dateisystemfehlern.
    """
    if cancel_check is not None and cancel_check():
        raise OperationCancelled

    if source.is_symlink():
        link_target = source.readlink()
        if target.exists() or target.is_symlink():
            _remove_path(target)
        target.symlink_to(link_target)
        report(source.name)
        return

    if source.is_dir():
        target.mkdir(parents=True, exist_ok=True)
        for child in sorted(source.iterdir()):
            _copy_one(child, target / child.name, report, cancel_check)
        report(source.name)
        return

    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    report(source.name)


def copy_single(source: Path, target: Path) -> None:
    """Kopiert genau eine Quelle auf einen exakt vorgegebenen Zielpfad.

    Anders als copy_paths() wird hier keine Kollisionsbehandlung
    durchgeführt – target wird exakt so verwendet, wie übergeben.
    Gedacht für den UndoManager (redo einer Kopieraktion: dieselbe
    Quelle erneut an denselben, zuvor durch undo() wieder freien
    Zielpfad kopieren).

    Args:
        source: Zu kopierende Quelle.
        target: Exakter Zielpfad (muss frei sein).

    Raises:
        OSError: Bei nicht behebbaren Dateisystemfehlern.
    """
    _copy_one(source, target, lambda _name: None, None)


def remove_single(path: Path) -> None:
    """Entfernt eine einzelne Datei/einen einzelnen Ordner endgültig.

    Öffentlicher Alias für die interne _remove_path(), gedacht für
    den UndoManager (undo einer Kopieraktion: die neu erzeugte Kopie
    wieder entfernen).

    Args:
        path: Zu entfernender Pfad.
    """
    _remove_path(path)


def _move_one(source: Path, target: Path) -> None:
    """Verschiebt eine einzelne Quelle auf einen exakt vorgegebenen Zielpfad.

    Versucht zunächst shutil.move; schlägt dies fehl (z. B.
    laufwerksübergreifend), wird auf Kopieren + Löschen der Quelle
    zurückgefallen. Gemeinsame Implementierung für move_paths() sowie
    move_single() (Undo/Redo einer Verschiebeaktion).

    Args:
        source: Zu verschiebende Quelle.
        target: Exakter Zielpfad.

    Raises:
        OSError: Wenn weder shutil.move noch der Kopieren+Löschen-
            Fallback erfolgreich waren.
    """
    try:
        shutil.move(str(source), str(target))
    except (OSError, shutil.Error) as error:
        logger.warning(
            "Direktes Verschieben fehlgeschlagen, versuche Kopieren+Löschen: "
            "%s -> %s (%s)",
            source,
            target,
            error,
        )
        _copy_one(source, target, lambda _name: None, None)
        _remove_path(source)


def move_single(source: Path, target: Path) -> None:
    """Verschiebt genau eine Quelle auf einen exakt vorgegebenen Zielpfad.

    Öffentlicher Einstiegspunkt für den UndoManager (undo/redo einer
    Verschiebeaktion).

    Args:
        source: Zu verschiebende Quelle.
        target: Exakter Zielpfad (muss frei sein).

    Raises:
        OSError: Bei nicht behebbaren Dateisystemfehlern.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    _move_one(source, target)


def move_paths(
    sources: list[Path],
    destination: Path,
    collision_policy: CollisionPolicy = CollisionPolicy.RENAME,
    progress_callback: "callable[[int, int, str], None] | None" = None,
    cancel_check: "callable[[], bool] | None" = None,
) -> OperationResult:
    """Verschiebt eine Liste von Dateien/Ordnern in ein Zielverzeichnis.

    Versucht zunächst shutil.move (schnell bei gleichem Dateisystem,
    da meist ein reines Umbenennen). Schlägt dies fehl (z. B. weil
    Quelle und Ziel auf unterschiedlichen Laufwerken liegen), wird
    automatisch auf Kopieren + Löschen der Quelle zurückgefallen, was
    aus Nutzersicht identisch funktioniert.

    Args:
        sources: Zu verschiebende Quellpfade.
        destination: Zielverzeichnis. Wird bei Bedarf angelegt.
        collision_policy: Verhalten bei bereits vorhandenem Ziel.
        progress_callback: Optional aufgerufen als
            (erledigt, gesamt, aktueller_name) nach jedem Element.
        cancel_check: Optional aufgerufen vor jedem Element; liefert
            True zurück, wenn abgebrochen werden soll. Ein Abbruch
            wird erst zwischen zwei obersten Quellen wirksam, da ein
            einzelnes shutil.move nicht unterbrechbar ist.

    Returns:
        OperationResult mit Erfolgen, Übersprüngen und Fehlern.
    """
    result = OperationResult(operation=OperationType.MOVE)
    destination.mkdir(parents=True, exist_ok=True)

    total = max(len(sources), 1)
    done = 0

    for source in sources:
        if cancel_check is not None and cancel_check():
            result.cancelled = True
            break

        if not source.exists() and not source.is_symlink():
            result.failed.append((source, "Quelle existiert nicht mehr."))
            continue

        target = _resolve_target(source, destination, collision_policy)
        done += 1
        if target is None:
            result.skipped.append(source)
            if progress_callback is not None:
                progress_callback(done, total, source.name)
            continue

        try:
            _move_one(source, target)
            result.succeeded.append(target)
            result.succeeded_pairs.append((source, target))
        except OSError as fallback_error:
            logger.error(
                "Verschieben endgültig fehlgeschlagen: %s -> %s (%s)",
                source,
                target,
                fallback_error,
            )
            result.failed.append((source, str(fallback_error)))

        if progress_callback is not None:
            progress_callback(done, total, source.name)

    return result


def delete_paths(
    sources: list[Path],
    progress_callback: "callable[[int, int, str], None] | None" = None,
    cancel_check: "callable[[], bool] | None" = None,
) -> OperationResult:
    """Löscht eine Liste von Dateien/Ordnern endgültig.

    Jeder Eintrag wird einzeln behandelt: Schlägt das Löschen eines
    Eintrags fehl (z. B. fehlende Berechtigung), werden die übrigen
    Einträge trotzdem verarbeitet (fehlertolerant).

    Args:
        sources: Zu löschende Pfade.
        progress_callback: Optional aufgerufen als
            (erledigt, gesamt, aktueller_name) nach jedem Element.
        cancel_check: Optional aufgerufen vor jedem Element; liefert
            True zurück, wenn abgebrochen werden soll.

    Returns:
        OperationResult (skipped bleibt beim Löschen stets leer).
    """
    result = OperationResult(operation=OperationType.DELETE)
    total = max(len(sources), 1)
    done = 0

    for source in sources:
        if cancel_check is not None and cancel_check():
            result.cancelled = True
            break

        done += 1
        try:
            if not source.exists() and not source.is_symlink():
                result.failed.append((source, "Eintrag existiert nicht mehr."))
            else:
                _remove_path(source)
                result.succeeded.append(source)
        except OSError as error:
            logger.warning("Löschen fehlgeschlagen: %s (%s)", source, error)
            result.failed.append((source, str(error)))

        if progress_callback is not None:
            progress_callback(done, total, source.name)

    return result


def _unique_trash_slot(trash_root: Path, name: str) -> Path:
    """Findet einen freien Ablageplatz für name innerhalb von trash_root.

    Jede Löschung bekommt einen eigenen, mit Zeitstempel/Zähler
    eindeutigen Unterordner, damit mehrfach gelöschte gleichnamige
    Einträge sich im Papierkorb nicht gegenseitig überschreiben und
    ein Undo stets eindeutig auf "seinen" Papierkorb-Eintrag zeigt.

    Args:
        trash_root: Basisverzeichnis des internen Papierkorbs.
        name: Ursprünglicher Datei-/Ordnername.

    Returns:
        Ein innerhalb von trash_root noch unbenutzter Pfad.
    """
    import time

    slot_dir = trash_root / f"{int(time.time() * 1000)}"
    counter = 0
    while slot_dir.exists():
        counter += 1
        slot_dir = trash_root / f"{int(time.time() * 1000)}_{counter}"
    return slot_dir / name


def move_to_trash(
    sources: list[Path],
    trash_root: Path,
    progress_callback: "callable[[int, int, str], None] | None" = None,
    cancel_check: "callable[[], bool] | None" = None,
) -> OperationResult:
    """Verschiebt Dateien/Ordner in ein internes Papierkorb-Verzeichnis.

    Statt endgültig zu löschen, wird jeder oberste Eintrag in einen
    eigenen, eindeutigen Unterordner von trash_root verschoben. Das
    macht das Löschen über den UndoManager rückgängig machbar (siehe
    app.core.filesystem.undo_manager.DeleteAction) und ist damit die
    von der Oberfläche standardmäßig verwendete Löschimplementierung.

    Args:
        sources: Zu löschende (in den Papierkorb zu verschiebende)
            Pfade.
        trash_root: Basisverzeichnis des internen Papierkorbs. Wird
            bei Bedarf angelegt.
        progress_callback: Optional aufgerufen als
            (erledigt, gesamt, aktueller_name) nach jedem Element.
        cancel_check: Optional aufgerufen vor jedem Element; liefert
            True zurück, wenn abgebrochen werden soll.

    Returns:
        OperationResult mit succeeded (Originalpfade) und
        succeeded_pairs (Original-, Papierkorbpfad) je erfolgreich
        entsorgtem Element.
    """
    result = OperationResult(operation=OperationType.DELETE_TO_TRASH)
    trash_root.mkdir(parents=True, exist_ok=True)

    total = max(len(sources), 1)
    done = 0

    for source in sources:
        if cancel_check is not None and cancel_check():
            result.cancelled = True
            break

        done += 1
        if not source.exists() and not source.is_symlink():
            result.failed.append((source, "Eintrag existiert nicht mehr."))
            if progress_callback is not None:
                progress_callback(done, total, source.name)
            continue

        trash_target = _unique_trash_slot(trash_root, source.name)
        try:
            trash_target.parent.mkdir(parents=True, exist_ok=True)
            _move_one(source, trash_target)
            result.succeeded.append(source)
            result.succeeded_pairs.append((source, trash_target))
        except OSError as error:
            logger.warning("Verschieben in den Papierkorb fehlgeschlagen: %s (%s)", source, error)
            result.failed.append((source, str(error)))

        if progress_callback is not None:
            progress_callback(done, total, source.name)

    return result


def restore_from_trash(pairs: list[tuple[Path, Path]]) -> list[tuple[Path, Path]]:
    """Stellt zuvor in den Papierkorb verschobene Einträge wieder her.

    Args:
        pairs: Liste aus (ursprünglicher_pfad, papierkorb_pfad), wie
            von move_to_trash() in OperationResult.succeeded_pairs
            geliefert.

    Returns:
        Liste der tatsächlich wiederhergestellten (ursprünglicher_pfad,
        papierkorb_pfad)-Paare, in derselben Reihenfolge wie pairs
        (Einträge, die nicht mehr im Papierkorb gefunden wurden,
        fehlen).

    Raises:
        OSError: Wenn ein im Papierkorb vorhandener Eintrag nicht an
            seinen ursprünglichen Pfad zurückverschoben werden kann
            (z. B. weil dort inzwischen wieder ein Eintrag mit
            gleichem Namen existiert).
    """
    restored: list[tuple[Path, Path]] = []
    for original_path, trash_path in pairs:
        if not trash_path.exists() and not trash_path.is_symlink():
            logger.warning("Papierkorb-Eintrag fehlt, kann nicht wiederhergestellt werden: %s", trash_path)
            continue
        if original_path.exists() or original_path.is_symlink():
            raise OSError(
                f'Wiederherstellen nicht möglich: "{original_path}" existiert bereits wieder.'
            )
        original_path.parent.mkdir(parents=True, exist_ok=True)
        _move_one(trash_path, original_path)
        restored.append((original_path, trash_path))
    return restored


class FileOperationWorker(QThread):
    """Führt eine Datei-Kopier-/Verschiebe-/Löschoperation im Hintergrund aus.

    Läuft in einem eigenen QThread, damit die Oberfläche während
    langer Operationen (große Dateien, viele Elemente, langsame
    Netzlaufwerke) reaktionsfähig bleibt.

    Signals:
        progress_changed: (erledigt, gesamt, aktueller_name) nach
            jedem verarbeiteten Element.
        item_failed: (pfad_als_text, fehlermeldung) für jeden
            einzelnen fehlgeschlagenen Eintrag, zusätzlich zum
            gesammelten Ergebnis in operation_finished.
        operation_finished: Wird genau einmal am Ende mit dem
            vollständigen OperationResult gesendet – unabhängig davon,
            ob die Operation erfolgreich, teilweise fehlgeschlagen
            oder abgebrochen wurde.
    """

    progress_changed = pyqtSignal(int, int, str)
    item_failed = pyqtSignal(str, str)
    operation_finished = pyqtSignal(object)

    def __init__(
        self,
        operation: OperationType,
        sources: list[Path],
        destination: Path | None = None,
        collision_policy: CollisionPolicy = CollisionPolicy.RENAME,
        parent: object = None,
    ) -> None:
        """Initialisiert den Worker, ohne die Operation zu starten.

        Args:
            operation: Art der auszuführenden Operation.
            sources: Zu verarbeitende Quellpfade.
            destination: Zielverzeichnis. Erforderlich für COPY und
                MOVE (Zielordner) sowie DELETE_TO_TRASH
                (Papierkorb-Basisverzeichnis); wird für DELETE
                ignoriert.
            collision_policy: Verhalten bei bereits vorhandenem Ziel
                (nur relevant für COPY/MOVE).
            parent: Optionales Eltern-QObject.

        Raises:
            ValueError: Wenn destination bei COPY/MOVE/DELETE_TO_TRASH
                fehlt.
        """
        super().__init__(parent)
        _needs_destination = (
            OperationType.COPY,
            OperationType.MOVE,
            OperationType.DELETE_TO_TRASH,
        )
        if operation in _needs_destination and destination is None:
            raise ValueError(f"destination ist für {operation.value} erforderlich.")

        self._operation = operation
        self._sources = list(sources)
        self._destination = destination
        self._collision_policy = collision_policy
        self._cancel_requested = False

    def cancel(self) -> None:
        """Fordert einen Abbruch der laufenden Operation an.

        Der Abbruch wird spätestens vor dem nächsten Element wirksam
        und ist damit stets fehlertolerant nachvollziehbar (kein
        Datenverlust an bereits erfolgreich verarbeiteten Elementen).
        """
        self._cancel_requested = True

    def _is_cancelled(self) -> bool:
        return self._cancel_requested

    def _emit_progress(self, done: int, total: int, name: str) -> None:
        self.progress_changed.emit(done, total, name)

    def run(self) -> None:  # noqa: D102 - Qt-Standard-Hook, siehe Klassendoku
        logger.info(
            "Starte Dateioperation %s für %d Element(e)",
            self._operation.value,
            len(self._sources),
        )

        if self._operation == OperationType.COPY:
            result = copy_paths(
                self._sources,
                self._destination,  # type: ignore[arg-type]
                self._collision_policy,
                self._emit_progress,
                self._is_cancelled,
            )
        elif self._operation == OperationType.MOVE:
            result = move_paths(
                self._sources,
                self._destination,  # type: ignore[arg-type]
                self._collision_policy,
                self._emit_progress,
                self._is_cancelled,
            )
        elif self._operation == OperationType.DELETE_TO_TRASH:
            result = move_to_trash(
                self._sources,
                self._destination,  # type: ignore[arg-type]
                self._emit_progress,
                self._is_cancelled,
            )
        else:
            result = delete_paths(
                self._sources,
                self._emit_progress,
                self._is_cancelled,
            )

        for failed_path, message in result.failed:
            self.item_failed.emit(str(failed_path), message)

        logger.info(
            "Dateioperation %s beendet: %s",
            self._operation.value,
            result.summary_text(),
        )
        self.operation_finished.emit(result)
