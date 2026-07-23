"""Onglet Historique : lots de renommage passes et annulation du dernier lot."""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QHBoxLayout, QListWidget, QMessageBox,
                               QPushButton, QVBoxLayout, QWidget)

from . import core


class HistoryPanel(QWidget):
    log = Signal(str)
    undo_done = Signal()   # pour rafraichir la grille

    def __init__(self, engine, parent=None):
        super().__init__(parent)
        self.engine = engine
        layout = QVBoxLayout(self)

        btns = QHBoxLayout()
        self.undo_btn = QPushButton("Annuler le dernier lot")
        self.undo_btn.clicked.connect(self.undo_last)
        self.refresh_btn = QPushButton("Rafraîchir")
        self.refresh_btn.clicked.connect(self.refresh)
        btns.addWidget(self.undo_btn)
        btns.addWidget(self.refresh_btn)
        btns.addStretch(1)
        layout.addLayout(btns)

        self.list = QListWidget()
        layout.addWidget(self.list, stretch=1)
        self.refresh()

    def refresh(self):
        self.list.clear()
        history = core.load_history()
        for batch in reversed(history):
            self.list.addItem(f"{batch['date']}  |  {len(batch['renames'])} fichier(s)  |  {batch['folder']}")
            for item in batch["renames"][:8]:
                self.list.addItem(f"      {item['old']}  ->  {item['new']}")
            if len(batch["renames"]) > 8:
                self.list.addItem(f"      ... et {len(batch['renames']) - 8} autre(s)")
        self.undo_btn.setEnabled(bool(history))
        if not history:
            self.list.addItem("Aucun lot de renommage enregistré.")

    def undo_last(self):
        history = core.load_history()
        if not history:
            return
        batch = history[-1]
        confirm = QMessageBox.question(
            self, "Annuler le dernier lot",
            f"Annuler le lot du {batch['date']} "
            f"({len(batch['renames'])} fichier(s)) ?\n\nDossier : {batch['folder']}",
            QMessageBox.Yes | QMessageBox.No)
        if confirm != QMessageBox.Yes:
            return
        self.log.emit("--- Annulation du dernier lot ---")
        undone, errors = core.undo_last_batch(self.log.emit, engine=self.engine)
        self.engine.save_cache()
        self.refresh()
        self.undo_done.emit()
        if errors:
            QMessageBox.warning(self, "Annulation",
                                "Certains fichiers n'ont pas pu être restaurés :\n"
                                + "\n".join(errors[:10]))
