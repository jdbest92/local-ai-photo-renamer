@echo off
rem Lanceur de Local AI Photo Renamer (Windows).
rem Si votre Python est dans un environnement (venv, conda...), activez-le ici.
rem Exemples :
rem   call .venv\Scripts\activate.bat
rem   call C:\ProgramData\anaconda3\Scripts\activate.bat mon_env

cd /d "%~dp0"

where pythonw >nul 2>nul
if errorlevel 1 goto nopython

python -c "import PySide6" >nul 2>nul
if errorlevel 1 goto nopyside

start "" pythonw app_gui.py
exit /b 0

:nopython
echo pythonw est introuvable dans le PATH.
echo Activez votre environnement Python en editant ce fichier (voir les
echo exemples en commentaire), ou installez Python depuis python.org.
pause
exit /b 1

:nopyside
echo Le Python trouve dans le PATH n'a pas les dependances de l'application.
echo Installez-les avec :  pip install -r requirements.txt
echo Ou activez le bon environnement en editant ce fichier (exemples en
echo commentaire). En cas de doute, lancez run_app_debug.bat pour le detail.
pause
exit /b 1
