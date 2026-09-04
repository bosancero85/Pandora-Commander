"""Pandora® Commander – dunkles Fluent-Design-Theme.

Definiert die Farbpalette und das Qt-Stylesheet (QSS) für das
Standard-Dunkeltheme der Anwendung. Das Stylesheet wird zentral über
apply_dark_theme() auf eine QApplication angewendet.

Der Aufbau ist bewusst als Python-Modul (nicht als statische .qss-
Datei) gehalten, damit die Farbpalette als typisierte Konstanten
wiederverwendet werden kann (z. B. für programmatisch gezeichnete
Icons oder Diagramme in späteren Dateien).
"""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtWidgets import QApplication


@dataclass(frozen=True)
class DarkPalette:
    """Farbpalette des dunklen Fluent-Themes.

    Attributes:
        background: Haupt-Hintergrundfarbe (Fenster, Panels).
        surface: Hintergrundfarbe abgesetzter Flächen (Karten, Popups).
        surface_alt: Leicht abweichende Flächenfarbe für Streifenmuster.
        border: Standard-Rahmenfarbe.
        text_primary: Primäre Textfarbe.
        text_secondary: Gedämpfte Textfarbe (Hinweise, Statusleiste).
        accent: Akzentfarbe für aktive/markierte Elemente.
        accent_hover: Akzentfarbe bei Hover.
        accent_pressed: Akzentfarbe bei gedrücktem Zustand.
        danger: Farbe für destruktive Aktionen (Löschen, Fehler).
        success: Farbe für Erfolgsmeldungen.
        warning: Farbe für Warnungen.
    """

    background: str = "#1e1f22"
    surface: str = "#26282b"
    surface_alt: str = "#2c2e32"
    border: str = "#3a3d42"
    text_primary: str = "#e6e6e6"
    text_secondary: str = "#9a9da2"
    accent: str = "#5b8def"
    accent_hover: str = "#6f9bf2"
    accent_pressed: str = "#4a76d1"
    danger: str = "#e5484d"
    success: str = "#3fb950"
    warning: str = "#e3a008"


#: Aktive Palette – zentraler Zugriffspunkt für andere Module, die
#: Farben außerhalb von QSS benötigen (z. B. für Icon-Einfärbung).
PALETTE = DarkPalette()


def _build_stylesheet(palette: DarkPalette) -> str:
    """Baut das vollständige QSS-Stylesheet aus einer Palette.

    Args:
        palette: Die zu verwendende Farbpalette.

    Returns:
        Ein vollständiger QSS-String, bereit für setStyleSheet().
    """
    return f"""
    /* ---------- Basis ---------- */
    QWidget {{
        background-color: {palette.background};
        color: {palette.text_primary};
        font-size: 10pt;
        selection-background-color: {palette.accent};
        selection-color: #ffffff;
    }}

    QMainWindow, QDialog {{
        background-color: {palette.background};
    }}

    /* ---------- Menüleiste ---------- */
    QMenuBar {{
        background-color: {palette.surface};
        border-bottom: 1px solid {palette.border};
        padding: 2px;
    }}
    QMenuBar::item {{
        background: transparent;
        padding: 4px 10px;
        border-radius: 6px;
    }}
    QMenuBar::item:selected {{
        background-color: {palette.surface_alt};
    }}
    QMenu {{
        background-color: {palette.surface};
        border: 1px solid {palette.border};
        border-radius: 8px;
        padding: 4px;
    }}
    QMenu::item {{
        padding: 6px 24px 6px 12px;
        border-radius: 6px;
    }}
    QMenu::item:selected {{
        background-color: {palette.accent};
        color: #ffffff;
    }}
    QMenu::separator {{
        height: 1px;
        background: {palette.border};
        margin: 4px 6px;
    }}

    /* ---------- Symbolleiste ---------- */
    QToolBar {{
        background-color: {palette.surface};
        border: none;
        padding: 6px;
        spacing: 6px;
    }}
    QToolButton {{
        background-color: transparent;
        border: 1px solid transparent;
        border-radius: 8px;
        padding: 6px;
    }}
    QToolButton:hover {{
        background-color: {palette.surface_alt};
        border: 1px solid {palette.border};
    }}
    QToolButton:pressed {{
        background-color: {palette.accent_pressed};
    }}

    /* ---------- Buttons ---------- */
    QPushButton {{
        background-color: {palette.surface_alt};
        border: 1px solid {palette.border};
        border-radius: 8px;
        padding: 6px 16px;
    }}
    QPushButton:hover {{
        background-color: {palette.accent_hover};
        border: 1px solid {palette.accent_hover};
        color: #ffffff;
    }}
    QPushButton:pressed {{
        background-color: {palette.accent_pressed};
    }}
    QPushButton:disabled {{
        color: {palette.text_secondary};
        background-color: {palette.surface};
        border: 1px solid {palette.border};
    }}
    QPushButton#dangerButton {{
        background-color: {palette.danger};
        border: 1px solid {palette.danger};
        color: #ffffff;
    }}

    /* ---------- Eingabefelder ---------- */
    QLineEdit, QTextEdit, QPlainTextEdit {{
        background-color: {palette.surface};
        border: 1px solid {palette.border};
        border-radius: 6px;
        padding: 4px 8px;
        selection-background-color: {palette.accent};
    }}
    QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {{
        border: 1px solid {palette.accent};
    }}

    /* ---------- Listen / Bäume / Tabellen ---------- */
    QTreeView, QListView, QTableView {{
        background-color: {palette.surface};
        alternate-background-color: {palette.surface_alt};
        border: 1px solid {palette.border};
        border-radius: 8px;
        gridline-color: {palette.border};
    }}
    QTreeView::item, QListView::item {{
        padding: 3px;
        border-radius: 4px;
    }}
    QTreeView::item:selected, QListView::item:selected,
    QTableView::item:selected {{
        background-color: {palette.accent};
        color: #ffffff;
    }}
    QHeaderView::section {{
        background-color: {palette.surface_alt};
        color: {palette.text_secondary};
        padding: 6px;
        border: none;
        border-bottom: 1px solid {palette.border};
    }}

    /* ---------- Tabs ---------- */
    QTabWidget::pane {{
        border: 1px solid {palette.border};
        border-radius: 8px;
        top: -1px;
    }}
    QTabBar::tab {{
        background-color: {palette.surface};
        color: {palette.text_secondary};
        padding: 6px 16px;
        margin-right: 2px;
        border-top-left-radius: 8px;
        border-top-right-radius: 8px;
    }}
    QTabBar::tab:selected {{
        background-color: {palette.surface_alt};
        color: {palette.text_primary};
        border-bottom: 2px solid {palette.accent};
    }}
    QTabBar::tab:hover:!selected {{
        background-color: {palette.surface_alt};
    }}
    QTabBar::close-button {{
        subcontrol-position: right;
    }}

    /* ---------- Statusleiste ---------- */
    QStatusBar {{
        background-color: {palette.surface};
        border-top: 1px solid {palette.border};
        color: {palette.text_secondary};
    }}

    /* ---------- Scrollbalken ---------- */
    QScrollBar:vertical {{
        background: transparent;
        width: 10px;
        margin: 2px;
    }}
    QScrollBar::handle:vertical {{
        background: {palette.border};
        border-radius: 5px;
        min-height: 24px;
    }}
    QScrollBar::handle:vertical:hover {{
        background: {palette.accent};
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0px;
    }}
    QScrollBar:horizontal {{
        background: transparent;
        height: 10px;
        margin: 2px;
    }}
    QScrollBar::handle:horizontal {{
        background: {palette.border};
        border-radius: 5px;
        min-width: 24px;
    }}
    QScrollBar::handle:horizontal:hover {{
        background: {palette.accent};
    }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
        width: 0px;
    }}

    /* ---------- Splitter ---------- */
    QSplitter::handle {{
        background-color: {palette.border};
    }}
    QSplitter::handle:horizontal {{
        width: 2px;
    }}
    QSplitter::handle:vertical {{
        height: 2px;
    }}

    /* ---------- Tooltips ---------- */
    QToolTip {{
        background-color: {palette.surface_alt};
        color: {palette.text_primary};
        border: 1px solid {palette.border};
        border-radius: 6px;
        padding: 4px 8px;
    }}

    /* ---------- Kontextmenü-Checkboxen / Radiobuttons ---------- */
    QCheckBox::indicator, QRadioButton::indicator {{
        width: 16px;
        height: 16px;
    }}

    /* ---------- Combobox ---------- */
    QComboBox {{
        background-color: {palette.surface};
        border: 1px solid {palette.border};
        border-radius: 6px;
        padding: 4px 8px;
    }}
    QComboBox:hover {{
        border: 1px solid {palette.accent};
    }}
    QComboBox QAbstractItemView {{
        background-color: {palette.surface};
        border: 1px solid {palette.border};
        selection-background-color: {palette.accent};
    }}

    /* ---------- Progressbar ---------- */
    QProgressBar {{
        background-color: {palette.surface};
        border: 1px solid {palette.border};
        border-radius: 6px;
        text-align: center;
    }}
    QProgressBar::chunk {{
        background-color: {palette.accent};
        border-radius: 6px;
    }}
    """


def apply_dark_theme(application: QApplication, palette: DarkPalette = PALETTE) -> None:
    """Wendet das dunkle Fluent-Theme auf die gesamte Anwendung an.

    Args:
        application: Die laufende QApplication-Instanz.
        palette: Optionale abweichende Farbpalette (Standard: PALETTE).
    """
    application.setStyleSheet(_build_stylesheet(palette))
