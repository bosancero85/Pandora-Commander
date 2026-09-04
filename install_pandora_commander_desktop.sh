#!/usr/bin/env bash
# ---------------------------------------------------------------------
# Pandora® Commander - Installer für Anwendungsstarter (.desktop)
# ---------------------------------------------------------------------
# Installiert das komplette hicolor-Icon-Set + eine .desktop-Datei,
# sodass der Dateimanager im Kali-Anwendungsmenü (und optional auf dem
# Desktop) auftaucht und korrekt startet.
#
# Verwendung:
#   ./install_pandora_commander_desktop.sh [Pfad/zu/app/main.py]
#
# Ohne Argument wird automatisch in diesem Ordner sowie in
# ~/Pandora, ~/, ~/Desktop und ~/pandora_commander gesucht.
# ---------------------------------------------------------------------

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ---- 1. app/main.py finden und daraus das Projekt-Root ableiten ----
TARGET_PY="$1"
if [ -z "$TARGET_PY" ]; then
    CANDIDATES=(
        "$SCRIPT_DIR/app/main.py"
        "$HOME/Pandora/app/main.py"
        "$HOME/pandora_commander/app/main.py"
        "$HOME/app/main.py"
        "$HOME/Desktop/app/main.py"
    )
    for c in "${CANDIDATES[@]}"; do
        if [ -f "$c" ]; then
            TARGET_PY="$c"
            break
        fi
    done
fi

if [ -z "$TARGET_PY" ] || [ ! -f "$TARGET_PY" ]; then
    echo "❌ app/main.py wurde nicht gefunden."
    echo "   Bitte Pfad als Argument übergeben:"
    echo "   ./install_pandora_commander_desktop.sh /pfad/zu/app/main.py"
    exit 1
fi
TARGET_PY="$(cd "$(dirname "$TARGET_PY")" && pwd)/$(basename "$TARGET_PY")"
# Projekt-Root = übergeordneter Ordner von "app/"
PROJECT_ROOT="$(cd "$(dirname "$TARGET_PY")/.." && pwd)"
echo "✅ Skript gefunden: $TARGET_PY"
echo "✅ Projekt-Root:    $PROJECT_ROOT"

# ---- 2. Start-Kommando bestimmen ----
# main.py verwendet absolute Imports (from app.xxx import ...) und MUSS
# daher entweder über "python -m app.main" aus dem Projekt-Root oder
# über den in pyproject.toml definierten Entry-Point "pandora-commander"
# gestartet werden - ein direkter Aufruf von main.py schlägt sonst mit
# "ModuleNotFoundError: No module named 'app'" fehl.
VENV_BIN="$PROJECT_ROOT/.venv/bin"
if [ -x "$VENV_BIN/pandora-commander" ]; then
    EXEC_CMD="$VENV_BIN/pandora-commander"
    echo "✅ Verwende venv-Entry-Point: $EXEC_CMD"
elif [ -x "$VENV_BIN/python3" ]; then
    EXEC_CMD="/bin/bash -c \"cd '$PROJECT_ROOT' && exec '$VENV_BIN/python3' -m app.main\""
    echo "✅ Verwende venv-Python mit -m app.main"
else
    EXEC_CMD="/bin/bash -c \"cd '$PROJECT_ROOT' && exec python3 -m app.main\""
    echo "⚠️  Keine .venv gefunden, verwende System-Python (PyQt6 muss global installiert sein)."
fi

# ---- 3. Icon-Set installieren (hicolor-Theme statt Einzeldatei) ----
ICON_THEME_DIR="$HOME/.local/share/icons/hicolor"
ICON_SOURCE_DIR="$PROJECT_ROOT/app/resources/icons/hicolor"
ICON_NAME="pandora-commander"

if [ -d "$ICON_SOURCE_DIR" ]; then
    while IFS= read -r -d '' icon_file; do
        rel_path="${icon_file#"$ICON_SOURCE_DIR"/}"
        target_path="$ICON_THEME_DIR/$rel_path"
        mkdir -p "$(dirname "$target_path")"
        cp -f "$icon_file" "$target_path"
    done < <(find "$ICON_SOURCE_DIR" -type f \( -name '*.svg' -o -name '*.png' \) -print0)
    echo "✅ Icon-Set installiert unter: $ICON_THEME_DIR"
    if command -v gtk-update-icon-cache >/dev/null 2>&1; then
        gtk-update-icon-cache -f -t "$ICON_THEME_DIR" >/dev/null 2>&1 || true
        echo "✅ Icon-Cache aktualisiert."
    fi
elif [ -f "$SCRIPT_DIR/pandora_commander_icon_256.png" ]; then
    # Fallback: einzelne PNG neben dem Skript
    mkdir -p "$ICON_THEME_DIR/256x256/apps"
    cp -f "$SCRIPT_DIR/pandora_commander_icon_256.png" "$ICON_THEME_DIR/256x256/apps/$ICON_NAME.png"
    echo "✅ Fallback-Icon installiert (nur 256x256)."
else
    ICON_NAME="utilities-terminal"
    echo "⚠️  Kein Icon gefunden, verwende System-Fallback-Icon."
fi

# ---- 4. .desktop-Datei erzeugen ----
APP_DIR="$HOME/.local/share/applications"
mkdir -p "$APP_DIR"
DESKTOP_FILE="$APP_DIR/pandora-commander.desktop"

cat > "$DESKTOP_FILE" << EOF
[Desktop Entry]
Type=Application
Name=Pandora® Commander
GenericName=Dateimanager
Comment=Zweispaltiger Dateimanager im Stil von Total Commander
Exec=$EXEC_CMD
Icon=$ICON_NAME
Terminal=false
Categories=System;FileTools;FileManager;Utility;
Keywords=Pandora;Commander;Dateimanager;Explorer;
StartupNotify=true
StartupWMClass=pandora-commander
EOF

chmod +x "$DESKTOP_FILE"
chmod +x "$TARGET_PY"
echo "✅ Starter installiert: $DESKTOP_FILE"

# ---- 5. Desktop-Datenbank aktualisieren ----
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$APP_DIR" 2>/dev/null || true
    echo "✅ Anwendungsmenü aktualisiert."
fi

# ---- 6. Optional: Verknüpfung auf dem Desktop ----
if [ -d "$HOME/Desktop" ]; then
    read -p "Zusätzlich eine Verknüpfung auf dem Desktop anlegen? [j/N] " ans
    if [[ "$ans" =~ ^[jJ]$ ]]; then
        cp -f "$DESKTOP_FILE" "$HOME/Desktop/pandora-commander.desktop"
        chmod +x "$HOME/Desktop/pandora-commander.desktop"
        gio set "$HOME/Desktop/pandora-commander.desktop" metadata::trusted true 2>/dev/null || true
        echo "✅ Desktop-Verknüpfung angelegt."
    fi
fi

echo ""
echo "🎉 Fertig! Pandora® Commander sollte jetzt im Anwendungsmenü"
echo "   (Kategorie 'System/Dateimanager') auffindbar sein."
echo "   Falls keine .venv existiert: ./install.sh ausführen (installiert PyQt6 automatisch)."
echo "   Falls ffmpeg fehlt: sudo apt-get install ffmpeg"
