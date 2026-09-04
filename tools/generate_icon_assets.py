#!/usr/bin/env python3
"""Pandora® Commander – Icon-Asset-Generator.

Erzeugt gerasterte PNG-Varianten des Anwendungs-Icons für den
freedesktop Icon-Theme-Pfad ``hicolor/<size>x<size>/apps`` aus dem
Vektor-Referenzdesign in
``app/resources/icons/hicolor/scalable/apps/pandora-commander.svg``.

Es wird bewusst *nicht* auf einen externen SVG-Rasterizer (z. B.
``rsvg-convert``, ``cairosvg``, ``Inkscape``) zurückgegriffen, da diese
auf einem frischen Raspberry-Pi/Kali-System nicht garantiert vorhanden
sind. Stattdessen zeichnet dieses Skript dasselbe Design direkt mit
Pillow – ohne zusätzliche Systemabhängigkeiten.

Verwendung:
    python tools/generate_icon_assets.py

Das Skript ist idempotent und kann nach jeder Änderung am Icon-Design
erneut ausgeführt werden, um alle Rastergrößen neu zu erzeugen.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

# ---------------------------------------------------------------------------
# Farbschema, abgestimmt auf app.themes.dark_theme / app.utils.icon_provider
# ---------------------------------------------------------------------------

_BG_TOP = (26, 29, 36)
_BG_BOTTOM = (16, 18, 23)
_PANEL_TOP = (35, 40, 48)
_PANEL_BOTTOM = (27, 31, 38)
_BORDER = (52, 59, 70)
_ACCENT = (91, 141, 239)
_MUTED = (74, 82, 97)
_MUTED_LIGHT = (154, 157, 162)
_SUCCESS = (63, 185, 80)
_DANGER = (229, 72, 77)

_SIZES: tuple[int, ...] = (16, 24, 32, 48, 64, 128, 256, 512)

_OUTPUT_ROOT = (
    Path(__file__).resolve().parent.parent
    / "app"
    / "resources"
    / "icons"
    / "hicolor"
)


def _vgradient(size: tuple[int, int], top: tuple[int, int, int], bottom: tuple[int, int, int]) -> Image.Image:
    """Erzeugt eine einfache vertikale Farbverlaufs-Fläche."""
    width, height = size
    gradient = Image.new("RGB", (1, height))
    for y in range(height):
        t = y / max(height - 1, 1)
        pixel = tuple(round(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
        gradient.putpixel((0, y), pixel)
    return gradient.resize((width, height))


def _scaled(base: int, value: float) -> int:
    """Skaliert eine im 256er-Referenzraster definierte Koordinate."""
    return round(value * base / 256)


def _render(size: int) -> Image.Image:
    """Rendert das Pandora® Commander-Icon in der gegebenen Kantenlänge."""
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))

    # Hintergrund: abgerundetes Quadrat mit vertikalem Farbverlauf.
    bg_mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(bg_mask).rounded_rectangle(
        [_scaled(size, 8), _scaled(size, 8), _scaled(size, 248), _scaled(size, 248)],
        radius=max(_scaled(size, 40), 2),
        fill=255,
    )
    bg_fill = _vgradient((size, size), _BG_TOP, _BG_BOTTOM).convert("RGBA")
    canvas.paste(bg_fill, (0, 0), bg_mask)

    draw = ImageDraw.Draw(canvas, "RGBA")

    # Dezenter Neon-Rand um den Hintergrund.
    draw.rounded_rectangle(
        [_scaled(size, 8), _scaled(size, 8), _scaled(size, 248), _scaled(size, 248)],
        radius=max(_scaled(size, 40), 2),
        outline=(*_ACCENT, 150),
        width=max(_scaled(size, 2), 1),
    )

    def panel(x0: float, y0: float, x1: float, y1: float) -> None:
        mask = Image.new("L", (size, size), 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            [_scaled(size, x0), _scaled(size, y0), _scaled(size, x1), _scaled(size, y1)],
            radius=max(_scaled(size, 12), 1),
            fill=255,
        )
        fill = _vgradient((size, size), _PANEL_TOP, _PANEL_BOTTOM).convert("RGBA")
        canvas.paste(fill, (0, 0), mask)
        draw.rounded_rectangle(
            [_scaled(size, x0), _scaled(size, y0), _scaled(size, x1), _scaled(size, y1)],
            radius=max(_scaled(size, 12), 1),
            outline=(*_BORDER, 255),
            width=max(_scaled(size, 1), 1),
        )

    panel(28, 40, 118, 216)
    panel(138, 40, 228, 216)

    # Neon-Trennlinie zwischen den Panes (weicher Glow durch mehrfaches
    # Überzeichnen mit sinkender Deckkraft, statt einer echten Gauß-
    # Unschärfe – ausreichend für kleine Icon-Größen).
    line_x = _scaled(size, 128)
    for width_px, alpha in ((7, 40), (5, 70), (3, 140), (2, 255)):
        w = max(_scaled(size, width_px), 1)
        draw.line(
            [(line_x, _scaled(size, 36)), (line_x, _scaled(size, 220))],
            fill=(*_ACCENT, alpha),
            width=w,
        )

    def row(x: float, y: float, w: float, color: tuple[int, int, int], alpha: int = 255) -> None:
        h = 6
        draw.rounded_rectangle(
            [_scaled(size, x), _scaled(size, y), _scaled(size, x + w), _scaled(size, y + h)],
            radius=max(_scaled(size, 3), 1),
            fill=(*color, alpha),
        )

    # Fokus-Markierung im aktiven (linken) Pane.
    draw.rounded_rectangle(
        [_scaled(size, 36), _scaled(size, 55), _scaled(size, 112), _scaled(size, 69)],
        radius=max(_scaled(size, 4), 1),
        fill=(*_ACCENT, 40),
    )

    # Dateizeilen linkes Panel.
    row(40, 58, 66, _ACCENT)
    row(40, 76, 52, _MUTED)
    row(40, 90, 58, _MUTED)
    row(40, 104, 44, _SUCCESS, 220)
    row(40, 118, 60, _MUTED)

    # Dateizeilen rechtes Panel.
    row(150, 58, 66, _MUTED_LIGHT)
    row(150, 76, 60, _MUTED)
    row(150, 90, 46, _DANGER, 200)
    row(150, 104, 58, _MUTED)
    row(150, 118, 50, _MUTED)

    # Statusleisten unten.
    draw.rounded_rectangle(
        [_scaled(size, 28), _scaled(size, 196), _scaled(size, 118), _scaled(size, 200)],
        radius=max(_scaled(size, 2), 1),
        fill=(*_ACCENT, 100),
    )
    draw.rounded_rectangle(
        [_scaled(size, 138), _scaled(size, 196), _scaled(size, 228), _scaled(size, 200)],
        radius=max(_scaled(size, 2), 1),
        fill=(*_ACCENT, 100),
    )

    return canvas


def generate_all(sizes: tuple[int, ...] = _SIZES, output_root: Path = _OUTPUT_ROOT) -> list[Path]:
    """Rendert und speichert alle konfigurierten Icon-Größen.

    Args:
        sizes: Zu erzeugende quadratische Kantenlängen in Pixeln.
        output_root: Basisverzeichnis des hicolor-Icon-Themes.

    Returns:
        Liste der geschriebenen PNG-Dateipfade.
    """
    written: list[Path] = []
    for size in sizes:
        target_dir = output_root / f"{size}x{size}" / "apps"
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / "pandora-commander.png"
        _render(size).save(target_path, format="PNG")
        written.append(target_path)
    return written


def main() -> int:
    written = generate_all()
    for path in written:
        print(f"geschrieben: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
