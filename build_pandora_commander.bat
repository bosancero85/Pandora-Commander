@echo off
setlocal enabledelayedexpansion

title Pandora Commander - Build System
color 0B

echo ========================================================
echo           PANDORA COMMANDER - BUILD PROCESS
echo ========================================================
echo.

:: 1. Virtuelle Umgebung suchen und aktivieren
if exist "venv\Scripts\activate.bat" (
    echo [*] Aktiviere venv...
    call venv\Scripts\activate.bat
) else if exist ".venv\Scripts\activate.bat" (
    echo [*] Aktiviere .venv...
    call .venv\Scripts\activate.bat
) else (
    echo [!] Keine virtuelle Umgebung gefunden. Fahre mit System-Python fort...
)

:: 2. Alte Build-Artefakte aufräumen
echo.
echo [*] Bereinige alte Build-Dateien...
if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"
if exist "*.spec" del /f /q "*.spec"

:: 3. PyInstaller Verfuegbarkeit pruefen
python -m pip show pyinstaller >nul 2>&1
if %errorlevel% neq 0 (
    echo [!] PyInstaller nicht gefunden. Installiere PyInstaller...
    python -m pip install pyinstaller
)

:: 4. PyInstaller Ausfuehrung
echo.
echo [*] Kompiliere Pandora Commander...
echo.

pyinstaller --noconfirm --onedir --windowed ^
    --name "PandoraCommander" ^
    --add-data "app/themes;app/themes" ^
    --add-data "app/assets;app/assets" ^
    --icon "app/assets/icon.ico" ^
    --paths "." ^
    app/main.py

:: 5. Fehlerprüfung
if %errorlevel% neq 0 (
    color 0C
    echo.
    echo ========================================================
    echo [!] FEHLER beim Erstellen der Executable!
    echo ========================================================
    pause
    exit /b %errorlevel%
)

:: 6. Erfolgsmeldung
color 0A
echo.
echo ========================================================
echo [✓] Build erfolgreich abgeschlossen!
echo [>] Binary zu finden unter: dist\PandoraCommander\PandoraCommander.exe
echo ========================================================
echo.

pause
