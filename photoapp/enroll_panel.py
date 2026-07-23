"""Onglet Enrolement : glisser-deposer des photos de reference vers Reference/<nom>/."""

import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QComboBox, QFileDialog, QHBoxLayout,
                               QInputDialog, QLabel, QListWidget,
                               QMessageBox, QPushButton, QVBoxLayout, QWidget)

from . import core, workers


class DropZone(QLabel):
    """Zone de depot de fichiers image."""

    files_dropped = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumHeight(120)
        self.setText("Glissez-déposez ici les photos de référence\n"
                     "(3 à 5 photos par personne, angles et éclairages variés)")
        self.setStyleSheet(
            "QLabel { border: 2px dashed #888; border-radius: 8px; "
            "color: #aaa; padding: 12px; }")

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self.setStyleSheet(
                "QLabel { border: 2px dashed #2ecc71; border-radius: 8px; "
                "color: #2ecc71; padding: 12px; }")

    def dragLeaveEvent(self, event):
        self.setStyleSheet(
            "QLabel { border: 2px dashed #888; border-radius: 8px; "
            "color: #aaa; padding: 12px; }")

    def dropEvent(self, event):
        self.dragLeaveEvent(event)
        paths = []
        for url in event.mimeData().urls():
            p = url.toLocalFile()
            if p and os.path.isfile(p):
                paths.append(p)
            elif p and os.path.isdir(p):
                for f in sorted(os.listdir(p)):
                    fp = os.path.join(p, f)
                    if os.path.isfile(fp):
                        paths.append(fp)
        if paths:
            self.files_dropped.emit(paths)


class EnrollPanel(QWidget):
    log = Signal(str)
    db_updated = Signal()   # base de visages modifiee (rafraichir filtre, viseur...)

    def __init__(self, engine, parent=None):
        super().__init__(parent)
        self.engine = engine
        self._pending_files = []
        self._worker = None

        layout = QVBoxLayout(self)

        row = QHBoxLayout()
        row.addWidget(QLabel("Personne :"))
        self.name_combo = QComboBox()
        self.name_combo.setEditable(True)
        self.name_combo.setMinimumWidth(160)
        row.addWidget(self.name_combo)
        self.new_person_btn = QPushButton("Nouvelle personne...")
        self.new_person_btn.clicked.connect(self.new_person)
        row.addWidget(self.new_person_btn)
        self.refresh_btn = QPushButton("Rafraîchir la liste")
        self.refresh_btn.clicked.connect(self.refresh_names)
        row.addWidget(self.refresh_btn)
        row.addStretch(1)
        layout.addLayout(row)

        self.drop_zone = DropZone()
        self.drop_zone.files_dropped.connect(self._on_files)
        layout.addWidget(self.drop_zone)

        pick = QHBoxLayout()
        self.pick_folder_btn = QPushButton("Ajouter un dossier...")
        self.pick_folder_btn.setToolTip("Ajoute toutes les images d'un dossier à la liste")
        self.pick_folder_btn.clicked.connect(self.pick_folder)
        self.pick_files_btn = QPushButton("Ajouter des photos...")
        self.pick_files_btn.clicked.connect(self.pick_files)
        pick.addWidget(self.pick_folder_btn)
        pick.addWidget(self.pick_files_btn)
        pick.addStretch(1)
        layout.addLayout(pick)

        self.file_list = QListWidget()
        self.file_list.setMaximumHeight(120)
        layout.addWidget(self.file_list)

        btns = QHBoxLayout()
        self.enroll_btn = QPushButton("Copier dans Reference/ et enrôler")
        self.enroll_btn.setEnabled(False)
        self.enroll_btn.clicked.connect(self.start_enroll)
        self.clear_btn = QPushButton("Vider la liste")
        self.clear_btn.clicked.connect(self._clear)
        btns.addWidget(self.enroll_btn)
        btns.addWidget(self.clear_btn)
        btns.addStretch(1)
        layout.addLayout(btns)

        self.status = QLabel("")
        layout.addWidget(self.status)
        layout.addStretch(1)

        self.refresh_names()

    def refresh_names(self):
        current = self.name_combo.currentText()
        self.name_combo.clear()
        names = set(self.engine.persons())
        if os.path.isdir(core.REFERENCE_DIR):
            for d in sorted(os.listdir(core.REFERENCE_DIR)):
                if os.path.isdir(os.path.join(core.REFERENCE_DIR, d)):
                    names.add(d)
        self.name_combo.addItems(sorted(names))
        if current:
            self.name_combo.setCurrentText(current)

    def new_person(self):
        name, ok = QInputDialog.getText(self, "Nouvelle personne",
                                        "Nom de la personne (ex : Alice) :")
        if not ok:
            return
        name = name.strip()
        if not name:
            return
        if any(c in '\\/:*?"<>|' for c in name):
            QMessageBox.warning(self, "Nouvelle personne",
                                "Le nom ne doit pas contenir \\ / : * ? \" < > |")
            return
        if self.name_combo.findText(name) < 0:
            self.name_combo.addItem(name)
        self.name_combo.setCurrentText(name)
        self.status.setText(f"Nouvelle personne : {name}. Ajoutez maintenant ses photos de référence.")

    def pick_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Dossier contenant les photos de référence")
        if not folder:
            return
        paths = [os.path.join(folder, f) for f in sorted(os.listdir(folder))
                 if os.path.isfile(os.path.join(folder, f))]
        if paths:
            self._on_files(paths)
        else:
            QMessageBox.information(self, "Enrôlement", "Ce dossier ne contient aucun fichier.")

    def pick_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Photos de référence", "",
            "Images (*.jpg *.jpeg *.png *.webp)")
        if paths:
            self._on_files(paths)

    def _on_files(self, paths):
        added = 0
        for p in paths:
            if p not in self._pending_files:
                self._pending_files.append(p)
                self.file_list.addItem(p)
                added += 1
        self.enroll_btn.setEnabled(bool(self._pending_files))
        self.status.setText(f"{len(self._pending_files)} photo(s) en attente d'enrôlement.")

    def _clear(self):
        self._pending_files.clear()
        self.file_list.clear()
        self.enroll_btn.setEnabled(False)
        self.status.setText("")

    def start_enroll(self):
        name = self.name_combo.currentText().strip()
        if not name:
            QMessageBox.information(self, "Enrôlement", "Indiquez le nom de la personne.")
            return
        if not self._pending_files:
            return
        if self._worker is not None and self._worker.isRunning():
            return
        self.enroll_btn.setEnabled(False)
        self.status.setText(f"Enrôlement de {name} en cours (chargement du modèle au premier lancement)...")
        self.log.emit(f"--- Enrôlement de {name} ({len(self._pending_files)} photo(s)) ---")
        self._worker = workers.EnrollWorker(name, list(self._pending_files), self.engine, self)
        self._worker.log.connect(self.log.emit)
        self._worker.done.connect(self._on_done)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _on_done(self, name, n_added):
        self.status.setText(f"{n_added} visage(s) ajouté(s) pour {name}.")
        self._clear()
        self.refresh_names()
        self.db_updated.emit()

    def _on_failed(self, message):
        self.status.setText("Échec de l'enrôlement.")
        self.enroll_btn.setEnabled(bool(self._pending_files))
        self.log.emit(f"[ERREUR] {message}")
        QMessageBox.warning(self, "Enrôlement", message)
