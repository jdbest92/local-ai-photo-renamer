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


class ForceDescribeWorker(QThread):
    """Force la description par le modele vision sur une liste de lignes du tableau."""

    log = Signal(str)
    result = Signal(int, str, str, bool)  # ligne, nouveau nom, detail, succes
    progress = Signal(int, int, str)      # index courant, total, nom de fichier
    failed = Signal(str)                  # erreur fatale (Ollama injoignable...)
    finished_ok = Signal()

    def __init__(self, folder, items, model, timeout, use_faces, threshold,
                 engine, parent=None):
        super().__init__(parent)
        self.folder = folder
        self.items = items  # liste de (row, fname)
        self.model = model
        self.timeout = timeout
        self.use_faces = use_faces
        self.threshold = threshold
        self.engine = engine
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        import requests
        try:
            core.check_ollama(self.model, self.log.emit)
        except core.PlanAborted as e:
            self.failed.emit(str(e))
            return
        for i, (row, fname) in enumerate(self.items):
            if self._stop:
                break
            self.log.emit(f"[verbose] description forcée de {fname} "
                          f"({i + 1}/{len(self.items)})")
            self.progress.emit(i + 1, len(self.items), fname)
            try:
                new_name, detail = core.describe_file(
                    self.folder, fname, self.model, self.timeout,
                    self.use_faces, self.threshold, self.engine)
                self.result.emit(row, new_name, detail, True)
            except requests.exceptions.ConnectionError:
                self.engine.save_cache()
                self.failed.emit("Impossible de contacter Ollama (ollama serve est-il lancé ?)")
                return
            except requests.exceptions.ReadTimeout:
                self.result.emit(row, fname,
                                 f"timeout après {self.timeout}s (augmenter le timeout)", False)
            except Exception as e:
                self.result.emit(row, fname, str(e), False)
        self.engine.save_cache()
        self.finished_ok.emit()


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
            self.failed.emit(f"Erreur pendant l'enrôlement : {e}")


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
            self.log.emit("Analyse : toutes les photos sont déjà en cache, rien à faire.")
            self.finished_ok.emit()
            return
        self.log.emit(f"Analyse démarrée : {total} photo(s) à analyser "
                      "(le premier passage charge le modèle, comptez quelques secondes).")
        self.progress.emit(0, total)
        for i, path in enumerate(todo):
            if self._stop:
                break
            self.engine.analyze(path)
            self.progress.emit(i + 1, total)
        self.engine.save_cache()
        self.finished_ok.emit()
