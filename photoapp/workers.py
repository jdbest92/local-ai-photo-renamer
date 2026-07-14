"""Threads de travail (QThread) pour ne jamais bloquer l'interface."""

from PySide6.QtCore import QThread, Signal

from . import core


class PlanWorker(QThread):
    """Analyse un dossier et produit les propositions de renommage (dry-run)."""

    log = Signal(str)
    proposal = Signal(dict)
    progress = Signal(int, int, str)  # index courant, total, nom de fichier
    failed = Signal(str)
    finished_ok = Signal()

    def __init__(self, folder, model, timeout, use_faces, threshold,
                 reprocess_faces, engine, recursive=False, parent=None):
        super().__init__(parent)
        self.folder = folder
        self.model = model
        self.timeout = timeout
        self.use_faces = use_faces
        self.threshold = threshold
        self.reprocess_faces = reprocess_faces
        self.engine = engine
        self.recursive = recursive
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        try:
            core.build_plan(
                self.folder, self.model, self.timeout, self.use_faces,
                self.threshold, self.reprocess_faces, self.engine,
                log=self.log.emit, emit=self.proposal.emit,
                should_stop=lambda: self._stop,
                progress=self.progress.emit,
                recursive=self.recursive,
            )
            self.engine.save_cache()
            self.finished_ok.emit()
        except core.PlanAborted as e:
            self.engine.save_cache()
            self.failed.emit(str(e))
        except Exception as e:
            self.engine.save_cache()
            self.failed.emit(f"Erreur inattendue pendant l'analyse : {e}")


class DetectWorker(QThread):
    """Detecte les visages d'une seule photo (pour la visionneuse)."""

    log = Signal(str)
    result = Signal(str, list)  # chemin, liste de visages
    failed = Signal(str, str)

    def __init__(self, path, engine, parent=None):
        super().__init__(parent)
        self.path = path
        self.engine = engine

    def run(self):
        try:
            faces = self.engine.analyze(self.path)
            self.engine.save_cache()
            self.result.emit(self.path, faces)
        except Exception as e:
            self.failed.emit(self.path, str(e))


class EnrollWorker(QThread):
    """Copie des photos de reference et enrole les visages."""

    log = Signal(str)
    done = Signal(str, int)  # nom, nombre de visages ajoutes
    failed = Signal(str)

    def __init__(self, name, files, engine, parent=None):
        super().__init__(parent)
        self.name = name
        self.files = files
        self.engine = engine

    def run(self):
        try:
            n = core.enroll_person(self.name, self.files, self.engine, log=self.log.emit)
            self.done.emit(self.name, n)
        except Exception as e:
            self.failed.emit(f"Erreur pendant l'enrolement : {e}")


class FilterScanWorker(QThread):
    """Analyse en arriere-plan les photos non encore en cache, pour le filtre par personne."""

    log = Signal(str)
    progress = Signal(int, int)
    finished_ok = Signal()

    def __init__(self, paths, engine, parent=None):
        super().__init__(parent)
        self.paths = paths
        self.engine = engine
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        todo = [p for p in self.paths if not self.engine.is_cached(p)]
        total = len(todo)
        if not total:
            self.log.emit("Analyse : toutes les photos sont deja en cache, rien a faire.")
            self.finished_ok.emit()
            return
        self.log.emit(f"Analyse demarree : {total} photo(s) a analyser "
                      "(le premier passage charge le modele, comptez quelques secondes).")
        self.progress.emit(0, total)
        for i, path in enumerate(todo):
            if self._stop:
                break
            self.engine.analyze(path)
            self.progress.emit(i + 1, total)
        self.engine.save_cache()
        self.finished_ok.emit()
