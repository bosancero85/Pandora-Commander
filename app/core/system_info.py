"""Pandora® Commander – Systeminformationen.

Ermittelt Plattform-, CPU-, Arbeitsspeicher- und Datenträger-
informationen für das Dashboard (siehe app.ui.dialogs.dashboard_dialog).

Bewusst ohne Zusatzabhängigkeit wie psutil umgesetzt, damit das
Dashboard auf jeder Standardinstallation sofort funktioniert – analog
zum bisherigen Beispiel-Plugin "Systeminformationen". Für den
Arbeitsspeicher wird je nach Plattform ein natives Vorgehen genutzt
(``/proc/meminfo`` unter Linux, ``GlobalMemoryStatusEx`` unter Windows,
``vm_stat``/``sysctl`` unter macOS); schlägt die Ermittlung fehl (z. B.
unbekannte Plattform, Sandbox-Einschränkungen), werden 0-Werte
zurückgegeben statt die Anwendung abstürzen zu lassen.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from app.core.logging_setup import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class MemoryInfo:
    """Arbeitsspeicher-Belegung in Bytes.

    Attributes:
        total: Gesamter physischer Arbeitsspeicher in Bytes.
        used: Aktuell belegter Arbeitsspeicher in Bytes.
    """

    total: int = 0
    used: int = 0

    @property
    def percent_used(self) -> float:
        """Belegter Anteil in Prozent (0.0, wenn total unbekannt ist)."""
        return (self.used / self.total * 100.0) if self.total > 0 else 0.0


@dataclass(frozen=True)
class DiskInfo:
    """Datenträger-Belegung für einen einzelnen Pfad.

    Attributes:
        label: Anzeigename (z. B. der Pfad selbst).
        total: Gesamtgröße in Bytes.
        used: Belegter Speicher in Bytes.
        free: Freier Speicher in Bytes.
    """

    label: str
    total: int
    used: int
    free: int

    @property
    def percent_used(self) -> float:
        """Belegter Anteil in Prozent (0.0, wenn total unbekannt ist)."""
        return (self.used / self.total * 100.0) if self.total > 0 else 0.0


def get_os_info() -> str:
    """Liefert eine kurze, menschenlesbare Beschreibung des Betriebssystems."""
    return f"{platform.system()} {platform.release()} ({platform.machine()})"


def get_python_info() -> str:
    """Liefert die verwendete Python-Version inkl. Implementierung."""
    return f"Python {sys.version.split()[0]} ({platform.python_implementation()})"


def get_cpu_count() -> int:
    """Liefert die Anzahl logischer CPU-Kerne (mind. 1)."""
    return os.cpu_count() or 1


def get_memory_info() -> MemoryInfo:
    """Ermittelt Gesamt- und belegten Arbeitsspeicher, plattformabhängig.

    Returns:
        MemoryInfo mit total=used=0, falls die Ermittlung auf der
        aktuellen Plattform nicht möglich war.
    """
    system = platform.system()
    try:
        if system == "Linux":
            return _memory_info_linux()
        if system == "Windows":
            return _memory_info_windows()
        if system == "Darwin":
            return _memory_info_macos()
    except Exception:  # noqa: BLE001 - Speicherermittlung darf nie abstürzen
        logger.exception("Arbeitsspeicher konnte nicht ermittelt werden.")
    return MemoryInfo()


def _memory_info_linux() -> MemoryInfo:
    """Liest Speicherwerte aus ``/proc/meminfo`` (KiB-Angaben)."""
    values: dict[str, int] = {}
    meminfo_path = Path("/proc/meminfo")
    if not meminfo_path.exists():
        return MemoryInfo()

    for line in meminfo_path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"(\w+):\s+(\d+)\s*kB", line)
        if match:
            values[match.group(1)] = int(match.group(2)) * 1024

    total = values.get("MemTotal", 0)
    # MemAvailable ist die vom Kernel geschätzte, tatsächlich für neue
    # Prozesse verfügbare Menge (berücksichtigt reclaimbare Caches) –
    # deutlich aussagekräftiger als MemFree allein.
    available = values.get("MemAvailable", values.get("MemFree", 0))
    used = max(total - available, 0)
    return MemoryInfo(total=total, used=used)


def _memory_info_windows() -> MemoryInfo:
    """Ermittelt Speicherwerte über die Win32-API ``GlobalMemoryStatusEx``."""
    import ctypes

    class _MemoryStatusEx(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    status = _MemoryStatusEx()
    status.dwLength = ctypes.sizeof(_MemoryStatusEx)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))  # type: ignore[attr-defined]
    total = int(status.ullTotalPhys)
    used = total - int(status.ullAvailPhys)
    return MemoryInfo(total=total, used=used)


def _memory_info_macos() -> MemoryInfo:
    """Ermittelt Speicherwerte über ``sysctl`` und ``vm_stat``."""
    total_output = subprocess.run(
        ["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=3, check=False
    )
    total = int(total_output.stdout.strip() or 0)

    vm_stat_output = subprocess.run(
        ["vm_stat"], capture_output=True, text=True, timeout=3, check=False
    )
    page_size = 4096
    pages_used = 0
    for line in vm_stat_output.stdout.splitlines():
        size_match = re.search(r"page size of (\d+) bytes", line)
        if size_match:
            page_size = int(size_match.group(1))
        for label in ("Pages active", "Pages wired down", "Pages occupied by compressor"):
            if line.startswith(label):
                count_match = re.search(r"(\d+)", line)
                if count_match:
                    pages_used += int(count_match.group(1))

    used = pages_used * page_size
    return MemoryInfo(total=total, used=min(used, total) if total else used)


def get_disk_info(path: Path) -> DiskInfo | None:
    """Liefert die Datenträger-Belegung für den Datenträger von path.

    Args:
        path: Beliebiger Pfad auf dem gewünschten Datenträger (z. B.
            das aktuell angezeigte Verzeichnis eines Panels).

    Returns:
        DiskInfo, oder None, falls der Pfad nicht (mehr) zugreifbar ist
        (z. B. getrenntes Netzlaufwerk).
    """
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return None
    return DiskInfo(label=str(path), total=usage.total, used=usage.used, free=usage.free)
