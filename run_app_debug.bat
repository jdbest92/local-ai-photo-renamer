@echo off
rem Variante de lancement AVEC console : a utiliser si l'application ne s'ouvre pas,
rem pour voir les messages d'erreur Python.

cd /d "%~dp0"
python app_gui.py
pause
