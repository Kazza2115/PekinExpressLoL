@echo off
rem =====================================================================
rem  Pekin Express LoL - tunnel Internet (Windows)
rem  Rend le site (lance par PekinExpress.bat) accessible aux amis a
rem  distance via une adresse https://....trycloudflare.com (gratuit).
rem  Lance automatiquement par PekinExpress.bat (AUTO_TUNNEL dans .env),
rem  ou a la main en double-cliquant dessus.
rem  L'adresse obtenue est ecrite dans data\tunnel.log : le site la lit et
rem  l'affiche dans Admin > Systeme (et l'utilise pour les messages Discord).
rem =====================================================================
setlocal
cd /d "%~dp0"
title Pekin Express LoL - tunnel
echo.
echo  ===== Tunnel Cloudflare =====
echo.

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
echo  L'adresse a partager apparait ci-dessous (ligne https://....trycloudflare.com)
echo  et dans Admin ^> Systeme du site. Elle change a chaque lancement.
echo  LAISSE CETTE FENETRE OUVERTE pendant le challenge. Ctrl+C pour arreter.
echo.
"cloudflared.exe" tunnel --url http://127.0.0.1:8000 --logfile "data\tunnel.log" --loglevel info
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
