#!/usr/bin/env python3
"""
Enrolement de visages de reference pour rename_photos.py (reconnaissance faciale via insightface).

Usage :
    # Enroler une personne : place ses photos dans Reference/<Nom>/ a cote de ce script,
    # puis lance simplement :
    python enroll_faces.py --name Alice

    # Ou en precisant un chemin explicite (dossier ou fichiers)
    python enroll_faces.py --name Li "D:\\Photos\\Li_1.jpg" "D:\\Photos\\Li_2.jpg"

    # Ajouter d'autres photos plus tard (cumule avec la base existante)
    python enroll_faces.py --name Alice "D:\\Photos\\Reference\\Alice_2026"

Arborescence par defaut (a cote de ce script) :
    ./Reference/Alice/*.jpg
    ./Reference/Li/*.jpg
    ./Reference/Bob/*.jpg
    ./Reference/Carol/*.jpg
    ./faces_db.json          <- genere par ce script

Prerequis :
    pip install insightface onnxruntime opencv-python-headless numpy --break-system-packages
    (le premier lancement telecharge automatiquement le modele buffalo_l, environ 326 Mo)

Notes :
    - Utilise le plus grand visage detecte sur chaque photo (suppose que c'est le sujet principal).
    - Prevoir 3 a 5 photos par personne, sous des angles/eclairages varies, pour de meilleurs resultats.
    - Pour un enfant en pleine croissance, prevoir un re-enrolement periodique (le visage change vite).
"""

import argparse
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(SCRIPT_DIR, "faces_db.json")
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


def load_db(path):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_db(db, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)


def collect_images(paths):
    images = []
    for p in paths:
        if os.path.isdir(p):
            for fname in sorted(os.listdir(p)):
                if os.path.splitext(fname)[1].lower() in IMAGE_EXTENSIONS:
                    images.append(os.path.join(p, fname))
        elif os.path.isfile(p):
            images.append(p)
        else:
            print(f"  [ATTENTION] chemin introuvable, ignore : {p}")
    return images


def main():
    parser = argparse.ArgumentParser(description="Enrole des visages de reference pour la reconnaissance faciale.")
    parser.add_argument("--name", required=True, help="Nom de la personne (ex : Alice)")
    parser.add_argument("paths", nargs="*", help="Dossier(s) et/ou fichier(s) image(s) de reference. "
                         "Si omis, utilise <dossier_du_script>/Reference/<name>/")
    parser.add_argument("--db", default=DB_PATH, help=f"Fichier de base de visages (defaut : {DB_PATH})")
    parser.add_argument("--gpu", action="store_true", help="Utiliser le GPU (par defaut : CPU, pour ne pas entrer en concurrence avec Ollama)")
    args = parser.parse_args()

    paths = args.paths or [os.path.join(SCRIPT_DIR, "Reference", args.name)]

    try:
        import cv2
        from insightface.app import FaceAnalysis
    except ImportError:
        print("Dependances manquantes. Installe-les avec :")
        print("  pip install insightface onnxruntime opencv-python-headless numpy --break-system-packages")
        sys.exit(1)

    images = collect_images(paths)
    if not images:
        print(f"Aucune image trouvee dans : {', '.join(paths)}")
        if not args.paths:
            print(f"Cree ce dossier et mets-y quelques photos de {args.name}, ou precise un chemin explicite.")
        sys.exit(1)

    print("Chargement du modele insightface (buffalo_l), telechargement automatique si premier lancement...")
    app = FaceAnalysis(name="buffalo_l")
    app.prepare(ctx_id=0 if args.gpu else -1, det_size=(640, 640))

    db = load_db(args.db)
    embeddings = db.get(args.name, [])

    n_added = 0
    for img_path in images:
        img = cv2.imread(img_path)
        if img is None:
            print(f"  [ERREUR] impossible de lire {img_path}")
            continue
        faces = app.get(img)
        if not faces:
            print(f"  {os.path.basename(img_path)} : aucun visage detecte, ignore")
            continue
        # Prend le plus grand visage detecte (suppose que c'est le sujet principal)
        face = max(faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))
        embeddings.append(face.embedding.tolist())
        n_added += 1
        print(f"  {os.path.basename(img_path)} : visage enrole ({len(faces)} detecte(s) au total sur la photo)")

    db[args.name] = embeddings
    save_db(db, args.db)
    print(f"\n{n_added} visage(s) ajoute(s) pour '{args.name}'. Total en base pour cette personne : {len(embeddings)}.")
    print(f"Base sauvegardee dans {args.db}")


if __name__ == "__main__":
    main()
