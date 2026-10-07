@echo off
rem =====================================================================
rem  Pekin Express LoL - lancement en un double-clic (Windows)
rem  Premier lancement : cree l'environnement Python, installe les
rem  dependances et prepare le fichier .env (cle Riot, mot de passe).
rem  Ensuite : demarre le site et l'ouvre dans le navigateur.
rem  Les autres joueurs se connectent sur http://<IP de ce PC>:8000
rem =====================================================================
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo Python est introuvable. Installe-le depuis https://python.org/downloads
    echo en cochant "Add Python to PATH", puis relance ce fichier.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo Premiere installation : creation de l'environnement Python...
    python -m venv .venv || (pause & exit /b 1)
    ".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
    echo Installation des dependances...
    ".venv\Scripts\python.exe" -m pip install --quiet -r requirements.txt || (pause & exit /b 1)
)

if not exist ".env" (
    copy /y ".env.example" ".env" >nul
    echo.
    echo  Le fichier .env vient d'etre cree. Ouvre-le pour y coller ta cle Riot
    echo  (RIOT_API_KEY=RGAPI-...) et choisir ADMIN_PASSWORD. Sans cle : mode demo.
    echo.
    start notepad ".env"
    pause
)

echo.
echo  Site : http://localhost:8000   (autres PC du reseau : http://^<IP de ce PC^>:8000)
echo  Laisse cette fenetre ouverte pendant le challenge. Ctrl+C pour arreter.
echo.
start "" "http://localhost:8000"
".venv\Scripts\python.exe" -m uvicorn app.main:app --host 0.0.0.0 --port 8000
endlocal
