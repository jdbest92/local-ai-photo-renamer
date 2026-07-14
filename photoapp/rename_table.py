"""Onglet Renommage : apercu editable des propositions avant application."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox,
                               QDoubleSpinBox, QHBoxLayout, QHeaderView,
                               QLabel, QLineEdit, QMessageBox, QPushButton,
                               QSpinBox, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from . import core, workers

COL_CHECK, COL_OLD, COL_NEW, COL_DETAIL = range(4)

STATUS_COLORS = {
    "ok": None,
    "skip": QColor("#808080"),
    "error": QColor("#e74c3c"),
}


class RenameTable(QWidget):
    log = Signal(str)
    renames_applied = Signal()   # pour rafraichir la grille et l'historique

    def __init__(self, engine, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.folder = None
        self._worker = None

        layout = QVBoxLayout(self)

        # --- barre d'options ---
        opts = QHBoxLayout()
        opts.addWidget(QLabel("Modele :"))
        self.model_edit = QLineEdit(core.DEFAULT_MODEL)
        self.model_edit.setMaximumWidth(160)
        opts.addWidget(self.model_edit)

        opts.addWidget(QLabel("Timeout (s) :"))
        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(30, 3600)
        self.timeout_spin.setValue(300)
        opts.addWidget(self.timeout_spin)

        self.faces_check = QCheckBox("Reconnaissance faciale")
        self.faces_check.setChecked(True)
        opts.addWidget(self.faces_check)

        self.reprocess_check = QCheckBox("Retraiter les photos deja horodatees (ajout des noms)")
        opts.addWidget(self.reprocess_check)
        opts.addStretch(1)
        layout.addLayout(opts)

        # --- boutons ---
        btns = QHBoxLayout()
        self.analyze_btn = QPushButton("Analyser le dossier (apercu)")
        self.analyze_btn.clicked.connect(self.start_analysis)
        self.stop_btn = QPushButton("Arreter")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop_analysis)
        self.apply_btn = QPushButton("Appliquer la selection")
        self.apply_btn.setEnabled(False)
        self.apply_btn.clicked.connect(self.apply_selection)
        self.select_all = QCheckBox("Tout cocher/decocher")
        self.select_all.setChecked(True)
        self.select_all.toggled.connect(self._toggle_all)
        btns.addWidget(self.analyze_btn)
        btns.addWidget(self.stop_btn)
        btns.addWidget(self.apply_btn)
        btns.addWidget(self.select_all)
        btns.addStretch(1)
        self.status_label = QLabel("")
        btns.addWidget(self.status_label)
        layout.addLayout(btns)

        # --- tableau ---
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["", "Fichier actuel", "Nouveau nom (editable)", "Detail"])
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(COL_CHECK, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(COL_OLD, QHeaderView.Stretch)
        header.setSectionResizeMode(COL_NEW, QHeaderView.Stretch)
        header.setSectionResizeMode(COL_DETAIL, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.verticalHeader().setVisible(False)
        layout.addWidget(self.table, stretch=1)

    # ---------- parametres externes ----------

    def set_folder(self, folder):
        self.folder = folder
        self.table.setRowCount(0)
        self.apply_btn.setEnabled(False)
        self.status_label.setText("")

    def threshold(self):
        # le seuil global est porte par la fenetre principale
        win = self.window()
        return getattr(win, "face_threshold", core.DEFAULT_THRESHOLD)

    def recursive(self):
        # la case "Sous-dossiers" est portee par la fenetre principale
        chk = getattr(self.window(), "recursive_check", None)
        return bool(chk and chk.isChecked())

    # ---------- analyse ----------

    def start_analysis(self):
        if not self.folder:
            QMessageBox.information(self, "Renommage", "Choisissez d'abord un dossier de photos.")
            return
        if self._worker is not None and self._worker.isRunning():
            return
        self.table.setRowCount(0)
        self.analyze_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.apply_btn.setEnabled(False)
        self.status_label.setText("Analyse en cours...")
        self.log.emit(f"--- Analyse (apercu) de {self.folder} ---")

        self._worker = workers.PlanWorker(
            self.folder,
            self.model_edit.text().strip() or core.DEFAULT_MODEL,
            self.timeout_spin.value(),
            self.faces_check.isChecked(),
            self.threshold(),
            self.reprocess_check.isChecked(),
            self.engine,
            recursive=self.recursive(),
            parent=self,
        )
        self._worker.log.connect(self.log.emit)
        self._worker.proposal.connect(self._add_row)
        self._worker.progress.connect(self._on_progress)
        self._worker.failed.connect(self._on_failed)
        self._worker.finished_ok.connect(self._on_finished)
        self._worker.start()

    def _on_progress(self, current, total, fname):
        self.status_label.setText(f"Analyse : photo {current}/{total} ({fname})")
        win = self.window()
        if hasattr(win, "statusBar"):
            win.statusBar().showMessage(f"Analyse en cours : {current}/{total} : {fname}")

    def stop_analysis(self):
        if self._worker is not None:
            self._worker.stop()
            self.status_label.setText("Arret demande...")

    def _add_row(self, prop):
        row = self.table.rowCount()
        self.table.insertRow(row)

        check_item = QTableWidgetItem()
        check_item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
        check_item.setCheckState(Qt.Checked if prop["check"] else Qt.Unchecked)
        self.table.setItem(row, COL_CHECK, check_item)

        old_item = QTableWidgetItem(prop["old"])
        old_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        self.table.setItem(row, COL_OLD, old_item)

        new_item = QTableWidgetItem(prop["new"])
        if prop["status"] == "ok":
            new_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)
        else:
            new_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        self.table.setItem(row, COL_NEW, new_item)

        detail_item = QTableWidgetItem(prop["detail"])
        detail_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        self.table.setItem(row, COL_DETAIL, detail_item)

        color = STATUS_COLORS.get(prop["status"])
        if color is not None:
            for col in (COL_OLD, COL_NEW, COL_DETAIL):
                self.table.item(row, col).setForeground(color)

        self.table.scrollToBottom()

    def _on_finished(self):
        self.analyze_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        n_ok = sum(1 for r in range(self.table.rowCount())
                   if self.table.item(r, COL_CHECK).checkState() == Qt.Checked)
        self.apply_btn.setEnabled(n_ok > 0)
        self.status_label.setText(f"Analyse terminee : {n_ok} renommage(s) propose(s).")
        self.log.emit(f"Analyse terminee : {n_ok} renommage(s) propose(s). "
                      f"Verifiez et modifiez les noms avant d'appliquer.")

    def _on_failed(self, message):
        self.analyze_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.status_label.setText("Echec de l'analyse.")
        self.log.emit(f"[ERREUR] {message}")
        QMessageBox.warning(self, "Analyse interrompue", message)

    def _toggle_all(self, checked):
        state = Qt.Checked if checked else Qt.Unchecked
        for row in range(self.table.rowCount()):
            item = self.table.item(row, COL_CHECK)
            new_item = self.table.item(row, COL_NEW)
            if item and new_item and (new_item.flags() & Qt.ItemIsEditable):
                item.setCheckState(state)

    # ---------- application ----------

    def apply_selection(self):
        if not self.folder:
            return
        pairs = []
        for row in range(self.table.rowCount()):
            if self.table.item(row, COL_CHECK).checkState() != Qt.Checked:
                continue
            old = self.table.item(row, COL_OLD).text()
            new = self.table.item(row, COL_NEW).text().strip()
            if new and new != old:
                problem = core.validate_filename(new)
                if problem:
                    QMessageBox.warning(self, "Nom invalide",
                                        f"Ligne '{old}' : {problem}")
                    return
                pairs.append((old, new))
        if not pairs:
            QMessageBox.information(self, "Renommage", "Aucun renommage coche.")
            return
        confirm = QMessageBox.question(
            self, "Appliquer le renommage",
            f"Renommer reellement {len(pairs)} fichier(s) ?",
            QMessageBox.Yes | QMessageBox.No)
        if confirm != QMessageBox.Yes:
            return
        self.log.emit(f"--- Application de {len(pairs)} renommage(s) ---")
        renames, errors = core.apply_renames(self.folder, pairs, self.log.emit,
                                             engine=self.engine)
        self.engine.save_cache()
        self.table.setRowCount(0)
        self.apply_btn.setEnabled(False)
        self.status_label.setText(
            f"{len(renames)} fichier(s) renomme(s)"
            + (f", {len(errors)} erreur(s)" if errors else "") + ".")
        self.renames_applied.emit()
        if errors:
            QMessageBox.warning(self, "Renommage",
                                "Certains fichiers n'ont pas pu etre renommes :\n"
                                + "\n".join(errors[:10]))
