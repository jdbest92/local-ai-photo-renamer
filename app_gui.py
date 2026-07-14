#!/usr/bin/env python3
"""Lanceur de l'application graphique Rename Photos.

Usage :
    python app_gui.py

Prerequis (en plus de ceux des scripts en ligne de commande) :
    pip install pyside6 --break-system-packages

Rappels :
    - Ollama doit tourner en local (ollama serve) avec le modele vision telecharge.
    - La reconnaissance faciale utilise faces_db.json (onglet Enrolement pour l'alimenter).
    - Le renommage passe toujours par un apercu editable : rien n'est modifie
      tant que 'Appliquer la selection' n'est pas confirme.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from photoapp.main_window import MainWindow

ICON_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app_icon.png")


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Rename Photos")
    app.setStyle("Fusion")
    if os.path.exists(ICON_PATH):
        app.setWindowIcon(QIcon(ICON_PATH))
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
