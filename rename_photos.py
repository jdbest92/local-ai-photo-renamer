#!/usr/bin/env python3
"""
Renommage intelligent de photos via un modele vision local (Ollama).

Usage :
    # Dry-run sur un dossier (par defaut, aucun fichier modifie)
    python rename_photos.py "D:\\Photos\\Vacances2026"

    # Application reelle du renommage
    python rename_photos.py "D:\\Photos\\Vacances2026" --apply

    # Avec plus de details sur les etapes (chargement modele, envoi prompt, timing)
    python rename_photos.py "D:\\Photos\\Vacances2026" --apply --verbose

    # Timeout plus long si le modele charge lentement a froid (defaut : 300s)
    python rename_photos.py "D:\\Photos\\Vacances2026" --apply --timeout 600

    # Avec un autre modele vision (attention : gemma4:e4b a un bug connu,
    # il ne voit pas les images ; utiliser gemma4:12b ou plus grand)
    python rename_photos.py "D:\\Photos\\Vacances2026" --apply --model llama3.2-vision:11b

    # Dry-run avec un modele specifique, pour verifier avant validation
    python rename_photos.py "D:\\Photos\\Vacances_2026" --model gemma4:12b

    # Chemin relatif depuis le dossier courant
    python rename_photos.py photos_a_trier --apply

    # Avec reconnaissance faciale (necessite d'avoir enrole des visages via enroll_faces.py)
    # Sans valeur, --faces utilise faces_db.json a cote de ce script
    python rename_photos.py "D:\\Photos\\Vacances2026" --apply --faces --verbose

Prerequis :
    - Ollama installe et lance (ollama serve)
    - Modele vision telecharge : ollama pull gemma4:12b
      (gemma4:e4b a un bug connu, images non reconnues, cf issues ollama/ollama #16809 et #16597)
    - pip install pillow requests --break-system-packages
    - Pour la reconnaissance faciale (optionnel) : voir enroll_faces.py
"""

import argparse
import base64
import json
import os
import re
import sys
import time
import unicodedata
from datetime import datetime

import requests
from PIL import Image
from PIL.ExifTags import TAGS

OLLAMA_URL = "http://localhost:11434/api/chat"
EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".heic"}
MAX_NAME_LEN = 60
FACE_MATCH_THRESHOLD = 0.35  # similarite cosinus minimale pour valider une identite (buffalo_l/ArcFace)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_FACES_DB = os.path.join(SCRIPT_DIR, "faces_db.json")

VERBOSE = False


def vprint(msg):
    if VERBOSE:
        print(f"    [verbose] {msg}")

# Noms "generiques" de camera/telephone/capture d'ecran : ceux-la seront decrits par le LLM.
GENERIC_NAME_RE = re.compile(
    r"^(img|dsc|dscn|photo|screenshot|pxl|mvimg|image|p\d{7,})[_\-]?\d*$"
    r"|^capture d.?[ée]cran.*$",
    re.IGNORECASE,
)

# Fichier deja traite par ce script (prefixe AAAAMMJJ_HHMMSS deja present).
ALREADY_PREFIXED_RE = re.compile(r"^\d{8}_\d{6}_")


def is_generic_name(stem):
    """True si le nom de fichier ressemble a un nom auto-genere par un appareil."""
    return bool(GENERIC_NAME_RE.match(stem.strip()))


def load_faces_db(path):
    """Charge la base de visages de reference (embeddings par nom)."""
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    import numpy as np
    return {name: np.array(vecs, dtype=np.float32) for name, vecs in raw.items()}


def init_face_app(use_gpu):
    """Charge le modele insightface (import paresseux, uniquement si --faces est utilise)."""
    try:
        from insightface.app import FaceAnalysis
    except ImportError:
        print("Dependances manquantes pour --faces. Installe-les avec :")
        print("  pip install insightface onnxruntime opencv-python-headless numpy --break-system-packages")
        sys.exit(1)
    vprint("chargement du modele insightface (buffalo_l)")
    app = FaceAnalysis(name="buffalo_l")
    app.prepare(ctx_id=0 if use_gpu else -1, det_size=(640, 640))
    return app


def recognize_faces(path, face_app, faces_db, threshold):
    """Renvoie la liste triee des noms reconnus sur la photo (peut etre vide)."""
    import cv2
    import numpy as np

    img = cv2.imread(path)
    if img is None:
        return []
    detected = face_app.get(img)
    if not detected:
        return []

    names = set()
    for face in detected:
        emb = face.embedding
        emb_norm = emb / (np.linalg.norm(emb) + 1e-8)
        best_name, best_score = None, -1.0
        for name, vecs in faces_db.items():
            vecs_norm = vecs / (np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-8)
            sims = vecs_norm @ emb_norm
            score = float(sims.max())
            if score > best_score:
                best_name, best_score = name, score
        if best_name is not None and best_score >= threshold:
            vprint(f"visage reconnu : {best_name} (similarite {best_score:.2f})")
            names.add(best_name)
        else:
            vprint(f"visage non reconnu (meilleur score {best_score:.2f} sous le seuil {threshold})")
    return sorted(names)


def get_exif_datetime(path):
    """Renvoie 'AAAAMMJJ_HHMMSS' depuis l'EXIF, ou None si absent."""
    try:
        img = Image.open(path)
        exif = img._getexif()
        if not exif:
            return None
        for tag_id, value in exif.items():
            tag = TAGS.get(tag_id, tag_id)
            if tag == "DateTimeOriginal":
                dt = datetime.strptime(value, "%Y:%m:%d %H:%M:%S")
                return dt.strftime("%Y%m%d_%H%M%S")
    except Exception:
        return None
    return None


def image_to_base64(path, max_size=768):
    """Redimensionne et encode l'image en base64 (reduit le cout d'inference)."""
    img = Image.open(path).convert("RGB")
    img.thumbnail((max_size, max_size))
    import io
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def describe_image(path, model, timeout, known_names=None):
    """Interroge Ollama pour obtenir une description courte du contenu.

    Si known_names est fourni (visages deja identifies par reconnaissance faciale),
    demande une description de la scene/activite uniquement, sans re-decrire les personnes.
    """
    if known_names:
        prompt = (
            "Decris l'activite ou le lieu de cette photo en 2 a 3 mots maximum, "
            "en francais, sans phrase complete, juste des mots-cles "
            "separes par des espaces (ex: 'plage coucher soleil' ou "
            "'anniversaire gateau bougies'). Ne mentionne pas qui est present sur la photo, "
            "decris seulement l'activite, le lieu ou l'ambiance. Pas de ponctuation."
        )
    else:
        prompt = (
            "Decris le contenu de cette photo en 3 a 5 mots maximum, "
            "en francais, sans phrase complete, juste des mots-cles "
            "separes par des espaces (ex: 'plage coucher soleil' ou "
            "'reunion bureau presentation'). Pas de ponctuation."
        )
    vprint(f"encodage de l'image ({path})")
    images = [image_to_base64(path)]
    vprint(f"taille base64 encode: {len(images[0])} caracteres")

    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": prompt,
                "images": images,
            }
        ],
        "think": False,
        "stream": False,
        "options": {
            "num_ctx": 2048,
        },
    }
    vprint(f"envoi du prompt a Ollama (modele: {model}, timeout: {timeout}s)")
    t0 = time.monotonic()
    resp = requests.post(OLLAMA_URL, json=payload, timeout=timeout)
    resp.raise_for_status()
    elapsed = time.monotonic() - t0
    data = resp.json()

    load_duration = data.get("load_duration")
    eval_count = data.get("eval_count")
    if load_duration is not None:
        vprint(f"chargement modele: {load_duration / 1e9:.2f}s (0 si deja charge en memoire)")
    vprint(f"reponse recue en {elapsed:.2f}s ({eval_count} tokens generes)" if eval_count else f"reponse recue en {elapsed:.2f}s")

    return data.get("message", {}).get("content", "").strip()


def slugify(text):
    """Nettoie une description pour en faire un nom de fichier valide Windows."""
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    text = re.sub(r"[\s_]+", "_", text)
    return text[:MAX_NAME_LEN].strip("_")


def process_folder(folder, model, apply, timeout, face_app=None, faces_db=None, face_threshold=FACE_MATCH_THRESHOLD, reprocess_faces=False):
    files = sorted(
        f for f in os.listdir(folder)
        if os.path.splitext(f)[1].lower() in EXTENSIONS
    )
    if not files:
        print("Aucune photo trouvee dans ce dossier.")
        return

    print(f"{len(files)} photo(s) trouvee(s). Modele : {model}. "
          f"Mode : {'APPLICATION' if apply else 'DRY-RUN (aucun fichier modifie)'}\n")

    for fname in files:
        path = os.path.join(folder, fname)
        stem, ext = os.path.splitext(fname)
        ext = ext.lower()

        vprint(f"traitement de {fname}")

        already_prefixed = ALREADY_PREFIXED_RE.match(stem)
        if already_prefixed:
            if not reprocess_faces or face_app is None:
                print(f"  {fname}  ->  (deja horodate, ignore)")
                continue

            existing_slug = stem[already_prefixed.end():]
            existing_parts = set(existing_slug.lower().split("_"))
            known_lower = {name.lower() for name in faces_db.keys()}
            if existing_parts & known_lower:
                print(f"  {fname}  ->  (deja horodate, nom deja present, ignore)")
                continue

            vprint(f"retraitement pour reconnaissance faciale : {fname}")
            known_names = recognize_faces(path, face_app, faces_db, face_threshold)
            if not known_names:
                print(f"  {fname}  ->  (deja horodate, aucun visage reconnu, inchange)")
                continue

            names_slug = slugify("_".join(known_names))
            slug = f"{names_slug}_{existing_slug}".strip("_") or "photo"
            timestamp = stem[:already_prefixed.end()].rstrip("_")
            desc_label = f"visages ajoutes : {', '.join(known_names)}"

            new_name = f"{timestamp}_{slug}{ext}"
            new_path = os.path.join(folder, new_name)
            counter = 1
            base_new_path = new_path
            while os.path.exists(new_path) and new_path != path:
                new_path = base_new_path.replace(ext, f"_{counter}{ext}")
                counter += 1
            print(f"  {fname}  ->  {os.path.basename(new_path)}   ({desc_label})")
            if apply:
                os.rename(path, new_path)
            continue

        timestamp = get_exif_datetime(path)
        if timestamp:
            vprint(f"horodatage EXIF trouve: {timestamp}")
        else:
            timestamp = datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y%m%d_%H%M%S")
            vprint(f"pas d'EXIF, horodatage via date de modification: {timestamp}")

        if is_generic_name(stem):
            vprint(f"nom '{stem}' juge generique -> reconnaissance faciale puis LLM vision")

            known_names = []
            if face_app is not None:
                known_names = recognize_faces(path, face_app, faces_db, face_threshold)
                if known_names:
                    vprint(f"personnes reconnues : {', '.join(known_names)}")

            try:
                description = describe_image(path, model, timeout, known_names=known_names)
            except requests.exceptions.ConnectionError:
                print("Erreur : impossible de contacter Ollama (ollama serve est-il lance ?)")
                sys.exit(1)
            except requests.exceptions.ReadTimeout:
                print(f"  [ERREUR] {fname} : timeout apres {timeout}s (modele trop lent a repondre, augmente --timeout)")
                continue
            except Exception as e:
                print(f"  [ERREUR] {fname} : {e}")
                continue

            if known_names:
                names_slug = slugify("_".join(known_names))
                scene_slug = slugify(description)
                slug = f"{names_slug}_{scene_slug}".strip("_") or "photo"
                desc_label = f"{', '.join(known_names)} : {description}"
            else:
                slug = slugify(description) or "photo"
                desc_label = description
        else:
            vprint(f"nom '{stem}' juge deja explicite -> pas d'appel LLM")
            slug = slugify(stem) or "photo"
            desc_label = "(nom conserve)"

        new_name = f"{timestamp}_{slug}{ext}"
        new_path = os.path.join(folder, new_name)

        # Evite les collisions si deux photos donnent le meme nom
        counter = 1
        base_new_path = new_path
        while os.path.exists(new_path) and new_path != path:
            new_path = base_new_path.replace(ext, f"_{counter}{ext}")
            counter += 1

        print(f"  {fname}  ->  {os.path.basename(new_path)}   (desc: {desc_label})")

        if apply:
            os.rename(path, new_path)

    if not apply:
        print("\nAucun fichier n'a ete renomme (dry-run). Relance avec --apply pour valider.")


def main():
    global VERBOSE
    parser = argparse.ArgumentParser(description="Renommage intelligent de photos via LLM vision local.")
    parser.add_argument("folder", help="Dossier contenant les photos")
    parser.add_argument("--model", default="gemma4:12b", help="Modele Ollama vision (defaut: gemma4:12b ; gemma4:e4b a un bug connu qui l'empeche de voir les images)")
    parser.add_argument("--apply", action="store_true", help="Applique reellement le renommage (sinon dry-run)")
    parser.add_argument("--verbose", action="store_true", help="Affiche le detail des etapes (chargement modele, envoi prompt, timing)")
    parser.add_argument("--timeout", type=int, default=300, help="Timeout en secondes par appel Ollama (defaut: 300, augmenter si le modele charge lentement a froid)")
    parser.add_argument("--faces", nargs="?", const=DEFAULT_FACES_DB, default=None,
                         help=f"Active la reconnaissance faciale. Sans valeur, utilise {DEFAULT_FACES_DB} "
                              f"(genere par enroll_faces.py). Sinon, precise un chemin explicite.")
    parser.add_argument("--face-threshold", type=float, default=FACE_MATCH_THRESHOLD, help=f"Seuil de similarite pour valider une identite (defaut : {FACE_MATCH_THRESHOLD})")
    parser.add_argument("--faces-gpu", action="store_true", help="Utiliser le GPU pour la reconnaissance faciale (par defaut : CPU, pour ne pas entrer en concurrence avec Ollama)")
    parser.add_argument("--reprocess-faces", action="store_true",
                         help="Retraite aussi les photos deja horodatees (par un run precedent) pour y ajouter "
                              "les noms reconnus. Necessite --faces. Ne touche pas au reste du nom ni a l'horodatage.")
    args = parser.parse_args()

    VERBOSE = args.verbose

    if not os.path.isdir(args.folder):
        print(f"Dossier introuvable : {args.folder}")
        sys.exit(1)

    face_app, faces_db = None, None
    if args.faces:
        if not os.path.isfile(args.faces):
            print(f"Base de visages introuvable : {args.faces}")
            sys.exit(1)
        faces_db = load_faces_db(args.faces)
        if not faces_db:
            print(f"Base de visages vide : {args.faces} (utilise enroll_faces.py pour l'alimenter)")
            sys.exit(1)
        face_app = init_face_app(args.faces_gpu)
        print(f"Reconnaissance faciale activee ({len(faces_db)} personne(s) en base : {', '.join(faces_db.keys())})")

    if args.reprocess_faces and not args.faces:
        print("--reprocess-faces necessite --faces (impossible de reconnaitre des visages sans base).")
        sys.exit(1)

    process_folder(args.folder, args.model, args.apply, args.timeout,
                    face_app=face_app, faces_db=faces_db, face_threshold=args.face_threshold,
                    reprocess_faces=args.reprocess_faces)


if __name__ == "__main__":
    main()
