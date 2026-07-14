"""Logique metier de l'application (aucune dependance a l'interface graphique).

Reutilise directement les fonctions des scripts existants rename_photos.py
et enroll_faces.py, sans les modifier.
"""

import json
import os
import re
import shutil
import sys
import threading
import time
from datetime import datetime

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

import rename_photos as rp
import enroll_faces as ef

FACE_CACHE_PATH = os.path.join(APP_DIR, "face_cache.json")
HISTORY_PATH = os.path.join(APP_DIR, "rename_history.json")
REFERENCE_DIR = os.path.join(APP_DIR, "Reference")
IMAGE_EXTENSIONS = rp.EXTENSIONS
DEFAULT_THRESHOLD = rp.FACE_MATCH_THRESHOLD
DEFAULT_MODEL = "gemma4:12b"
HISTORY_MAX_BATCHES = 30


# Complement a la regle du script CLI : celle-ci ne reconnait pas les noms
# a plusieurs groupes de chiffres comme IMG_20260613_082740 (format Android),
# ni WIN_20260101_12_30_45, VID_20260101_123045 ou IMG-20230703-WA0072 (WhatsApp).
GENERIC_EXTRA_RE = re.compile(
    r"^(img|dsc|dscn|dscf|photo|screenshot|pxl|mvimg|image|pic|vid|win|wp|p)"
    r"([_\-](?:wa)?\d+)+$",
    re.IGNORECASE,
)

# Approche generale : un nom est generique s'il n'est compose que de chiffres,
# de separateurs et de mots-jetons connus des appareils/applications.
# Le moindre mot inconnu ("plage", "anniversaire", "mariage"...) rend le nom
# explicite : c'est ce qui garantit l'absence de faux positifs.
GENERIC_TOKENS = {
    # appareils photo / telephones
    "img", "image", "photo", "pic", "pict", "picture", "dsc", "dscn", "dscf",
    "imgp", "cimg", "sdc", "hpim", "kic", "sam", "pxl", "mvimg", "pano",
    "burst", "hdr", "dcim", "p", "e",
    # video / webcam
    "vid", "video", "mov", "win", "wp", "cam", "webcam", "gopr", "gp", "dji",
    # captures d'ecran
    "screenshot", "screen", "capture", "snap",
    # messageries / telechargements
    "wa", "fb", "msg", "received", "resized", "snapchat", "signal",
    "telegram", "unnamed", "download", "scan",
    # copies / retouches
    "copie", "copy", "edit", "edited",
}

_RUN_RE = re.compile(r"[0-9]+|[a-zà-öø-ÿ]+")
_SEP_RE = re.compile(r"[\s_\-~.()\[\]+]+")


def is_generic_name(stem):
    """True si le nom ressemble a un nom auto-genere par un appareil.

    Trois etages : les deux regex historiques (formats connus), puis une
    analyse par jetons : le nom est decoupe en suites de lettres et de
    chiffres ; il est generique si chaque suite de lettres est un jeton
    d'appareil connu (GENERIC_TOKENS) et qu'il contient au moins 3 chiffres.
    """
    stem = stem.strip()
    if rp.is_generic_name(stem) or GENERIC_EXTRA_RE.match(stem):
        return True

    lower = stem.lower()
    runs = _RUN_RE.findall(lower)
    # Caracteres imprevus (apostrophes, symboles...) : prudence, nom juge explicite.
    if "".join(runs) != _SEP_RE.sub("", lower):
        return False
    digit_count = sum(len(r) for r in runs if r.isdigit())
    if digit_count < 3:
        return False
    return all(r.isdigit() or r in GENERIC_TOKENS for r in runs)


# Horodatage AAAAMMJJ[_HHMMSS] present dans le nom (WhatsApp, Android, Pixel...).
_NAME_TS_RE = re.compile(r"(?<!\d)(\d{8})(?:[_\-](\d{6})\d{0,3})?(?!\d)")


def timestamp_from_name(stem):
    """Extrait un horodatage plausible du nom de fichier, sinon None.

    Ex. : IMG-20230703-WA0072 -> 20230703_000000 ;
          PXL_20230703_143551123 -> 20230703_143551.
    """
    for m in _NAME_TS_RE.finditer(stem):
        date_part, time_part = m.group(1), m.group(2) or "000000"
        try:
            dt = datetime.strptime(date_part + time_part, "%Y%m%d%H%M%S")
        except ValueError:
            continue
        if 1990 <= dt.year <= datetime.now().year + 1:
            return f"{date_part}_{time_part}"
    return None


def set_verbose_sink(callback):
    """Route les messages [verbose] des scripts existants vers le journal de l'appli."""
    rp.VERBOSE = True
    rp.vprint = lambda msg: callback(f"[verbose] {msg}")


def list_photos(folder, recursive=False):
    """Liste triee des fichiers image du dossier.

    En mode recursif, renvoie des chemins relatifs au dossier (sous-dossiers
    inclus, racine comprise) ; les dossiers caches (.xxx) sont ignores.
    """
    if not recursive:
        try:
            return sorted(
                f for f in os.listdir(folder)
                if os.path.splitext(f)[1].lower() in IMAGE_EXTENSIONS
            )
        except OSError:
            return []
    result = []
    try:
        for root, dirs, files in os.walk(folder):
            dirs[:] = sorted(d for d in dirs if not d.startswith("."))
            for f in files:
                if os.path.splitext(f)[1].lower() in IMAGE_EXTENSIONS:
                    result.append(os.path.relpath(os.path.join(root, f), folder))
    except OSError:
        pass
    return sorted(result)


def imread_unicode(path):
    """cv2.imread compatible avec les chemins accentues sous Windows."""
    import cv2
    import numpy as np
    try:
        data = np.fromfile(path, dtype=np.uint8)
        if data.size == 0:
            return None
        return cv2.imdecode(data, cv2.IMREAD_COLOR)
    except Exception:
        return None


class PlanAborted(Exception):
    """Levee quand l'analyse doit s'arreter completement (ex : Ollama injoignable)."""


def check_ollama(model, log):
    """Verifie avant l'analyse qu'Ollama repond et que le modele est installe.

    - Serveur injoignable : avertit dans le journal et renvoie False (l'analyse
      continue, seuls les noms generiques echoueront ; utile pour un simple
      horodatage sans Ollama).
    - Serveur OK mais modele absent : leve PlanAborted immediatement, plutot
      qu'une erreur par photo.
    """
    import requests
    base = rp.OLLAMA_URL.rsplit("/api/", 1)[0]
    try:
        resp = requests.get(f"{base}/api/tags", timeout=5)
        resp.raise_for_status()
        models = resp.json().get("models", [])
    except Exception:
        log("[attention] Ollama injoignable : les photos a nom generique ne pourront "
            "pas etre decrites (lancez 'ollama serve' puis relancez l'analyse).")
        return False
    names = {m.get("name", "") for m in models}
    if model not in names and f"{model}:latest" not in names:
        raise PlanAborted(
            f"Le modele '{model}' n'est pas installe dans Ollama "
            f"(installes : {', '.join(sorted(names)) or 'aucun'}). "
            f"Telechargez-le avec : ollama pull {model}")
    log(f"Ollama OK : modele '{model}' disponible.")
    return True


class FaceEngine:
    """Detection et reconnaissance de visages, avec cache persistant.

    Le cache stocke bbox + embedding par photo (invalide si mtime change) ;
    la correspondance avec la base est recalculee a chaque appel, ce qui permet
    de changer le seuil ou de re-enroler sans refaire la detection.
    """

    def __init__(self, db_path=None, use_gpu=False, log=None):
        self.db_path = db_path or rp.DEFAULT_FACES_DB
        self.use_gpu = use_gpu
        self.log = log or (lambda msg: None)
        self._app = None
        self._lock = threading.Lock()
        self._dirty = False
        self.db = {}
        self.cache = self._load_cache()
        self.reload_db()

    # ---------- base de visages ----------

    def reload_db(self):
        import numpy as np
        self.db = {}
        if os.path.isfile(self.db_path):
            try:
                raw = rp.load_faces_db(self.db_path)
            except Exception as e:
                self.log(f"[ERREUR] lecture de {self.db_path} : {e}")
                return
            for name, vecs in raw.items():
                if len(vecs):
                    norms = np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-8
                    self.db[name] = vecs / norms

    def persons(self):
        return sorted(self.db.keys())

    # ---------- cache ----------

    def _load_cache(self):
        if os.path.isfile(FACE_CACHE_PATH):
            try:
                with open(FACE_CACHE_PATH, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def save_cache(self):
        with self._lock:
            if not self._dirty:
                return
            try:
                with open(FACE_CACHE_PATH, "w", encoding="utf-8") as f:
                    json.dump(self.cache, f)
                self._dirty = False
            except OSError as e:
                self.log(f"[ERREUR] sauvegarde du cache visages : {e}")

    def forget(self, path):
        """Retire une photo du cache (utile apres renommage : la cle change)."""
        with self._lock:
            if self.cache.pop(os.path.abspath(path), None) is not None:
                self._dirty = True

    def rename_key(self, old_path, new_path):
        with self._lock:
            ent = self.cache.pop(os.path.abspath(old_path), None)
            if ent is not None:
                try:
                    ent["mtime"] = os.path.getmtime(new_path)
                except OSError:
                    pass
                self.cache[os.path.abspath(new_path)] = ent
                self._dirty = True

    # ---------- modele ----------

    def ensure_app(self):
        with self._lock:
            if self._app is None:
                self.log("Chargement du modele insightface (buffalo_l)...")
                t0 = time.monotonic()
                from insightface.app import FaceAnalysis
                self._app = FaceAnalysis(name="buffalo_l")
                self._app.prepare(ctx_id=0 if self.use_gpu else -1, det_size=(640, 640))
                self.log(f"Modele insightface charge en {time.monotonic() - t0:.1f}s")
            return self._app

    def detect_raw(self, img):
        """Detection brute (thread-safe) sur une image deja chargee."""
        app = self.ensure_app()
        with self._lock:
            return app.get(img)

    # ---------- analyse ----------

    def _match(self, emb_norm):
        best_name, best_score = None, -1.0
        for name, vecs in self.db.items():
            score = float((vecs @ emb_norm).max())
            if score > best_score:
                best_name, best_score = name, score
        return best_name, best_score

    def is_cached(self, path):
        key = os.path.abspath(path)
        ent = self.cache.get(key)
        if not ent:
            return False
        try:
            return abs(ent.get("mtime", -1) - os.path.getmtime(path)) < 1
        except OSError:
            return False

    def analyze(self, path, force=False):
        """Renvoie la liste des visages de la photo :
        [{"bbox": [x1,y1,x2,y2], "det_score": f, "best_name": n|None, "best_score": f}]
        Detection mise en cache ; correspondance recalculee a chaque appel.
        """
        import numpy as np
        key = os.path.abspath(path)
        if force or not self.is_cached(path):
            self.log(f"[verbose] detection de visages : {os.path.basename(path)}")
            img = imread_unicode(path)
            if img is None:
                self.log(f"[ERREUR] lecture impossible : {path}")
                return []
            t0 = time.monotonic()
            detected = self.detect_raw(img)
            self.log(f"[verbose] {len(detected)} visage(s) detecte(s) en {time.monotonic() - t0:.2f}s")
            faces_raw = [
                {
                    "bbox": [float(v) for v in f.bbox[:4]],
                    "det_score": float(f.det_score),
                    "embedding": [float(x) for x in f.embedding],
                }
                for f in detected
            ]
            with self._lock:
                try:
                    mtime = os.path.getmtime(path)
                except OSError:
                    mtime = -1
                self.cache[key] = {"mtime": mtime, "faces": faces_raw}
                self._dirty = True
        results = []
        for f in self.cache[key]["faces"]:
            emb = np.array(f["embedding"], dtype=np.float32)
            emb = emb / (np.linalg.norm(emb) + 1e-8)
            name, score = self._match(emb)
            results.append({
                "bbox": f["bbox"],
                "det_score": f["det_score"],
                "best_name": name,
                "best_score": score,
            })
        return results

    def names_at(self, path, threshold, force=False):
        """Noms reconnus (au-dessus du seuil), tries. Declenche la detection si besoin."""
        names = set()
        for face in self.analyze(path, force=force):
            if face["best_name"] and face["best_score"] >= threshold:
                names.add(face["best_name"])
        return sorted(names)

    def cached_names(self, path, threshold):
        """Comme names_at, mais uniquement depuis le cache. None si photo non analysee."""
        if not self.is_cached(path):
            return None
        return self.names_at(path, threshold)


# ---------- construction du plan de renommage ----------

def build_plan(folder, model, timeout, use_faces, threshold, reprocess_faces,
               engine, log, emit, should_stop, progress=None, recursive=False):
    """Reproduit la logique de rename_photos.process_folder en mode dry-run,
    mais renvoie des propositions structurees via emit(dict) au lieu d'imprimer.

    Proposition : {"old": str, "new": str, "status": "ok"|"skip"|"error",
                   "detail": str, "check": bool}
    """
    files = list_photos(folder, recursive)
    if not files:
        log("Aucune photo trouvee dans ce dossier.")
        return
    log(f"{len(files)} photo(s) trouvee(s)"
        + (" (sous-dossiers inclus)" if recursive else "")
        + f". Modele : {model}. Mode : APERCU (aucun fichier modifie)")
    check_ollama(model, log)
    if use_faces and engine.db:
        log(f"Reconnaissance faciale activee ({len(engine.db)} personne(s) en base : "
            f"{', '.join(engine.persons())})")

    taken = {f.lower() for f in files}

    def reserve(candidate, current):
        """Evite les collisions entre noms proposes et fichiers existants."""
        stem, ext = os.path.splitext(candidate)
        result, counter = candidate, 1
        while result.lower() in taken and result.lower() != current.lower():
            result = f"{stem}_{counter}{ext}"
            counter += 1
        taken.add(result.lower())
        return result

    for i, fname in enumerate(files):
        if should_stop():
            log("Analyse interrompue par l'utilisateur.")
            return
        path = os.path.join(folder, fname)
        subdir = os.path.dirname(fname)  # "" a la racine, sinon chemin relatif
        stem, ext = os.path.splitext(os.path.basename(fname))
        ext = ext.lower()
        if progress is not None:
            progress(i + 1, len(files), fname)
        log(f"[verbose] traitement de {fname} ({i + 1}/{len(files)})")

        already = rp.ALREADY_PREFIXED_RE.match(stem)
        if already:
            if not (reprocess_faces and use_faces and engine.db):
                emit({"old": fname, "new": fname, "status": "skip",
                      "detail": "deja horodate, ignore", "check": False})
                continue
            existing_slug = stem[already.end():]
            existing_parts = set(existing_slug.lower().split("_"))
            known_lower = {n.lower() for n in engine.db}
            if existing_parts & known_lower:
                emit({"old": fname, "new": fname, "status": "skip",
                      "detail": "deja horodate, nom deja present", "check": False})
                continue
            known_names = engine.names_at(path, threshold)
            if not known_names:
                emit({"old": fname, "new": fname, "status": "skip",
                      "detail": "deja horodate, aucun visage reconnu", "check": False})
                continue
            names_slug = rp.slugify("_".join(known_names))
            slug = f"{names_slug}_{existing_slug}".strip("_") or "photo"
            timestamp = stem[:already.end()].rstrip("_")
            new_name = reserve(os.path.join(subdir, f"{timestamp}_{slug}{ext}"), fname)
            emit({"old": fname, "new": new_name, "status": "ok",
                  "detail": f"visages ajoutes : {', '.join(known_names)}", "check": True})
            continue

        timestamp = rp.get_exif_datetime(path)
        if timestamp:
            log(f"[verbose] horodatage EXIF trouve : {timestamp}")
        else:
            timestamp = timestamp_from_name(stem)
            if timestamp:
                log(f"[verbose] pas d'EXIF, horodatage extrait du nom : {timestamp}")
            else:
                timestamp = datetime.fromtimestamp(
                    os.path.getmtime(path)).strftime("%Y%m%d_%H%M%S")
                log(f"[verbose] pas d'EXIF, horodatage via date de modification : {timestamp}")

        if is_generic_name(stem):
            known_names = []
            if use_faces and engine.db:
                known_names = engine.names_at(path, threshold)
                if known_names:
                    log(f"[verbose] personnes reconnues : {', '.join(known_names)}")
            try:
                description = rp.describe_image(path, model, timeout, known_names=known_names)
            except Exception as e:
                import requests
                if isinstance(e, requests.exceptions.ConnectionError):
                    raise PlanAborted("Impossible de contacter Ollama (ollama serve est-il lance ?)")
                if isinstance(e, requests.exceptions.ReadTimeout):
                    emit({"old": fname, "new": fname, "status": "error",
                          "detail": f"timeout apres {timeout}s (augmenter le timeout)", "check": False})
                    continue
                emit({"old": fname, "new": fname, "status": "error",
                      "detail": str(e), "check": False})
                continue
            if known_names:
                slug = f"{rp.slugify('_'.join(known_names))}_{rp.slugify(description)}".strip("_") or "photo"
                detail = f"{', '.join(known_names)} : {description}"
            else:
                slug = rp.slugify(description) or "photo"
                detail = description
        else:
            log(f"[verbose] nom '{stem}' juge deja explicite, pas d'appel au modele vision")
            slug = rp.slugify(stem) or "photo"
            detail = "nom conserve, horodatage ajoute"

        new_name = reserve(os.path.join(subdir, f"{timestamp}_{slug}{ext}"), fname)
        if new_name == fname:
            emit({"old": fname, "new": fname, "status": "skip",
                  "detail": "aucun changement necessaire", "check": False})
        else:
            emit({"old": fname, "new": new_name, "status": "ok",
                  "detail": detail, "check": True})


# ---------- application des renommages ----------

INVALID_CHARS = set('\\/:*?"<>|')


def validate_filename(name):
    """Renvoie None si valide, sinon un message d'erreur."""
    if not name or not name.strip():
        return "nom vide"
    if any(c in INVALID_CHARS for c in name):
        return "caracteres interdits ( \\ / : * ? \" < > | )"
    if not os.path.splitext(name)[1]:
        return "extension manquante"
    return None


def apply_renames(folder, pairs, log, engine=None):
    """Applique une liste de renommages [(ancien, nouveau)] et enregistre le lot.

    Renvoie (renames, errors) ou renames = [{"old":..., "new":...}].
    """
    renames, errors = [], []
    for old, new in pairs:
        src = os.path.join(folder, old)
        problem = validate_filename(new)
        if problem:
            errors.append(f"{old} : {problem}")
            continue
        if not os.path.exists(src):
            errors.append(f"{old} : fichier introuvable (deja renomme ?)")
            continue
        if new == old:
            continue
        dst = os.path.join(folder, new)
        stem, ext = os.path.splitext(new)
        counter = 1
        while os.path.exists(dst):
            dst = os.path.join(folder, f"{stem}_{counter}{ext}")
            counter += 1
        try:
            os.rename(src, dst)
        except OSError as e:
            errors.append(f"{old} : {e}")
            continue
        final = os.path.basename(dst)
        renames.append({"old": old, "new": final})
        if engine is not None:
            engine.rename_key(src, dst)
        log(f"  {old}  ->  {final}")
    if renames:
        record_batch(folder, renames)
        log(f"{len(renames)} fichier(s) renomme(s), lot enregistre dans l'historique.")
    for e in errors:
        log(f"  [ERREUR] {e}")
    return renames, errors


# ---------- historique / annulation ----------

def load_history():
    if os.path.isfile(HISTORY_PATH):
        try:
            with open(HISTORY_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []


def _save_history(history):
    with open(HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=1)


def record_batch(folder, renames):
    history = load_history()
    history.append({
        "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "folder": folder,
        "renames": renames,
    })
    _save_history(history[-HISTORY_MAX_BATCHES:])


def undo_last_batch(log, engine=None):
    """Annule le dernier lot (renomme nouveau -> ancien). Renvoie (n_annules, erreurs)."""
    history = load_history()
    if not history:
        log("Aucun lot a annuler.")
        return 0, []
    batch = history[-1]
    folder = batch["folder"]
    undone, errors = 0, []
    for item in reversed(batch["renames"]):
        src = os.path.join(folder, item["new"])
        dst = os.path.join(folder, item["old"])
        if not os.path.exists(src):
            errors.append(f"{item['new']} : introuvable, annulation impossible")
            continue
        if os.path.exists(dst):
            errors.append(f"{item['old']} : un fichier porte deja ce nom")
            continue
        try:
            os.rename(src, dst)
            undone += 1
            if engine is not None:
                engine.rename_key(src, dst)
            log(f"  {item['new']}  ->  {item['old']} (annule)")
        except OSError as e:
            errors.append(f"{item['new']} : {e}")
    _save_history(history[:-1])
    log(f"Lot du {batch['date']} annule : {undone} fichier(s) restaure(s).")
    for e in errors:
        log(f"  [ERREUR] {e}")
    return undone, errors


# ---------- enrolement ----------

def enroll_person(name, file_paths, engine, log):
    """Copie les photos dans Reference/<name>/ puis enrole les visages.

    Renvoie le nombre de visages ajoutes.
    """
    name = name.strip()
    if not name:
        log("[ERREUR] nom de personne vide.")
        return 0
    dest_dir = os.path.join(REFERENCE_DIR, name)
    os.makedirs(dest_dir, exist_ok=True)

    copied = []
    for p in file_paths:
        if os.path.splitext(p)[1].lower() not in ef.IMAGE_EXTENSIONS:
            log(f"  {os.path.basename(p)} : extension non geree, ignore")
            continue
        target = os.path.join(dest_dir, os.path.basename(p))
        stem, ext = os.path.splitext(target)
        counter = 1
        while os.path.exists(target):
            target = f"{stem}_{counter}{ext}"
            counter += 1
        try:
            shutil.copy2(p, target)
            copied.append(target)
        except OSError as e:
            log(f"  [ERREUR] copie de {p} : {e}")
    if not copied:
        log("Aucune photo de reference copiee.")
        return 0
    log(f"{len(copied)} photo(s) copiee(s) dans {dest_dir}")

    engine.ensure_app()
    db = ef.load_db(engine.db_path)
    embeddings = db.get(name, [])
    n_added = 0
    for img_path in copied:
        img = imread_unicode(img_path)
        if img is None:
            log(f"  [ERREUR] lecture impossible : {img_path}")
            continue
        faces = engine.detect_raw(img)
        if not faces:
            log(f"  {os.path.basename(img_path)} : aucun visage detecte, ignore")
            continue
        face = max(faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))
        embeddings.append(face.embedding.tolist())
        n_added += 1
        log(f"  {os.path.basename(img_path)} : visage enrole ({len(faces)} detecte(s) sur la photo)")
    db[name] = embeddings
    ef.save_db(db, engine.db_path)
    engine.reload_db()
    log(f"{n_added} visage(s) ajoute(s) pour '{name}'. Total en base : {len(embeddings)}.")
    return n_added
