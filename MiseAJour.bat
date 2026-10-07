@echo off
rem =====================================================================
rem  Pekin Express LoL - mise a jour du site en un double-clic (Windows)
rem  Telecharge la derniere version publiee sur GitHub (ZIP), la copie
rem  par-dessus ce dossier, puis installe les nouvelles dependances.
rem  Aucun outil a installer. Tes fichiers personnels (.env, data\,
rem  cloudflared.exe) ne sont jamais touches.
rem  Le site deja lance (PekinExpress.bat) redemarre tout seul ensuite.
rem =====================================================================
setlocal
cd /d "%~dp0"
title Pekin Express LoL - mise a jour
set "BRANCH=claude/quirky-pascal-y84evb"
set "ZIP_URL=https://github.com/Kazza2115/PekinExpressLoL/archive/refs/heads/%BRANCH%.zip"
set "WORK=%TEMP%\pekin-update"
set "ZIP=%TEMP%\pekin-update.zip"
echo.
echo  ===== Mise a jour du site =====
echo  Dossier : %CD%
echo.

echo  [1/3] Telechargement de la derniere version ...
if exist "%WORK%" rmdir /s /q "%WORK%"
del /q "%ZIP%" 2>nul
curl.exe -L --fail -s -S -o "%ZIP%" "%ZIP_URL%"
if errorlevel 1 (
    echo  ERREUR : telechargement impossible. Verifie la connexion Internet.
    goto fail
)

echo  [2/3] Extraction ...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Expand-Archive -Force -LiteralPath '%ZIP%' -DestinationPath '%WORK%'"
if errorlevel 1 (
    echo  ERREUR : extraction impossible.
    goto fail
)
set "SRC="
for /d %%d in ("%WORK%\PekinExpressLoL-*") do set "SRC=%%d"
if not defined SRC (
    echo  ERREUR : archive inattendue ^(dossier PekinExpressLoL-* introuvable^).
    goto fail
)

echo  [3/3] Copie des fichiers ^(.env, data et .venv conserves^) ...
rem Dossiers du site : miroir exact (les fichiers supprimes en amont le sont ici aussi)
robocopy "%SRC%\app" "%CD%\app" /MIR /XD __pycache__ /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 goto copyerror
robocopy "%SRC%\tests" "%CD%\tests" /MIR /XD __pycache__ /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 goto copyerror
robocopy "%SRC%\docs" "%CD%\docs" /MIR /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 goto copyerror
rem Fichiers a la racine (sauf ce script, en cours d'execution)
robocopy "%SRC%" "%CD%" /XF .env *.db MiseAJour.bat /XD app tests docs data .venv .git /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 goto copyerror
copy /y "%SRC%\MiseAJour.bat" "%CD%\MiseAJour.bat.new" >nul
rmdir /s /q "%WORK%" 2>nul
del /q "%ZIP%" 2>nul

if exist ".venv\Scripts\python.exe" (
    echo  Mise a jour des dependances ...
    ".venv\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check -r requirements.txt
)
echo.
echo  Mise a jour terminee.
echo  - Si le site tourne, il redemarre tout seul dans quelques secondes ;
echo    sinon lance PekinExpress.bat. La version s'affiche en bas des pages.
echo  - Si un fichier MiseAJour.bat.new est apparu, remplace MiseAJour.bat par lui
echo    ^(nouvelle version de ce script^).
goto end

:copyerror
echo.
echo  ERREUR : la copie des fichiers a echoue ^(fichier verrouille ?^). Ferme le site
echo  ^(fenetre PekinExpress.bat^), puis relance ce fichier.
:fail
echo.
echo  Copie le texte de cette fenetre pour te faire aider.
:end
echo.
pause
endlocal
