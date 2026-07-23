"""Fenetre principale de l'application."""

import os

from PySide6.QtCore import QObject, QSettings, Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox,
                               QFileDialog, QLabel, QMainWindow, QMessageBox,
                               QPlainTextEdit, QProgressBar, QPushButton,
                               QSplitter, QStatusBar, QTabWidget, QToolBar,
                               QWidget)

from . import core, workers
from .enroll_panel import EnrollPanel
from .history_panel import HistoryPanel
from .rename_table import RenameTable
from .thumb_grid import ThumbGridView
from .viewer import PhotoViewer

FILTER_ALL = "Toutes les photos"


class LogBus(QObject):
    """Canal unique et thread-safe vers le journal (les signaux Qt sont thread-safe)."""
    message = Signal(str)


class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Local AI Photo Renamer")
        self.resize(1280, 820)
        self.settings = QSettings("LocalAIPhotoRenamer", "LocalAIPhotoRenamer")

        # --- journal (cree en premier : tout le reste s'y branche) ---
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(5000)
        self.log_bus = LogBus()
        self.log_bus.message.connect(self._append_log)

        # --- moteur de reconnaissance partage ---
        self.engine = core.FaceEngine(log=self.log_bus.message.emit)
        core.set_verbose_sink(self.log_bus.message.emit)

        self.face_threshold = float(self.settings.value("threshold", core.DEFAULT_THRESHOLD))
        self.current_folder = None
        self._scan_worker = None

        # --- barre d'outils ---
        toolbar = QToolBar("Principal")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        open_action = QAction("Ouvrir un dossier...", self)
        open_action.triggered.connect(self.choose_folder)
        toolbar.addAction(open_action)

        self.folder_label = QLabel("  Aucun dossier ouvert  ")
        toolbar.addWidget(self.folder_label)

        self.recursive_check = QCheckBox("Sous-dossiers")
        self.recursive_check.setToolTip(
            "Inclure les photos des sous-dossiers (les fichiers renommés restent "
            "dans leur sous-dossier d'origine).")
        self.recursive_check.setChecked(
            self.settings.value("recursive", False, type=bool))
        self.recursive_check.toggled.connect(self._on_recursive_toggled)
        toolbar.addWidget(self.recursive_check)
        toolbar.addSeparator()

        toolbar.addWidget(QLabel(" Filtre personne : "))
        self.filter_combo = QComboBox()
        self.filter_combo.setMinimumWidth(150)
        self.filter_combo.currentTextChanged.connect(self.apply_person_filter)
        toolbar.addWidget(self.filter_combo)

        self.scan_btn = QPushButton("Analyser tout le dossier (pour le filtre)")
        self.scan_btn.setToolTip(
            "Le filtre s'appuie sur les visages déjà analysés (cache) et sur les noms "
            "présents dans les fichiers. Ce bouton analyse les photos restantes en arrière-plan.")
        self.scan_btn.clicked.connect(self.scan_all_for_filter)
        toolbar.addWidget(self.scan_btn)
        toolbar.addSeparator()

        toolbar.addWidget(QLabel(" Seuil de reconnaissance : "))
        self.threshold_spin = QDoubleSpinBox()
        self.threshold_spin.setRange(0.05, 0.95)
        self.threshold_spin.setSingleStep(0.05)
        self.threshold_spin.setValue(self.face_threshold)
        self.threshold_spin.valueChanged.connect(self._on_threshold)
        toolbar.addWidget(self.threshold_spin)

        # --- zone centrale : grille + visionneuse ---
        self.grid = ThumbGridView()
        self.viewer = PhotoViewer(self.engine)
        self.viewer.set_threshold(self.face_threshold)
        self.viewer.log.connect(self.log_bus.message.emit)
        self.grid.photo_clicked.connect(self.viewer.show_photo)

        top_split = QSplitter(Qt.Horizontal)
        top_split.addWidget(self.grid)
        top_split.addWidget(self.viewer)
        top_split.setStretchFactor(0, 3)
        top_split.setStretchFactor(1, 2)

        # --- onglets du bas ---
        self.rename_tab = RenameTable(self.engine)
        self.rename_tab.log.connect(self.log_bus.message.emit)
        self.rename_tab.renames_applied.connect(self.on_renames_applied)
        self.rename_tab.photo_selected.connect(self.viewer.show_photo)

        self.enroll_tab = EnrollPanel(self.engine)
        self.enroll_tab.log.connect(self.log_bus.message.emit)
        self.enroll_tab.db_updated.connect(self.on_db_updated)

        self.history_tab = HistoryPanel(self.engine)
        self.history_tab.log.connect(self.log_bus.message.emit)
        self.history_tab.undo_done.connect(self.reload_grid)

        tabs = QTabWidget()
        tabs.addTab(self.rename_tab, "Renommage")
        tabs.addTab(self.enroll_tab, "Enrôlement")
        tabs.addTab(self.history_tab, "Historique")
        tabs.addTab(self.log_view, "Journal")
        self.tabs = tabs

        main_split = QSplitter(Qt.Vertical)
        main_split.addWidget(top_split)
        main_split.addWidget(tabs)
        main_split.setStretchFactor(0, 3)
        main_split.setStretchFactor(1, 2)
        self.setCentralWidget(main_split)

        self.setStatusBar(QStatusBar())
        self.progress_bar = QProgressBar()
        self.progress_bar.setMaximumWidth(240)
        self.progress_bar.setFormat("%v/%m (%p%)")
        self.progress_bar.setVisible(False)
        self.statusBar().addPermanentWidget(self.progress_bar)
        self.refresh_filter_names()
        self.log_bus.message.emit("Application démarrée. Ouvrez un dossier de photos pour commencer.")

        last = self.settings.value("last_folder", "")
        if last and os.path.isdir(last):
            self.open_folder(last)

    # ---------- journal ----------

    def _append_log(self, msg):
        self.log_view.appendPlainText(msg)

    # ---------- dossier ----------

    def choose_folder(self):
        start = self.current_folder or self.settings.value("last_folder", "")
        folder = QFileDialog.getExistingDirectory(self, "Choisir le dossier de photos", start)
        if folder:
            self.open_folder(folder)

    def open_folder(self, folder):
        self.current_folder = folder
        self.settings.setValue("last_folder", folder)
        n = self.grid.load_folder(folder, self.recursive_check.isChecked())
        self.folder_label.setText(f"  {folder}  ({n} photo(s))  ")
        self.rename_tab.set_folder(folder)
        self.filter_combo.setCurrentText(FILTER_ALL)
        self.statusBar().showMessage(f"{n} photo(s) chargée(s).", 5000)
        self.log_bus.message.emit(f"Dossier ouvert : {folder} ({n} photo(s))")

    def reload_grid(self):
        if self.current_folder:
            n = self.grid.load_folder(self.current_folder,
                                      self.recursive_check.isChecked())
            self.folder_label.setText(f"  {self.current_folder}  ({n} photo(s))  ")
            self.apply_person_filter(self.filter_combo.currentText())

    def _on_recursive_toggled(self, checked):
        self.settings.setValue("recursive", checked)
        if self.current_folder:
            self.reload_grid()
            self.rename_tab.set_folder(self.current_folder)  # vide l'apercu obsolete
            self.log_bus.message.emit(
                f"Sous-dossiers {'inclus' if checked else 'exclus'} : "
                "grille rechargée, relancez l'analyse si besoin.")

    def on_renames_applied(self):
        self.reload_grid()
        self.history_tab.refresh()

    # ---------- seuil ----------

    def _on_threshold(self, value):
        self.face_threshold = value
        self.settings.setValue("threshold", value)
        self.viewer.set_threshold(value)
        self.apply_person_filter(self.filter_combo.currentText())

    # ---------- filtre par personne ----------

    def refresh_filter_names(self):
        current = self.filter_combo.currentText() or FILTER_ALL
        self.filter_combo.blockSignals(True)
        self.filter_combo.clear()
        self.filter_combo.addItem(FILTER_ALL)
        self.filter_combo.addItems(self.engine.persons())
        self.filter_combo.setCurrentText(current)
        self.filter_combo.blockSignals(False)

    def apply_person_filter(self, person):
        if not person or person == FILTER_ALL:
            self.grid.model_.apply_filter(None)
            return
        threshold = self.face_threshold
        engine = self.engine
        person_lower = person.lower()

        def keep(path):
            stem = os.path.splitext(os.path.basename(path))[0].lower()
            if person_lower in stem.split("_"):
                return True
            names = engine.cached_names(path, threshold)
            return bool(names and person in names)

        self.grid.model_.apply_filter(keep)
        shown = self.grid.model_.rowCount()
        not_cached = sum(1 for p in self.grid.model_.all_paths()
                         if not engine.is_cached(p))
        msg = f"Filtre '{person}' : {shown} photo(s)."
        if not_cached:
            msg += (f" {not_cached} photo(s) pas encore analysée(s) : "
                    f"utilisez 'Analyser tout le dossier' pour un filtre exhaustif.")
        self.statusBar().showMessage(msg, 8000)

    def scan_all_for_filter(self):
        if not self.current_folder:
            QMessageBox.information(self, "Analyse", "Ouvrez d'abord un dossier de photos.")
            return
        if self._scan_worker is not None and self._scan_worker.isRunning():
            self._scan_worker.stop()
            self.scan_btn.setText("Analyser tout le dossier (pour le filtre)")
            return
        paths = self.grid.model_.all_paths()
        self._scan_worker = workers.FilterScanWorker(paths, self.engine, self)
        self._scan_worker.log.connect(self.log_bus.message.emit)
        def _scan_progress(i, n):
            self.statusBar().showMessage(f"Analyse des visages : {i}/{n}")
            self.show_progress(i, n)
        self._scan_worker.progress.connect(_scan_progress)
        self._scan_worker.finished_ok.connect(self._on_scan_done)
        self.scan_btn.setText("Arrêter l'analyse")
        self.log_bus.message.emit("--- Analyse du dossier pour le filtre par personne ---")
        self.statusBar().showMessage(
            "Analyse démarrée (chargement du modèle au premier passage, "
            "progression dans cette barre)...")
        self._scan_worker.start()

    def show_progress(self, current, total):
        """Affiche la barre de progression globale (barre d'état)."""
        self.progress_bar.setMaximum(max(total, 1))
        self.progress_bar.setValue(current)
        self.progress_bar.setVisible(True)

    def hide_progress(self):
        self.progress_bar.setVisible(False)

    def _on_scan_done(self):
        self.hide_progress()
        self.scan_btn.setText("Analyser tout le dossier (pour le filtre)")
        self.statusBar().showMessage(
            "Analyse des visages terminée : le filtre par personne est à jour. "
            "(Pour renommer, utilisez 'Analyser le dossier (aperçu)' dans l'onglet Renommage.)",
            15000)
        self.log_bus.message.emit(
            "Analyse des visages terminée. Cette analyse alimente le filtre par personne ; "
            "elle ne propose pas de renommages (onglet Renommage pour cela).")
        self.apply_person_filter(self.filter_combo.currentText())

    # ---------- base de visages mise a jour ----------

    def on_db_updated(self):
        self.refresh_filter_names()
        self.apply_person_filter(self.filter_combo.currentText())
        if self.viewer.current_path:
            self.viewer.show_photo(self.viewer.current_path)

    # ---------- fermeture ----------

    def closeEvent(self, event):
        for worker in (self._scan_worker, self.rename_tab._worker,
                       self.rename_tab._desc_worker,
                       self.enroll_tab._worker, self.viewer._worker):
            if worker is not None and worker.isRunning():
                if hasattr(worker, "stop"):
                    worker.stop()
                worker.wait(3000)
        self.rename_tab.save_plan()
        self.engine.save_cache()
        super().closeEvent(event)
