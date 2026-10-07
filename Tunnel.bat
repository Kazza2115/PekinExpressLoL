@echo off
rem =====================================================================
rem  Pekin Express LoL - tunnel Internet en un double-clic (Windows)
rem  Rend le site (deja lance avec PekinExpress.bat) accessible aux amis
rem  a distance via une adresse https://....trycloudflare.com (gratuit).
rem  Telecharge cloudflared.exe dans ce dossier si besoin.
rem =====================================================================
setlocal
cd /d "%~dp0"
title Pekin Express LoL - tunnel
echo.
echo  ===== Tunnel Cloudflare =====
echo.

if exist "cloudflared.exe" goto run

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

:run
echo  Le site doit deja tourner (PekinExpress.bat) : verifie http://localhost:8000
echo  L'adresse a partager apparait ci-dessous (ligne https://....trycloudflare.com).
echo  Elle change a chaque lancement. LAISSE CETTE FENETRE OUVERTE. Ctrl+C pour arreter.
echo.
"cloudflared.exe" tunnel --url http://127.0.0.1:8000
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
