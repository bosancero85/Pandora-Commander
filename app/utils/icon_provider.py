"""Pandora® Commander – Icon-Provider.

Stellt das vollständige Satz an SVG-Symbolleisten-/Menü-Icons der
Anwendung bereit. Die Icons werden bewusst als eingebettete SVG-
Zeichenketten gehalten (kein separates Asset-Verzeichnis mit
Binärdateien nötig) und im typischen dünnen Outline-Stil im Pandora-
Farbschema gezeichnet, sodass sie sich nahtlos in das dunkle Theme
(``app.themes.dark_theme``) einfügen.

Verwendung:
    from app.utils.icon_provider import get_icon

    action = QAction(get_icon("copy"), "Kopieren …", self)

Für High-DPI-Bildschirme wird jedes Icon in mehreren Pixelgrößen
gerendert und über ``QIcon.addPixmap`` zusammengeführt, statt eine
einzelne Rastergröße hochzuskalieren.
"""

from __future__ import annotations

from PyQt6.QtCore import QByteArray, QRectF, Qt
from PyQt6.QtGui import QIcon, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer

#: Farbschema, abgestimmt auf app.themes.dark_theme.PandoraPalette.
_STROKE = "#e6e6e6"
_STROKE_MUTED = "#9a9da2"
_ACCENT = "#5b8def"
_DANGER = "#e5484d"
_SUCCESS = "#3fb950"

#: Pixelgrößen, in denen jedes Icon für High-DPI-Displays vorgerendert wird.
_RENDER_SIZES: tuple[int, ...] = (16, 24, 32, 48, 64)

_SVG_TEMPLATE = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
    'fill="none" stroke="{stroke}" stroke-width="1.6" '
    'stroke-linecap="round" stroke-linejoin="round">{body}</svg>'
)

# ---------------------------------------------------------------------------
# Icon-Definitionen (Name -> SVG-Body im 24x24-Koordinatensystem)
# ---------------------------------------------------------------------------

_ICON_BODIES: dict[str, str] = {
    "new_folder": (
        '<path d="M3 6.5A1.5 1.5 0 0 1 4.5 5h4.4l1.6 2H19.5A1.5 1.5 0 0 1 21 8.5v9A1.5 1.5 0 0 1 19.5 19h-15A1.5 1.5 0 0 1 3 17.5z"/>'
        '<path stroke="{accent}" d="M12 10.5v5M9.5 13h5"/>'
    ),
    "copy": (
        '<rect x="8" y="8" width="12" height="12" rx="1.5"/>'
        '<path d="M16 8V5.5A1.5 1.5 0 0 0 14.5 4h-9A1.5 1.5 0 0 0 4 5.5v9A1.5 1.5 0 0 0 5.5 16H8"/>'
    ),
    "move": (
        '<rect x="4" y="6" width="16" height="13" rx="1.5"/>'
        '<path stroke="{accent}" d="M9 12h6M12.5 9.5 15 12l-2.5 2.5"/>'
    ),
    "delete": (
        '<path stroke="{danger}" d="M5 7h14"/>'
        '<path stroke="{danger}" d="M9 7V5.5A1.5 1.5 0 0 1 10.5 4h3A1.5 1.5 0 0 1 15 5.5V7"/>'
        '<path stroke="{danger}" d="M6.5 7 7.3 19a1.5 1.5 0 0 0 1.5 1.4h6.4a1.5 1.5 0 0 0 1.5-1.4L17.5 7"/>'
        '<path stroke="{danger}" d="M10.3 11v6M13.7 11v6"/>'
    ),
    "rename": (
        '<path d="M4 19.5 4.7 16.6 15.4 5.9a1.4 1.4 0 0 1 2 0l1.7 1.7a1.4 1.4 0 0 1 0 2L8.4 20.3z"/>'
        '<path d="M13.8 7.5l2.7 2.7"/>'
    ),
    "properties": (
        '<circle cx="12" cy="12" r="8.5"/>'
        '<path stroke="{accent}" d="M12 11v5.5"/>'
        '<circle cx="12" cy="8" r="0.9" fill="{accent}" stroke="none"/>'
    ),
    "refresh": (
        '<path d="M5 12a7 7 0 0 1 12-4.9l1.5 1.4"/>'
        '<path d="M18.5 5.5v3.5H15"/>'
        '<path d="M19 12a7 7 0 0 1-12 4.9l-1.5-1.4"/>'
        '<path d="M5.5 18.5V15H9"/>'
    ),
    "update": (
        '<path d="M7 16.5a4.5 4.5 0 0 1 .8-8.9 5.5 5.5 0 0 1 10.6 1.6 3.8 3.8 0 0 1-.9 7.3H7z"/>'
        '<path stroke="{accent}" d="M12 10v6.5"/>'
        '<path stroke="{accent}" d="M9.3 13.7 12 16.5l2.7-2.8"/>'
    ),
    "dashboard": (
        '<rect x="3.5" y="3.5" width="7.5" height="7.5" rx="1.2"/>'
        '<rect x="13" y="3.5" width="7.5" height="4.5" rx="1.2" stroke="{accent}"/>'
        '<rect x="13" y="10" width="7.5" height="10.5" rx="1.2"/>'
        '<rect x="3.5" y="13" width="7.5" height="7.5" rx="1.2" stroke="{accent}"/>'
    ),
    "editor": (
        '<path d="M6 4.5h9L19.5 9v10.5a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V5.5a1 1 0 0 1 1-1z"/>'
        '<path d="M15 4.5V9h4.5"/>'
        '<path stroke="{accent}" d="M8.5 12.5h7M8.5 15.5h7M8.5 18h4.5"/>'
    ),
    "preview": (
        '<path d="M2.5 12S5.8 6 12 6s9.5 6 9.5 6-3.3 6-9.5 6-9.5-6-9.5-6z"/>'
        '<circle cx="12" cy="12" r="2.6" stroke="{accent}"/>'
    ),
    "search": (
        '<circle cx="10.5" cy="10.5" r="6"/>'
        '<path stroke="{accent}" d="M15 15l5.5 5.5"/>'
    ),
    "favorites": (
        '<path fill="{accent_fill}" stroke="{accent}" d="M12 4.8l2.2 4.6 5 .7-3.6 3.6.9 5-4.5-2.4-4.5 2.4.9-5-3.6-3.6 5-.7z"/>'
    ),
    "terminal": (
        '<rect x="3.5" y="4.5" width="17" height="15" rx="1.5"/>'
        '<path stroke="{success}" d="M7 9.5 10.5 12 7 14.5"/>'
        '<path stroke="{success}" d="M12 14.5h5"/>'
    ),
    "hash": (
        '<path d="M9 4.5 6.5 19.5M17.5 4.5 15 19.5M4 9h16M3.5 15h16"/>'
    ),
    "compare": (
        '<path d="M9 4.5v15M9 4.5c-2.8 0-5 2.7-5 6v3c0 3.3 2.2 6 5 6"/>'
        '<path stroke="{accent}" d="M15 4.5v15M15 4.5c2.8 0 5 2.7 5 6v3c0 3.3-2.2 6-5 6"/>'
    ),
    "archive_create": (
        '<rect x="4" y="4" width="16" height="16" rx="1.5"/>'
        '<path d="M9.5 4v16"/>'
        '<path d="M9.5 6.5h2M9.5 9.5h2M9.5 12.5h2"/>'
        '<path stroke="{accent}" d="M15 12v6M12.2 15.2 15 18l2.8-2.8"/>'
    ),
    "archive_extract": (
        '<rect x="4" y="4" width="16" height="16" rx="1.5"/>'
        '<path d="M9.5 4v16"/>'
        '<path d="M9.5 6.5h2M9.5 9.5h2M9.5 12.5h2"/>'
        '<path stroke="{accent}" d="M15 18v-6M12.2 14.8 15 12l2.8 2.8"/>'
    ),
    "plugin": (
        '<rect x="4" y="4" width="7" height="7" rx="1.4"/>'
        '<rect x="13" y="4" width="7" height="7" rx="1.4"/>'
        '<rect x="4" y="13" width="7" height="7" rx="1.4"/>'
        '<path stroke="{accent}" d="M15.2 13.2v2.1h2.1a1.7 1.7 0 1 1 0 3.4h-2.1v2.1'
        'a1.7 1.7 0 1 1-3.4 0v-2.1H9.7a1.7 1.7 0 1 1 0-3.4h2.1v-2.1a1.7 1.7 0 1 1 3.4 0z"/>'
    ),
    "network": (
        '<circle cx="12" cy="5.5" r="2"/>'
        '<circle cx="5.5" cy="18" r="2"/>'
        '<circle cx="18.5" cy="18" r="2"/>'
        '<path stroke="{accent}" d="M12 7.5v4M12 11.5 6.6 16.4M12 11.5l5.4 4.9"/>'
    ),
    "settings": (
        '<circle cx="12" cy="12" r="3"/>'
        '<path stroke="{accent}" d="M12 3v2.2M12 18.8V21M21 12h-2.2M5.2 12H3'
        'M18.4 5.6l-1.5 1.5M7.1 16.9l-1.5 1.5M18.4 18.4l-1.5-1.5M7.1 7.1 5.6 5.6"/>'
    ),
    "switch_panel": (
        '<rect x="3" y="5" width="8" height="14" rx="1.2"/>'
        '<rect x="13" y="5" width="8" height="14" rx="1.2"/>'
        '<path stroke="{accent}" d="M9 9.5 6.5 12 9 14.5M15 9.5 17.5 12 15 14.5"/>'
    ),
    "toggle_hidden": (
        '<path d="M2.5 12S5.8 6.5 12 6.5 21.5 12 21.5 12 18.2 17.5 12 17.5 2.5 12 2.5 12z"/>'
        '<circle cx="12" cy="12" r="2.4" stroke="{accent}"/>'
        '<path stroke="{danger}" d="M4 4l16 16"/>'
    ),
    "app_icon": (
        '<circle cx="12" cy="12" r="9" stroke="{accent}"/>'
        '<path d="M8 15V8.5l7 3.25-7 3.25z" fill="{accent_fill}" stroke="{accent}"/>'
    ),
    "quit": (
        '<path d="M9.5 4.5h-4A1.5 1.5 0 0 0 4 6v12a1.5 1.5 0 0 0 1.5 1.5h4"/>'
        '<path stroke="{danger}" d="M13 8l4.5 4-4.5 4"/>'
        '<path stroke="{danger}" d="M17 12H9.5"/>'
    ),
    "undo": (
        '<path stroke="{accent}" d="M7 8H16.5A4.5 4.5 0 0 1 21 12.5v0'
        'A4.5 4.5 0 0 1 16.5 17H10"/>'
        '<path d="M10.5 4.5 6 8l4.5 3.5"/>'
    ),
    "redo": (
        '<path stroke="{accent}" d="M17 8H7.5A4.5 4.5 0 0 0 3 12.5v0'
        'A4.5 4.5 0 0 0 7.5 17H14"/>'
        '<path d="M13.5 4.5 18 8l-4.5 3.5"/>'
    ),
    "queue": (
        '<rect x="3.5" y="5" width="17" height="4" rx="1"/>'
        '<rect x="3.5" y="10" width="17" height="4" rx="1"/>'
        '<rect stroke="{accent}" x="3.5" y="15" width="10" height="4" rx="1"/>'
    ),
}

_icon_cache: dict[str, QIcon] = {}


def _resolve_body(name: str) -> str:
    """Setzt die Farbplatzhalter eines Icon-Bodys in konkrete Farbwerte ein."""
    body = _ICON_BODIES[name]
    return body.format(
        accent=_ACCENT,
        accent_fill=_ACCENT + "33",
        danger=_DANGER,
        success=_SUCCESS,
    )


def _render_pixmap(svg_data: bytes, size: int) -> QPixmap:
    """Rendert SVG-Daten verlustfrei in eine quadratische Pixmap.

    Args:
        svg_data: UTF-8-kodierte SVG-Zeichenkette.
        size: Kantenlänge der Zielpixmap in Pixeln.

    Returns:
        Eine transparente Pixmap mit dem gerenderten Icon.
    """
    renderer = QSvgRenderer(QByteArray(svg_data))
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    try:
        renderer.render(painter, QRectF(0, 0, size, size))
    finally:
        painter.end()
    return pixmap


def get_icon(name: str) -> QIcon:
    """Liefert das benannte Icon als fertiges, High-DPI-fähiges QIcon.

    Ergebnisse werden pro Icon-Name zwischengespeichert, da das
    wiederholte Rendern identischer SVGs unnötig wäre.

    Args:
        name: Schlüssel aus dem internen Icon-Set (z. B. "copy",
            "delete", "search" …).

    Returns:
        Ein QIcon mit vorgerenderten Pixmaps in mehreren Größen.

    Raises:
        KeyError: Wenn kein Icon mit diesem Namen existiert.
    """
    if name in _icon_cache:
        return _icon_cache[name]

    if name not in _ICON_BODIES:
        raise KeyError(f"Unbekanntes Icon: '{name}'")

    svg_markup = _SVG_TEMPLATE.format(stroke=_STROKE, body=_resolve_body(name))
    svg_bytes = svg_markup.encode("utf-8")

    icon = QIcon()
    for size in _RENDER_SIZES:
        icon.addPixmap(_render_pixmap(svg_bytes, size))

    _icon_cache[name] = icon
    return icon


def available_icons() -> list[str]:
    """Gibt alle verfügbaren Icon-Namen sortiert zurück (z. B. für Debug/Tests)."""
    return sorted(_ICON_BODIES.keys())
