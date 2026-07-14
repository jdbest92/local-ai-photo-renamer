"""Visionneuse : photo en grand + cadres des visages avec nom et score."""

import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QLabel, QSizePolicy, QVBoxLayout, QWidget

from . import workers


def load_qpixmap(path, max_size=1600):
    """Charge une image avec orientation EXIF corrigee."""
    try:
        from PIL import Image, ImageOps
        img = Image.open(path)
        img = ImageOps.exif_transpose(img)
        orig_w, orig_h = img.width, img.height
        img.thumbnail((max_size, max_size))
        scale = img.width / orig_w if orig_w else 1.0
        img = img.convert("RGB")
        data = img.tobytes("raw", "RGB")
        qimg = QImage(data, img.width, img.height, img.width * 3,
                      QImage.Format_RGB888).copy()
        return QPixmap.fromImage(qimg), scale
    except Exception:
        return QPixmap(), 1.0


class FaceOverlayLabel(QLabel):
    """QLabel qui dessine les cadres de visages par-dessus la photo."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumSize(240, 240)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._pixmap = QPixmap()
        self._faces = []          # bbox en coordonnees du pixmap charge
        self._threshold = 0.35

    def set_photo(self, pixmap, faces, threshold):
        self._pixmap = pixmap
        self._faces = faces
        self._threshold = threshold
        self.update()

    def set_faces(self, faces):
        self._faces = faces
        self.update()

    def set_threshold(self, threshold):
        self._threshold = threshold
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._pixmap.isNull():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # Mise a l'echelle pour tenir dans le widget
        scaled = self._pixmap.scaled(self.size(), Qt.KeepAspectRatio,
                                     Qt.SmoothTransformation)
        off_x = (self.width() - scaled.width()) // 2
        off_y = (self.height() - scaled.height()) // 2
        painter.drawPixmap(off_x, off_y, scaled)

        if not self._faces:
            painter.end()
            return
        sx = scaled.width() / self._pixmap.width()
        sy = scaled.height() / self._pixmap.height()
        font = QFont()
        font.setPointSize(10)
        font.setBold(True)
        painter.setFont(font)

        for face in self._faces:
            x1, y1, x2, y2 = face["bbox"]
            rx = off_x + x1 * sx
            ry = off_y + y1 * sy
            rw = (x2 - x1) * sx
            rh = (y2 - y1) * sy
            recognized = (face["best_name"] is not None
                          and face["best_score"] >= self._threshold)
            color = QColor("#2ecc71") if recognized else QColor("#e74c3c")
            painter.setPen(QPen(color, 2))
            painter.drawRect(int(rx), int(ry), int(rw), int(rh))

            if face["best_name"] is not None:
                if recognized:
                    label = f"{face['best_name']} ({face['best_score']:.2f})"
                else:
                    label = f"? ({face['best_name']} {face['best_score']:.2f})"
            else:
                label = "? (base vide)"
            metrics = painter.fontMetrics()
            tw = metrics.horizontalAdvance(label) + 8
            th = metrics.height() + 4
            ty = ry - th if ry - th > 0 else ry + rh
            painter.fillRect(int(rx), int(ty), tw, th, color)
            painter.setPen(QPen(QColor("#ffffff")))
            painter.drawText(int(rx) + 4, int(ty) + th - 6, label)
        painter.end()


class PhotoViewer(QWidget):
    """Panneau de droite : photo cliquee + visages, detection a la demande."""

    log = Signal(str)

    def __init__(self, engine, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.threshold = 0.35
        self.current_path = None
        self._worker = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        self.overlay = FaceOverlayLabel()
        self.info = QLabel("Cliquez sur une photo pour l'afficher.")
        self.info.setWordWrap(True)
        layout.addWidget(self.overlay, stretch=1)
        layout.addWidget(self.info)

        self._scale = 1.0

    def set_threshold(self, value):
        self.threshold = value
        self.overlay.set_threshold(value)
        if self.current_path:
            self._refresh_info()

    def show_photo(self, path):
        self.current_path = path
        self._faces_raw = []
        pixmap, self._scale = load_qpixmap(path)
        self.overlay.set_photo(pixmap, [], self.threshold)
        self.info.setText(f"{os.path.basename(path)} : detection des visages...")
        self._start_detection(path)

    def _start_detection(self, path):
        if self._worker is not None and self._worker.isRunning():
            self._worker.result.disconnect()
            self._worker.failed.disconnect()
        self._worker = workers.DetectWorker(path, self.engine, self)
        self._worker.result.connect(self._on_faces)
        self._worker.failed.connect(self._on_failed)
        self._worker.log.connect(self.log.emit)
        self._worker.start()

    def _scaled_faces(self, faces):
        """Ramene les bbox (coordonnees image pleine taille) a l'echelle du pixmap charge."""
        out = []
        for f in faces:
            x1, y1, x2, y2 = f["bbox"]
            g = dict(f)
            g["bbox"] = [x1 * self._scale, y1 * self._scale,
                         x2 * self._scale, y2 * self._scale]
            out.append(g)
        return out

    def _on_faces(self, path, faces):
        if path != self.current_path:
            return
        self._faces_raw = faces
        self.overlay.set_faces(self._scaled_faces(faces))
        self._refresh_info()

    def _refresh_info(self):
        faces = getattr(self, "_faces_raw", [])
        if not faces:
            self.info.setText(f"{os.path.basename(self.current_path)} : aucun visage detecte.")
            return
        parts = []
        for f in faces:
            if f["best_name"] and f["best_score"] >= self.threshold:
                parts.append(f"{f['best_name']} ({f['best_score']:.2f})")
            elif f["best_name"]:
                parts.append(f"non reconnu (meilleur : {f['best_name']} {f['best_score']:.2f})")
            else:
                parts.append("non reconnu (base vide)")
        self.info.setText(f"{os.path.basename(self.current_path)} : "
                          f"{len(faces)} visage(s). " + " | ".join(parts))

    def _on_failed(self, path, message):
        if path == self.current_path:
            self.info.setText(f"Erreur de detection : {message}")
