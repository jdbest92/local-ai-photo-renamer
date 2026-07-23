"""Onglet Renommage : apercu editable des propositions avant application."""

import json
import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox,
                               QDoubleSpinBox, QHBoxLayout, QHeaderView,
                               QLabel, QLineEdit, QMessageBox, QPushButton,
                               QSpinBox, QTableWidget, QTableWidgetItem,
                               QTableWidgetSelectionRange, QVBoxLayout, QWidget)

from . import core, workers

COL_CHECK, COL_OLD, COL_NEW, COL_DETAIL = range(4)

# Propositions non appliquees, conservees d'une session a l'autre.
PLAN_PATH = os.path.join(core.APP_DIR, "pending_plan.json")

STATUS_COLORS = {
    "ok": None,
    "skip": QColor("#808080"),
    "error": QColor("#e74c3c"),
}


class RenameTable(QWidget):
    log = Signal(str)
    renames_applied = Signal()   # pour rafraichir la grille et l'historique
    photo_selected = Signal(str)  # chemin absolu, pour la visionneuse

    def __init__(self, engine, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.folder = None
        self._worker = None
        self._desc_worker = None

        layout = QVBoxLayout(self)

        # --- barre d'options ---
        opts = QHBoxLayout()
        opts.addWidget(QLabel("Modèle :"))
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

        self.reprocess_check = QCheckBox("Retraiter les photos déjà horodatées (ajout des noms)")
        opts.addWidget(self.reprocess_check)
        opts.addStretch(1)
        layout.addLayout(opts)

        # --- boutons ---
        btns = QHBoxLayout()
        self.analyze_btn = QPushButton("Analyser le dossier (aperçu)")
        self.analyze_btn.clicked.connect(self.start_analysis)
        self.stop_btn = QPushButton("Arrêter")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop_analysis)
        self.apply_btn = QPushButton("Appliquer la sélection")
        self.apply_btn.setEnabled(False)
        self.apply_btn.clicked.connect(self.apply_selection)
        self.describe_btn = QPushButton("Décrire les photos choisies (forcer le modèle)")
        self.describe_btn.setToolTip(
            "Envoie les photos cochées ou surlignées au modèle vision, même si leur nom "
            "est jugé explicite (utile quand un format de nom n'est pas reconnu).")
        self.describe_btn.setEnabled(False)
        self.describe_btn.clicked.connect(self.force_describe)
        self.select_all = QCheckBox("Tout cocher/décocher")
        self.select_all.setChecked(True)
        self.select_all.toggled.connect(self._toggle_all)
        btns.addWidget(self.analyze_btn)
        btns.addWidget(self.stop_btn)
        btns.addWidget(self.apply_btn)
        btns.addWidget(self.describe_btn)
        btns.addWidget(self.select_all)
        btns.addStretch(1)
        self.status_label = QLabel("")
        btns.addWidget(self.status_label)
        layout.addLayout(btns)

        # --- tableau ---
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["", "Fichier actuel", "Nouveau nom (éditable)", "Détail"])
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(COL_CHECK, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(COL_OLD, QHeaderView.Stretch)
        header.setSectionResizeMode(COL_NEW, QHeaderView.Stretch)
        header.setSectionResizeMode(COL_DETAIL, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        self.table.itemChanged.connect(self._on_item_changed)
        self.table.verticalHeader().setVisible(False)
        layout.addWidget(self.table, stretch=1)

    # ---------- parametres externes ----------

    def set_folder(self, folder):
        self.folder = folder
        self.table.setRowCount(0)
        self.apply_btn.setEnabled(False)
        self.status_label.setText("")
        self.restore_plan()

    # ---------- persistance des propositions ----------

    def save_plan(self):
        """Enregistre les propositions courantes (appele a la fermeture et
        apres chaque analyse). Fichier supprime si le tableau est vide."""
        if not self.folder or self.table.rowCount() == 0:
            try:
                os.remove(PLAN_PATH)
            except OSError:
                pass
            return
        rows = []
        for r in range(self.table.rowCount()):
            rows.append({
                "old": self.table.item(r, COL_OLD).text(),
                "new": self.table.item(r, COL_NEW).text(),
                "detail": self.table.item(r, COL_DETAIL).text(),
                "status": self.table.item(r, COL_CHECK).data(Qt.UserRole) or "ok",
                "check": self.table.item(r, COL_CHECK).checkState() == Qt.Checked,
            })
        try:
            with open(PLAN_PATH, "w", encoding="utf-8") as f:
                json.dump({"folder": self.folder, "rows": rows}, f,
                          ensure_ascii=False, indent=1)
        except OSError as e:
            self.log.emit(f"[ERREUR] sauvegarde des propositions : {e}")

    def restore_plan(self):
        """Recharge les propositions de la session precedente si le meme
        dossier est ouvert, en ignorant les fichiers disparus."""
        try:
            with open(PLAN_PATH, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return
        if data.get("folder") != self.folder:
            return
        restored = 0
        for row in data.get("rows", []):
            old = row.get("old", "")
            if not old or not os.path.exists(os.path.join(self.folder, old)):
                continue  # fichier disparu ou deja renomme
            self._add_row(row)
            restored += 1
        if restored:
            n_ok = sum(1 for r in range(self.table.rowCount())
                       if self.table.item(r, COL_CHECK).checkState() == Qt.Checked)
            self.apply_btn.setEnabled(n_ok > 0)
            self.describe_btn.setEnabled(True)
            self.status_label.setText(
                f"{restored} proposition(s) restaurée(s) de la session précédente.")
            self.log.emit(f"{restored} proposition(s) de renommage restaurée(s) "
                          "de la session précédente.")

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
        if hasattr(win, "show_progress"):
            win.show_progress(current, total)

    def stop_analysis(self):
        if self._worker is not None:
            self._worker.stop()
            self.status_label.setText("Arrêt demandé...")

    def _add_row(self, prop):
        row = self.table.rowCount()
        self.table.insertRow(row)

        check_item = QTableWidgetItem()
        check_item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
        check_item.setCheckState(Qt.Checked if prop["check"] else Qt.Unchecked)
        check_item.setData(Qt.UserRole, prop.get("status", "ok"))
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
        self.describe_btn.setEnabled(self.table.rowCount() > 0)
        statuses = [self.table.item(r, COL_CHECK).data(Qt.UserRole)
                    for r in range(self.table.rowCount())]
        n_skip, n_err = statuses.count("skip"), statuses.count("error")
        bilan = (f"Analyse terminée : {n_ok} renommage(s) proposé(s), "
                 f"{n_skip} ignoré(s), {n_err} erreur(s).")
        self.status_label.setText(bilan)
        self.log.emit(bilan + " Vérifiez et modifiez les noms avant d'appliquer.")
        self._hide_progress()
        self.save_plan()

    # ---------- apercu dans la visionneuse ----------

    def _selected_rows(self):
        return sorted({i.row() for i in self.table.selectedIndexes()})

    def _on_selection_changed(self):
        rows = self._selected_rows()
        if rows and self.folder:
            old = self.table.item(rows[0], COL_OLD).text()
            self.photo_selected.emit(os.path.join(self.folder, old))

    def _on_item_changed(self, item):
        """Cocher/decocher une case surligne/desurligne la ligne."""
        if item.column() != COL_CHECK:
            return
        row = item.row()
        # ligne encore en cours de construction (_add_row) : ignorer
        if self.table.item(row, COL_DETAIL) is None:
            return
        checked = item.checkState() == Qt.Checked
        rng = QTableWidgetSelectionRange(row, 0, row, self.table.columnCount() - 1)
        self.table.setRangeSelected(rng, checked)

    # ---------- description forcee ----------

    def force_describe(self):
        if not self.folder:
            return
        if self._desc_worker is not None and self._desc_worker.isRunning():
            self._desc_worker.stop()
            self.describe_btn.setText("Décrire les photos choisies (forcer le modèle)")
            return
        checked = {r for r in range(self.table.rowCount())
                   if self.table.item(r, COL_CHECK).checkState() == Qt.Checked}
        rows = sorted(checked | set(self._selected_rows()))
        if not rows:
            QMessageBox.information(
                self, "Description forcée",
                "Cochez ou surlignez d'abord la ou les lignes à décrire.")
            return
        confirm = QMessageBox.question(
            self, "Description forcée",
            f"Envoyer {len(rows)} photo(s) au modèle vision ?",
            QMessageBox.Yes | QMessageBox.No)
        if confirm != QMessageBox.Yes:
            return
        items = [(r, self.table.item(r, COL_OLD).text()) for r in rows]
        self._desc_worker = workers.ForceDescribeWorker(
            self.folder, items,
            self.model_edit.text().strip() or core.DEFAULT_MODEL,
            self.timeout_spin.value(),
            self.faces_check.isChecked(),
            self.threshold(),
            self.engine, self,
        )
        self._desc_ok = 0
        self._desc_fail = 0
        self._desc_worker.log.connect(self.log.emit)
        self._desc_worker.progress.connect(self._on_desc_progress)
        self._desc_worker.result.connect(self._on_desc_result)
        self._desc_worker.failed.connect(self._on_desc_failed)
        self._desc_worker.finished_ok.connect(self._on_desc_finished)
        self.describe_btn.setText("Arrêter la description")
        self.status_label.setText(f"Description forcée de {len(items)} photo(s)...")
        self.log.emit(f"--- Description forcée de {len(items)} photo(s) ---")
        self._desc_worker.start()

    def _on_desc_progress(self, current, total, fname):
        self.status_label.setText(f"Description forcée : {current}/{total} ({fname})")
        win = self.window()
        if hasattr(win, "statusBar"):
            win.statusBar().showMessage(
                f"Description forcée : {current}/{total} : {fname}")
        if hasattr(win, "show_progress"):
            win.show_progress(current, total)

    def _on_desc_result(self, row, new_name, detail, ok):
        if ok:
            self._desc_ok += 1
        else:
            self._desc_fail += 1
        new_item = self.table.item(row, COL_NEW)
        detail_item = self.table.item(row, COL_DETAIL)
        check_item = self.table.item(row, COL_CHECK)
        if ok:
            new_item.setText(new_name)
            new_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)
            detail_item.setText(f"description forcee : {detail}")
            check_item.setCheckState(Qt.Checked)
            check_item.setData(Qt.UserRole, "ok")
            for col in (COL_OLD, COL_NEW, COL_DETAIL):
                self.table.item(row, col).setData(Qt.ForegroundRole, None)
        else:
            detail_item.setText(detail)
            check_item.setData(Qt.UserRole, "error")
            for col in (COL_OLD, COL_NEW, COL_DETAIL):
                self.table.item(row, col).setForeground(STATUS_COLORS["error"])

    def _on_desc_finished(self):
        self.describe_btn.setText("Décrire les photos choisies (forcer le modèle)")
        n_ok = sum(1 for r in range(self.table.rowCount())
                   if self.table.item(r, COL_CHECK).checkState() == Qt.Checked)
        self.apply_btn.setEnabled(n_ok > 0)
        self.status_label.setText(
            f"Description forcée terminée : {self._desc_ok} réussite(s), "
            f"{self._desc_fail} échec(s).")
        self.log.emit(f"Description forcée terminée : {self._desc_ok} réussite(s), "
                      f"{self._desc_fail} échec(s).")
        self._hide_progress()
        self.save_plan()

    def _on_desc_failed(self, message):
        self.describe_btn.setText("Décrire les photos choisies (forcer le modèle)")
        self._hide_progress()
        self.status_label.setText("Échec de la description forcée.")
        self.log.emit(f"[ERREUR] {message}")
        QMessageBox.warning(self, "Description interrompue", message)

    def _hide_progress(self):
        win = self.window()
        if hasattr(win, "hide_progress"):
            win.hide_progress()

    def _on_failed(self, message):
        self.analyze_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self._hide_progress()
        self.status_label.setText("Échec de l'analyse.")
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
            QMessageBox.information(self, "Renommage", "Aucun renommage coché.")
            return
        confirm = QMessageBox.question(
            self, "Appliquer le renommage",
            f"Renommer réellement {len(pairs)} fichier(s) ?",
            QMessageBox.Yes | QMessageBox.No)
        if confirm != QMessageBox.Yes:
            return
        self.log.emit(f"--- Application de {len(pairs)} renommage(s) ---")
        renames, errors = core.apply_renames(self.folder, pairs, self.log.emit,
                                             engine=self.engine)
        self.engine.save_cache()
        self.table.setRowCount(0)
        self.save_plan()
        self.apply_btn.setEnabled(False)
        self.status_label.setText(
            f"{len(renames)} fichier(s) renommé(s)"
            + (f", {len(errors)} erreur(s)" if errors else "") + ".")
        self.renames_applied.emit()
        if errors:
            QMessageBox.warning(self, "Renommage",
                                "Certains fichiers n'ont pas pu être renommés :\n"
                                + "\n".join(errors[:10]))
