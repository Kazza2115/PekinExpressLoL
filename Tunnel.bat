@echo off
rem =====================================================================
rem  Pekin Express LoL - tunnel Internet (Windows)
rem  Rend le site (lance par PekinExpress.bat) accessible aux amis a
rem  distance. Lance automatiquement par PekinExpress.bat (AUTO_TUNNEL dans
rem  .env), ou a la main en double-cliquant dessus.
rem
rem  Trois modes, choisis par TUNNEL= dans .env :
rem   - rapide     (defaut) Cloudflare sans compte : https://....trycloudflare.com,
rem                adresse differente a chaque lancement.
rem   - tailscale  lien FIXE gratuit https://nom-du-pc.xxxx.ts.net (Tailscale
rem                Funnel) : installer Tailscale et se connecter une fois.
rem   - cloudflare lien FIXE sur ton nom de domaine (tunnel Cloudflare nomme) :
rem                CLOUDFLARE_TUNNEL_TOKEN= et BASE_URL= dans .env.
rem  L'adresse est ecrite dans data\tunnel.log : le site l'affiche dans
rem  Admin > Systeme (et l'utilise pour les messages Discord).
rem =====================================================================
setlocal
cd /d "%~dp0"
title Pekin Express LoL - tunnel

rem --- Reglages lus dans .env -----------------------------------------------
set "TUNNEL="
set "CF_TOKEN="
if exist ".env" (
    for /f "usebackq eol=# tokens=1,* delims==" %%a in (".env") do (
        if /i "%%a"=="TUNNEL" set "TUNNEL=%%b"
        if /i "%%a"=="CLOUDFLARE_TUNNEL_TOKEN" set "CF_TOKEN=%%b"
    )
)
if "%TUNNEL%"=="" set "TUNNEL=rapide"

echo.
echo  ===== Tunnel Internet (mode : %TUNNEL%) =====
echo.
if /i "%TUNNEL%"=="tailscale" goto wait_site
if /i "%TUNNEL%"=="cloudflare" goto need_cloudflared
if /i "%TUNNEL%"=="rapide" goto need_cloudflared
echo  ERREUR : TUNNEL=%TUNNEL% inconnu dans .env. Valeurs possibles :
echo  rapide, tailscale ou cloudflare.
goto fail

:need_cloudflared
if exist "cloudflared.exe" goto wait_site
echo  Telechargement de cloudflared.exe (une seule fois) ...
curl.exe -L --fail -o "cloudflared.exe" "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
if errorlevel 1 (
    echo.
    echo  ERREUR : telechargement impossible. Telecharge-le a la main :
    echo  https://github.com/cloudflare/cloudflared/releases/latest
    echo  fichier "cloudflared-windows-amd64.exe", a renommer en cloudflared.exe
    echo  et a poser dans ce dossier, puis relance ce fichier.
    goto fail
)

:wait_site
rem Attend que le site reponde (jusqu'a 2 minutes) avant d'ouvrir le tunnel
set /a tries=0
:wait_loop
curl.exe -s -o nul http://127.0.0.1:8000/health && goto run
set /a tries+=1
if %tries% geq 60 (
    echo  Le site ne repond pas sur http://127.0.0.1:8000 : lance PekinExpress.bat,
    echo  puis relance ce fichier.
    goto fail
)
if %tries%==1 echo  En attente du site ^(PekinExpress.bat^) ...
timeout /t 2 /nobreak >nul
goto wait_loop

:run
if not exist "data" mkdir "data"
del /q "data\tunnel.log" 2>nul
if /i "%TUNNEL%"=="tailscale" goto run_tailscale
if /i "%TUNNEL%"=="cloudflare" goto run_cloudflare

rem --- Mode rapide : adresse trycloudflare.com qui change a chaque lancement ---
echo  L'adresse a partager apparait ci-dessous (ligne https://....trycloudflare.com)
echo  et dans Admin ^> Systeme du site. Elle change a chaque lancement :
echo  pour un lien fixe, voir TUNNEL=tailscale dans le README.
echo  LAISSE CETTE FENETRE OUVERTE pendant le challenge. Ctrl+C pour arreter.
echo.
"cloudflared.exe" tunnel --url http://127.0.0.1:8000 --logfile "data\tunnel.log" --loglevel info
goto stopped

rem --- Mode cloudflare : tunnel nomme, lien fixe sur ton domaine ---------------
:run_cloudflare
if "%CF_TOKEN%"=="" (
    echo  ERREUR : CLOUDFLARE_TUNNEL_TOKEN est vide dans .env.
    echo  Cree le tunnel dans le tableau de bord Cloudflare ^(Zero Trust ^> Networks
    echo  ^> Tunnels^), copie son jeton dans .env, puis relance ce fichier. Voir README.
    goto fail
)
echo  Lien fixe : l'adresse de ton domaine ^(BASE_URL dans .env^).
echo  LAISSE CETTE FENETRE OUVERTE pendant le challenge. Ctrl+C pour arreter.
echo.
rem Le jeton passe par une variable d'environnement : il n'apparait pas a l'ecran
set "TUNNEL_TOKEN=%CF_TOKEN%"
"cloudflared.exe" tunnel --no-autoupdate --logfile "data\tunnel.log" --loglevel info run
goto stopped

rem --- Mode tailscale : Tailscale Funnel, lien fixe gratuit en .ts.net --------
:run_tailscale
set "TS=tailscale"
where tailscale >nul 2>nul
if errorlevel 1 set "TS=%ProgramFiles%\Tailscale\tailscale.exe"
"%TS%" version >nul 2>nul
if errorlevel 1 (
    echo  ERREUR : Tailscale n'est pas installe.
    echo  Installe-le depuis https://tailscale.com/download/windows, connecte-toi,
    echo  puis relance ce fichier. Voir README, section "Lien fixe".
    goto fail
)
rem Adresse fixe = nom de ce PC sur Tailscale ; ecrite dans data\tunnel.log pour l'Admin
powershell -NoProfile -Command "try { $n = ((& '%TS%' status --json) | ConvertFrom-Json).Self.DNSName.TrimEnd('.'); if ($n) { 'Lien fixe Tailscale : https://' + $n | Out-File -Encoding utf8 'data\tunnel.log' } } catch { }"
if exist "data\tunnel.log" type "data\tunnel.log"
echo.
echo  Si c'est la premiere fois, Tailscale affiche un lien pour autoriser Funnel :
echo  ouvre-le, accepte, et l'adresse ci-dessus devient accessible a tous.
echo  LAISSE CETTE FENETRE OUVERTE pendant le challenge. Ctrl+C pour arreter.
echo.
"%TS%" funnel 8000
goto stopped

:stopped
echo.
echo  Le tunnel s'est arrete.
goto end

:fail
echo.
echo  Copie le texte de cette fenetre pour te faire aider.
:end
echo.
pause
endlocal
