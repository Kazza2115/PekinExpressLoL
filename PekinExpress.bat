@echo off
rem =====================================================================
rem  Pekin Express LoL - lancement en un double-clic (Windows)
rem  1) cree le fichier .env (cle Riot, mot de passe) s'il n'existe pas
rem  2) installe Python/venv + dependances (mises a jour comprises)
rem  3) demarre le site ; il redemarre tout seul quand ses fichiers
rem     changent (apres MiseAJour.bat par exemple)
rem  Les autres joueurs se connectent sur http://<IP de ce PC>:8000
rem =====================================================================
setlocal
cd /d "%~dp0"
title Pekin Express LoL
echo.
echo  ===== Pekin Express LoL =====
echo  Dossier : %CD%
echo.
rem --- 0. Deja lance ? ----------------------------------------------------
rem Un second lancement ouvrirait un second tunnel (adresse differente) : on l'evite
curl.exe -s -o nul -m 3 http://127.0.0.1:8000/health >nul 2>nul
if errorlevel 1 goto not_running
echo  Le site tourne deja dans une autre fenetre : rien a relancer.
echo  Le navigateur s'ouvre sur le lien du site.
echo  Pour redemarrer : ferme d'abord la fenetre du site, puis relance ce fichier.
echo  Si seul le tunnel manque ^(lien qui ne repond pas^), lance Tunnel.bat.
call :open_browser
goto end
:not_running


rem --- 1. Fichier .env --------------------------------------------------
if exist ".env" goto env_ok
echo  [1/3] Creation du fichier .env ...
call :create_env
if not exist ".env" (
    echo.
    echo  ERREUR : impossible de creer le fichier .env dans ce dossier.
    echo  Cree-le a la main avec le Bloc-notes ^(voir README^).
    goto fail
)
echo.
echo  Le fichier .env est cree. Le Bloc-notes va s'ouvrir : colle ta cle Riot
echo  apres RIOT_API_KEY= et choisis ADMIN_PASSWORD, enregistre ^(Ctrl+S^)
echo  puis FERME le Bloc-notes pour continuer. Sans cle : mode demo.
echo.
pause
start /wait notepad ".env"
:env_ok
echo  [1/3] Fichier .env : OK

rem --- 2. Python + dependances -------------------------------------------
if exist ".venv\Scripts\python.exe" goto venv_ok
echo  [2/3] Premiere installation ...
python -c "import sys; print('  Python', sys.version.split()[0])" 2>nul
if errorlevel 1 (
    echo.
    echo  ERREUR : Python est introuvable ou n'est pas le vrai Python.
    echo  Installe-le depuis https://python.org/downloads en cochant
    echo  "Add Python to PATH", puis relance ce fichier.
    echo  ^(Si une fenetre Microsoft Store s'ouvre quand tu tapes "python",
    echo   desactive l'alias dans Parametres ^> Applications ^> Alias d'execution.^)
    goto fail
)
echo  Creation de l'environnement Python ^(.venv^) ...
python -m venv .venv
if errorlevel 1 (
    echo  ERREUR : la creation de l'environnement a echoue ^(voir ci-dessus^).
    goto fail
)
".venv\Scripts\python.exe" -m pip install --upgrade pip
:venv_ok
echo  [2/3] Dependances ^(installation / mise a jour^) ...
".venv\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check -r requirements.txt
if errorlevel 1 (
    echo.
    echo  ERREUR : l'installation des dependances a echoue ^(voir ci-dessus^).
    echo  Verifie la connexion Internet, puis relance ce fichier.
    goto fail
)
echo  [2/3] Dependances : OK

rem --- 3. Lancement -------------------------------------------------------
echo  [3/3] Demarrage du site ...
echo.
echo  Lien a partager : https://kazza2115.github.io/PekinExpressLoL/
echo  ^(le navigateur l'ouvre tout seul des qu'il est pret, en general moins d'une minute^)
echo  Sur ce PC seulement : http://127.0.0.1:8000
echo  Autres PC du reseau : http://^<IP de ce PC^>:8000  ^(ipconfig pour l'IP^)
echo  Pour installer une nouvelle version : double-clic sur MiseAJour.bat,
echo  le site redemarre tout seul.
echo  LAISSE CETTE FENETRE OUVERTE pendant le challenge. Ctrl+C pour arreter.
echo.
rem Tunnel Internet (adresse https://....trycloudflare.com pour les amis a distance),
rem dans sa propre fenetre, sauf AUTO_TUNNEL=false dans .env
set "OPEN_MODE=--local"
findstr /b /i /c:"AUTO_TUNNEL=false" ".env" >nul 2>nul
if errorlevel 1 (
    if exist "Tunnel.bat" (
        set "OPEN_MODE="
        call :stop_old_tunnel
        echo  Ouverture du tunnel Internet dans une autre fenetre ^(AUTO_TUNNEL, TUNNEL^) ...
        start "Pekin Express LoL - tunnel" cmd /c "Tunnel.bat"
    )
)
rem Le navigateur s'ouvre sur le lien fixe GitHub des que la nouvelle adresse du tunnel y est
rem publiee (moins d'une minute), sinon sur l'adresse du tunnel, sinon en local
call :open_browser
rem --reload : le serveur redemarre de lui-meme quand les fichiers du site changent
".venv\Scripts\python.exe" -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload --reload-dir app
echo.
echo  Le serveur s'est arrete.
goto end

:open_browser
rem Petit programme sans fenetre : attend que le lien soit pret puis ouvre le navigateur
if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" -m app.open_site %OPEN_MODE%
) else (
    start "" cmd /c "timeout /t 3 /nobreak >nul & start "" http://127.0.0.1:8000"
)
goto :eof

:stop_old_tunnel
rem Tunnel d'un lancement precedent (sa fenetre reste ouverte quand on ferme le site) :
rem on le ferme, sinon deux tunnels tournent et le lien publie peut etre l'ancien.
tasklist /FI "IMAGENAME eq cloudflared.exe" 2>nul | find /I "cloudflared.exe" >nul
if not errorlevel 1 (
    echo  Fermeture de l'ancien tunnel Cloudflare encore ouvert ...
    taskkill /IM cloudflared.exe /F >nul 2>nul
    timeout /t 2 /nobreak >nul
)
tasklist /FI "IMAGENAME eq tailscale.exe" 2>nul | find /I "tailscale.exe" >nul
if not errorlevel 1 taskkill /IM tailscale.exe /F >nul 2>nul
rem Ancienne adresse : le site ne doit pas la lire (ni la publier) avant la nouvelle
if exist "data\tunnel.log" del /q "data\tunnel.log" >nul 2>nul
goto :eof

:create_env
rem Ecrit un .env complet (identique a .env.example) sans dependre de la copie
> ".env" echo # Cle Riot (developer.riotgames.com). Laisser vide pour le mode demo.
>>".env" echo RIOT_API_KEY=
>>".env" echo RIOT_PLATFORM=euw1
>>".env" echo RIOT_REGION=europe
>>".env" echo.
>>".env" echo # Mode demo : true = client Riot simule. Vide = automatique (demo si pas de cle).
>>".env" echo DEMO_MODE=
>>".env" echo.
>>".env" echo # Secondes entre deux interrogations de Riot (90 en reel, 10 en demo)
>>".env" echo POLL_INTERVAL_SECONDS=90
>>".env" echo # Secondes entre deux verifications des parties en cours (notifications). Minimum 10.
>>".env" echo LIVE_POLL_SECONDS=30
>>".env" echo TRACK_FLEX=false
>>".env" echo.
>>".env" echo # Mot de passe de l'organisateur (page Admin, duos, demarrage)
>>".env" echo ADMIN_PASSWORD=change-me
>>".env" echo.
>>".env" echo # Base SQLite
>>".env" echo DATABASE_URL=sqlite:///./data/tracker.db
>>".env" echo.
>>".env" echo # Regles du challenge
>>".env" echo GAMES_PER_DAY=10
>>".env" echo MAX_PLAYERS=8
>>".env" echo TIMEZONE=Europe/Paris
>>".env" echo.
>>".env" echo # Webhook Discord (optionnel). Vide = desactive.
>>".env" echo DISCORD_WEBHOOK_URL=
>>".env" echo # Role mentionne dans chaque message : son identifiant (Discord, mode developpeur, clic droit sur le role, Copier l'identifiant du role).
>>".env" echo DISCORD_ROLE_ID=
>>".env" echo # GIF des resultats cherches sur Klipy : cle gratuite sur partner.klipy.com. Vide = pas de GIF.
>>".env" echo KLIPY_API_KEY=
>>".env" echo.
>>".env" echo # Adresse publique du site (utilisee dans les messages Discord)
>>".env" echo BASE_URL=http://localhost:8000
>>".env" echo.
>>".env" echo # Ouvrir aussi le tunnel Cloudflare au lancement (adresse a partager). false pour desactiver.
>>".env" echo AUTO_TUNNEL=true
>>".env" echo.
>>".env" echo # Type de tunnel : rapide (adresse qui change), tailscale (lien fixe gratuit) ou cloudflare (lien fixe, ton domaine). Voir README.
>>".env" echo TUNNEL=rapide
>>".env" echo # Mode cloudflare uniquement : jeton du tunnel. Mets aussi BASE_URL=https://ton-domaine
>>".env" echo CLOUDFLARE_TUNNEL_TOKEN=
>>".env" echo.
>>".env" echo # Lien fixe gratuit (page GitHub Pages) : jeton GitHub, acces Contents en ecriture au depot. Secret. Voir README.
>>".env" echo GITHUB_TOKEN=
goto :eof

:fail
echo.
echo  Le lancement a echoue. Copie le texte de cette fenetre pour te faire aider.
:end
echo.
pause
endlocal
