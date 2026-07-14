"""Grille de vignettes avec chargement paresseux (QListView + QThreadPool)."""

import os

from PySide6.QtCore import (QAbstractListModel, QModelIndex, QObject, QRunnable,
                            QSize, Qt, QThreadPool, Signal)
from PySide6.QtGui import QColor, QIcon, QImage, QPixmap
from PySide6.QtWidgets import QListView

THUMB_SIZE = 160
GRID_SIZE = 190


class _ThumbSignals(QObject):
    loaded = Signal(str, QImage)


class _ThumbJob(QRunnable):
    """Charge et reduit une image dans le pool de threads (jamais de QPixmap ici)."""

    def __init__(self, path, signals):
        super().__init__()
        self.path = path
        self.signals = signals
        self.setAutoDelete(True)

    def run(self):
        try:
            from PIL import Image, ImageOps
            img = Image.open(self.path)
            img = ImageOps.exif_transpose(img)
            img.thumbnail((THUMB_SIZE, THUMB_SIZE))
            img = img.convert("RGB")
            data = img.tobytes("raw", "RGB")
            qimg = QImage(data, img.width, img.height,
                          img.width * 3, QImage.Format_RGB888).copy()
        except Exception:
            qimg = QImage()
        self.signals.loaded.emit(self.path, qimg)


class PhotoListModel(QAbstractListModel):
    """Modele de la grille : ne charge une vignette que lorsqu'elle devient visible."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._paths = []          # chemins affiches (apres filtre)
        self._all_paths = []      # tous les chemins du dossier
        self._thumbs = {}         # chemin -> QPixmap
        self._pending = set()
        self._signals = _ThumbSignals()
        self._signals.loaded.connect(self._on_loaded)
        self._pool = QThreadPool()
        self._pool.setMaxThreadCount(max(2, (os.cpu_count() or 4) // 2))
        self._placeholder = QPixmap(THUMB_SIZE, THUMB_SIZE)
        self._placeholder.fill(QColor("#3a3a3a"))

    # ---------- alimentation ----------

    def set_folder_paths(self, paths):
        self.beginResetModel()
        self._all_paths = list(paths)
        self._paths = list(paths)
        self._thumbs.clear()
        self._pending.clear()
        self.endResetModel()

    def apply_filter(self, keep_predicate):
        """keep_predicate(path) -> bool ; None pour tout afficher."""
        self.beginResetModel()
        if keep_predicate is None:
            self._paths = list(self._all_paths)
        else:
            self._paths = [p for p in self._all_paths if keep_predicate(p)]
        self.endResetModel()

    def all_paths(self):
        return list(self._all_paths)

    def path_at(self, index):
        if 0 <= index.row() < len(self._paths):
            return self._paths[index.row()]
        return None

    # ---------- modele Qt ----------

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._paths)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        path = self._paths[index.row()]
        if role == Qt.DisplayRole:
            name = os.path.basename(path)
            return name if len(name) <= 28 else name[:25] + "..."
        if role == Qt.ToolTipRole:
            return os.path.basename(path)
        if role == Qt.DecorationRole:
            pix = self._thumbs.get(path)
            if pix is not None:
                return QIcon(pix)
            if path not in self._pending:
                self._pending.add(path)
                self._pool.start(_ThumbJob(path, self._signals))
            return QIcon(self._placeholder)
        return None

    def _on_loaded(self, path, qimg):
        self._pending.discard(path)
        if qimg.isNull():
            return
        self._thumbs[path] = QPixmap.fromImage(qimg)
        try:
            row = self._paths.index(path)
        except ValueError:
            return
        idx = self.index(row)
        self.dataChanged.emit(idx, idx, [Qt.DecorationRole])


class ThumbGridView(QListView):
    """Vue en grille configuree pour le chargement paresseux."""

    photo_clicked = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.model_ = PhotoListModel(self)
        self.setModel(self.model_)
        self.setViewMode(QListView.IconMode)
        self.setResizeMode(QListView.Adjust)
        self.setUniformItemSizes(True)
        self.setIconSize(QSize(THUMB_SIZE, THUMB_SIZE))
        self.setGridSize(QSize(GRID_SIZE, GRID_SIZE + 24))
        self.setLayoutMode(QListView.Batched)
        self.setBatchSize(40)
        self.setSpacing(6)
        self.setWordWrap(True)
        self.setSelectionMode(QListView.SingleSelection)
        self.clicked.connect(self._on_clicked)

    def _on_clicked(self, index):
        path = self.model_.path_at(index)
        if path:
            self.photo_clicked.emit(path)

    def load_folder(self, folder, recursive=False):
        from . import core
        paths = [os.path.join(folder, f) for f in core.list_photos(folder, recursive)]
        self.model_.set_folder_paths(paths)
        return len(paths)
