@echo off
rem =====================================================================
rem  Pekin Express LoL - mise a jour du site en un double-clic (Windows)
rem  Recupere la derniere version publiee sur GitHub avec Git, puis
rem  installe les nouvelles dependances. Le site deja lance (PekinExpress.bat)
rem  redemarre tout seul des que ses fichiers changent.
rem  Tes fichiers personnels (.env, data\) ne sont jamais touches.
rem =====================================================================
setlocal
cd /d "%~dp0"
title Pekin Express LoL - mise a jour
set "REPO_URL=https://github.com/Kazza2115/PekinExpressLoL.git"
set "BRANCH=claude/quirky-pascal-y84evb"
echo.
echo  ===== Mise a jour du site =====
echo.

where git >nul 2>nul
if errorlevel 1 (
    echo  Git est introuvable. Installe-le une fois pour toutes :
    echo    - soit en tapant dans PowerShell :  winget install Git.Git
    echo    - soit depuis https://git-scm.com/download/win
    echo  puis ferme cette fenetre et relance ce fichier.
    goto fail
)

if exist ".git" goto pull

rem --- Dossier installe depuis un ZIP : on le relie au depot GitHub (une seule fois)
echo  Premiere mise a jour : liaison du dossier au depot GitHub ...
git init -q || goto giterror
git remote add origin "%REPO_URL%" || goto giterror
git fetch -q origin "%BRANCH%" || goto giterror
rem Les fichiers du site sont remplaces par la version GitHub ; .env et data\ sont ignores par git
git checkout -q -f -B "%BRANCH%" "origin/%BRANCH%" || goto giterror
goto deps

:pull
echo  Telechargement des dernieres modifications ...
git fetch -q origin "%BRANCH%" || goto giterror
git checkout -q -f -B "%BRANCH%" "origin/%BRANCH%" || goto giterror

:deps
for /f "delims=" %%v in ('git log -1 --format^="%%h - %%s"') do set "VERSION=%%v"
echo  Version installee : %VERSION%
if exist ".venv\Scripts\python.exe" (
    echo  Mise a jour des dependances ...
    ".venv\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check -r requirements.txt
)
echo.
echo  Mise a jour terminee. Si le site tourne, il redemarre tout seul dans
echo  quelques secondes ^(sinon lance PekinExpress.bat^). Recharge la page.
goto end

:giterror
echo.
echo  ERREUR : la commande git a echoue ^(voir ci-dessus^). Verifie la connexion
echo  Internet, puis relance ce fichier.
:fail
echo.
echo  Copie le texte de cette fenetre pour te faire aider.
:end
echo.
pause
endlocal
