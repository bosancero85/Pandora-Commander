# Pandora® Commander

Ein moderner, zweispaltiger Dateimanager im Stil von **Total Commander**,
**Double Commander** und **Krusader** – mit dunklem Fluent-Design,
SVG-Icons und den Komfortfunktionen, die man von einem
Enterprise-tauglichen Dateimanager erwartet.

Teil der **AKI_SystemDown® / Pandora®**-Produktfamilie.

---

## Funktionsumfang

| Bereich | Funktionen |
|---|---|
| Dateioperationen | Kopieren, Verschieben, Löschen, Umbenennen, Neuer Ordner – alle im Hintergrund-Thread mit Fortschrittsanzeige und Kollisionsbehandlung (Überschreiben/Umbenennen/Überspringen) |
| Drag & Drop | Ziehen zwischen linkem/rechtem Panel sowie von/zu externen Programmen (Explorer, Nautilus, Dolphin, …); ohne Zusatztaste automatische Wahl Verschieben/Kopieren je nach Dateisystem, Strg erzwingt Kopieren, Umschalt erzwingt Verschieben |
| Navigation | Zwei unabhängige Panels, Breadcrumb-Pfad, versteckte Dateien umschaltbar |
| Suche | Rekursiv, Wildcard/Regex, Größen-/Typfilter, optionale Inhaltssuche |
| Vorschau | Bilder, PDF, Text, Markdown, JSON, XML, HTML, SVG |
| Editor | Eigener Texteditor mit Syntax-Highlighting, Zeilennummern, Suchen/Ersetzen |
| Archive | Erstellen/Entpacken von ZIP, TAR, TAR.GZ, TAR.BZ2 und 7Z |
| Netzwerk | Verbindungsprofile für FTP, FTPS, SFTP, SMB und WebDAV inkl. Verbindungstest |
| Werkzeuge | Hash-Berechnung (MD5/SHA1/SHA256/SHA512), Ordner-/Dateivergleich, Massenumbenennung |
| Favoriten | Gruppierte Lesezeichen mit Export/Import als JSON |
| Terminal | Eingebettetes Shell-Terminal je Panel-Verzeichnis |
| Sonstiges | Plugin-System, Mehrsprachigkeit (Deutsch/Englisch), Logging mit Debug-Modus |
| Benachrichtigungen | Native Desktop-Benachrichtigungen nach abgeschlossenen Hintergrundoperationen (Kopieren/Verschieben/Löschen), abschaltbar in den Einstellungen |
| Updates | Automatische Update-Prüfung gegen ein konfigurierbares JSON-Manifest (optional beim Start, jederzeit manuell über Hilfe -> Nach Updates suchen), Anzeige von Changelog und Download-Link |
| Dashboard | Fest eingebautes, live aktualisiertes Dashboard (Extras -> Dashboard) mit App-Laufzeit, Plugin-Anzahl, Betriebssystem, Python-Version, CPU-Kernen, Arbeitsspeicher- und Datenträgerbelegung beider Panels |
| Warteschlange | Kopieren/Verschieben/Löschen blockiert nicht mehr die Oberfläche: Operationen laufen über eine Warteschlange (Extras -> Warteschlange), mehrere davon gleichzeitig (Anzahl in den Einstellungen konfigurierbar), jede mit eigener Fortschrittsanzeige und Abbrechen-Button in einem nicht-modalen Fenster |

---

## Voraussetzungen

- Python **3.13** oder neuer
- Ein funktionierendes Qt6-Laufzeitsystem (wird über `PyQt6` mitinstalliert)

---

## Installation

```bash
git clone <repository-url> PandoraCommander
cd PandoraCommander
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

Alternativ genügen für einen reinen Produktivbetrieb ohne editierbares
Paket auch die puren Laufzeitabhängigkeiten aus `requirements.txt`:

```bash
pip install -r requirements.txt
```

Auf dem Raspberry Pi 4B (Kali Linux) übernimmt `install.sh` Venv-Anlage,
Systempaket-Prüfung und Installation automatisch (siehe unten).

Mit der Option `--desktop` legt `install.sh` zusätzlich einen
Menüeintrag samt Anwendungssymbol an: Das Icon aus
`app/resources/icons/hicolor/` wird ins persönliche
Icon-Theme (`~/.local/share/icons/hicolor/`) installiert, und der
Starter `~/.local/share/applications/pandora-commander.desktop` wird
aus der gepflegten Vorlage `packaging/pandora-commander.desktop`
erzeugt:

```bash
./install.sh --desktop
```

## Start

```bash
python -m app.main
# oder, nach der Installation, über den Entry-Point:
pandora-commander
```

---

## Projektstruktur

```
PandoraCommander/
├── pyproject.toml
├── README.md
├── LICENSE                      Proprietäre Lizenz (AKI_SystemDown® / Pandora®)
├── install.sh                   Setup-Skript inkl. optionalem Desktop-Starter (--desktop)
├── packaging/
│   └── pandora-commander.desktop  Freedesktop-.desktop-Vorlage
├── tools/
│   └── generate_icon_assets.py Erzeugt PNG-Icon-Varianten aus dem Vektor-Design
└── app/
    ├── main.py                  Einstiegspunkt (QApplication, High-DPI, Theme)
    ├── core/
    │   ├── config.py            Settings-Verwaltung (JSON, dataclass-basiert)
    │   ├── logging_setup.py     Zentrales Logging (Datei + Konsole)
    │   ├── archive/             ZIP/TAR/7Z-Handling
    │   ├── filesystem/          Dateioperationen, Hash, Vergleich, Favoriten, Massenumbenennung
    │   ├── network/             FTP/FTPS/SFTP/SMB/WebDAV-Clients + Verbindungsmanager
    │   └── search/              Hintergrund-Suchmaschine
    ├── ui/
    │   ├── main_window.py       Hauptfenster (Menü, Symbolleiste, Panels, Statusleiste)
    │   ├── widgets/             FilePanel, Vorschau, Editor, Terminal
    │   └── dialogs/             Suche, Favoriten, Einstellungen, Vergleich, Hash, Massenumbenennung, Verbindungen
    ├── themes/                  Dunkles Fluent-Theme (QSS)
    ├── plugins/                 Automatisch geladenes Plugin-System
    ├── resources/
    │   └── icons/hicolor/       Anwendungssymbol (SVG + PNG-Rastergrößen) für Desktop-Integration
    ├── utils/
    │   └── icon_provider.py     Eingebettetes SVG-Icon-Set (Toolbar/Menü-Icons)
    └── translations/            de.json / en.json
```

---

## Tastenkürzel

| Taste | Aktion |
|---|---|
| F2 | Umbenennen |
| F3 | Vorschau |
| F4 | Editor |
| F5 | Kopieren |
| F6 | Verschieben |
| F7 | Neuer Ordner |
| F8 | Löschen |
| F10 | Beenden |
| Tab | Panel wechseln |
| Strg+F | Suchen |
| Strg+D | Favoriten |
| Strg+T | Terminal |
| Strg+R | Aktualisieren |
| Alt+Eingabe | Eigenschaften |

Alle Tastenkürzel sind in `app/core/config.py` (`DEFAULT_SHORTCUTS`) hinterlegt
und über den Einstellungsdialog einsehbar.

---

## Konfiguration & Daten

Alle Nutzerdaten liegen unter `~/.pandora_commander/`:

| Datei | Inhalt |
|---|---|
| `settings.json` | Theme, Sprache, Schriftgröße, Startpfade, Tastenkürzel |
| `favorites.json` | Favoritengruppen und -einträge |
| `connections.json` | Gespeicherte Netzwerkverbindungsprofile |
| `logs/pandora_commander.log` | Laufzeit-Log |
| `logs/pandora_commander.error.log` | Ausschließlich Fehlerprotokoll |

Passwörter in Verbindungsprofilen werden **unverschlüsselt** gespeichert
(kein Betriebssystem-Schlüsselbund als zusätzliche Abhängigkeit) –
ein entsprechender Hinweis wird beim Speichern geloggt.

---

## Entwicklung

```bash
ruff check app
black app
mypy app
pytest
```

Codequalität ist über `pyproject.toml` festgelegt (Ruff, Black,
Mypy im Strict-Modus, Pytest mit `pytest-qt`).

---

## Lizenz

Proprietär – © AKI_SystemDown® / Pandora®. Alle Rechte vorbehalten.
Der vollständige Lizenztext befindet sich in [`LICENSE`](LICENSE).
