# Local AI Photo Renamer

Rename your photo library intelligently, 100% locally: `IMG_1234.jpg` becomes `20230703_143551_alice_beach.jpg`, thanks to a local vision model ([Ollama](https://ollama.com)) that describes the scene and on-device face recognition (insightface) that identifies the people you have taught it. No photo ever leaves your computer.

> **Note**: the GUI is currently French-only. Its labels (buttons, tabs) are quoted verbatim in this manual, with translations. Command-line scripts are included.

*Documentation française complète : [README.fr.md](README.fr.md).*

![Renaming tab: thumbnail grid and editable rename proposals](docs/Renaming_panel.png)

![Viewer: face recognition with per-face scores](docs/Face_recognition_panel.png)

## Features

- **Smart renaming** to `YYYYMMDD_HHMMSS_description.ext`. Generic camera names (`IMG_1234`, `IMG-20230703-WA0072`, `PXL_...`, screenshots...) get an AI-generated description; explicit names are kept and just timestamped. Timestamps come from EXIF, then from a date embedded in the filename (WhatsApp/Android formats), then from the file's modification time.
- **Face recognition** (optional): enroll a few reference photos per person; recognized names are injected into the new filenames (`alice_beach` instead of `child_beach`) and power a per-person photo filter.
- **Preview before renaming**: every proposal is shown in an editable table; nothing is renamed without your confirmation, and every batch can be undone from the History tab.
- **Fully local and private**: Ollama for scene description, insightface for faces, JSON files on disk. Reference photos, face embeddings, caches and history stay on your machine (and are git-ignored).
- Thumbnail grid with lazy loading, face viewer with adjustable recognition threshold, per-person filter, subfolder mode, live activity log. CLI scripts (`rename_photos.py`, `enroll_faces.py`) cover renaming and enrollment without the GUI.

## 1. Requirements

- **OS**: Windows, Linux or macOS (developed and tested on Windows).
- **Python 3.10 or newer.**
- **[Ollama](https://ollama.com)** for automatic photo descriptions, with a vision model downloaded (default: `gemma4:12b`, ~8 GB). Avoid `gemma4:e4b`: a known bug prevents it from seeing images. Ollama must be running during analysis: either the Ollama desktop app (started automatically with the system) or `ollama serve` in a terminal. The app connects to `http://localhost:11434`, Ollama's default address and port; if your setup uses another port or machine (`OLLAMA_HOST` variable), edit the `OLLAMA_URL` constant at the top of `rename_photos.py`.
- **Hardware**: 16 GB RAM recommended for the vision model (a GPU speeds Ollama up considerably but is not required). Face recognition runs on CPU by default.
- **Internet on first run only**: to download the Ollama model, and the insightface model (`buffalo_l`, ~300 MB) the first time faces are used. Everything runs offline afterwards.

Ollama and face recognition are both optional: without Ollama, the app can still browse photos, handle faces and timestamp files whose names are already explicit; without the face recognition dependencies (insightface, onnxruntime, opencv), everything else works too.

## 2. Installation

A virtual environment is recommended, to keep the dependencies isolated from your system Python:

```
:: Windows
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
ollama pull gemma4:12b

# Linux / macOS
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
ollama pull gemma4:12b
```

A conda environment works just as well (`conda create -n photos python=3.12`, then `conda activate photos` and the same `pip install`).

## 3. Launching

With your environment activated, simply run:

```
python app_gui.py
```

On Windows, `run_app.bat` starts the app without a console window, but it uses whatever `pythonw` is in your PATH. If your dependencies live in a venv or conda environment, edit the file and uncomment/adapt the activation line at the top (examples are included). If nothing happens or a message flashes by, run `run_app_debug.bat`: it keeps the console open and shows the actual error.

## 4. Window overview

The window is split in two areas:

Top: the thumbnail grid (left) and the viewer (right). Thumbnails load lazily as you scroll, even with hundreds of photos.

Bottom: four tabs. "Renommage" (renaming: analyzing and applying new names), "Enrolement" (enrollment: teaching faces to the app), "Historique" (history: undoing a rename batch), "Journal" (log of all operations).

The top toolbar gathers: folder opening, the "Sous-dossiers" (subfolders) checkbox, the per-person filter, the full-folder analysis button, and the recognition threshold.

## 5. Opening a photo folder

Click "Ouvrir un dossier..." (open a folder) and pick the folder containing the photos to rename. The photo count appears in the toolbar. The last opened folder is remembered and reloaded on next start.

The "Sous-dossiers" checkbox recursively includes photos from subfolders (renamed files stay in their original subfolder).

## 6. Viewer and faces

Click a thumbnail: the photo is displayed on the right, and face detection starts automatically (the first use loads the model, allow a few seconds; progress is shown in the log).

Each detected face gets a box:

Green box: recognized face. The label shows the name and similarity score, e.g. "Alice (0.52)".

Red box: face not recognized at the current threshold. The label shows the best candidate and its score, e.g. "? (Bob 0.28)". Useful for spotting false negatives.

The recognition threshold (toolbar, 0.35 by default) applies live: boxes switch from red to green without re-running detection. Lower it if known faces are missed, raise it if strangers get identified. Detections are cached (`face_cache.json`): revisiting an analyzed photo is instant, and changing the threshold or enrolling someone does not redo detection.

## 7. Per-person filter

The "Filtre personne" menu shows only the photos where the chosen person appears. The filter relies on two sources: names already present in filenames, and faces already analyzed (cache).

Photos never analyzed cannot be filtered: the status bar tells you when that happens. Click "Analyser tout le dossier (pour le filtre)" (analyze the whole folder): the analysis runs in the background (progress in the status bar) and can take several minutes for hundreds of photos. Once done, it is cached for good.

Not to be confused with "Analyser le dossier (apercu)" in the "Renommage" tab: the toolbar button only detects faces to feed this filter (no vision model call, no rename proposal), whereas the one in the "Renommage" tab builds the new-name proposals (section 8). Both share the face cache: running one speeds up the other.

## 8. Renaming ("Renommage" tab)

Principle: nothing is ever renamed without your confirmation. The flow is Analyze, then review and adjust, then Apply.

Produced name format: `YYYYMMDD_HHMMSS_description.ext`. The timestamp comes from EXIF, else from a date embedded in the filename (WhatsApp, Android formats...), and as a last resort from the file's modification time. The description depends on the original name:

Generic name (IMG_1234, IMG-20230703-WA0072, screenshots...): description generated by the vision model. If face recognition is checked and recognizes someone, their name replaces the generic terms and the model only describes the scene (e.g. `alice_beach` rather than `child_beach`).

Already explicit name: timestamp only, no model call.

Photo already timestamped by a previous run: skipped, unless "Retraiter les photos deja horodatees" (reprocess already timestamped photos) is checked, in which case recognized names are added without touching the timestamp.

Options: Ollama model (`gemma4:12b` by default; beware, `gemma4:e4b` has a known bug and cannot see images), per-photo timeout (increase it if the model is slow to cold-start), face recognition, reprocessing.

Workflow:

1. Click "Analyser le dossier (apercu)" (analyze the folder, preview). Ollama and model availability are checked up front. Proposals appear row by row. Grey rows are skipped (already processed), red ones are errors, the others are proposed and checked.
2. Review. The "Nouveau nom" (new name) column is editable: double-click to fix a bad description. Uncheck rows you don't want renamed. "Arreter" (stop) interrupts a running analysis.
3. Click "Appliquer la selection" (apply selection) and confirm. Files are renamed, the batch is saved to history, the grid refreshes.

## 9. Enrollment ("Enrolement" tab)

To teach the app a new face, or add photos to an existing person:

1. Pick the name in "Personne", or click "Nouvelle personne..." (new person) to create one.
2. Add 3 to 5 photos of the person (varied angles and lighting, face clearly visible): drag and drop into the dashed area (files or a whole folder), "Ajouter un dossier..." to take every image in a folder, or "Ajouter des photos..." for a file-by-file selection.
3. Click "Copier dans Reference/ et enroler" (copy to Reference/ and enroll). Photos are copied to `Reference/<name>/` and the embeddings added to `faces_db.json`. Nothing to prepare: the `Reference/` folder is created automatically in the application folder, next to `app_gui.py` (see the tree in section 12).

On each reference photo, the largest face is enrolled: prefer photos where the person is alone or clearly in the foreground. For a growing child, re-enroll periodically with recent photos.

## 10. History and undo ("Historique" tab)

Each applied rename creates a timestamped batch (the last 30 are kept in `rename_history.json`). The tab lists batches with per-file detail. "Annuler le dernier lot" (undo the last batch) restores the old names, after confirmation. Batches are undone one at a time, from newest to oldest.

## 11. Log ("Journal" tab)

Every operation is traced live: model loading, Ollama response times, recognized faces with their scores, renames, errors. It is the first place to look when something seems slow or broken.

## 12. Application files

Full tree after some time of use:

```
local-ai-photo-renamer/
├── app_gui.py                 # GUI entry point
├── photoapp/                  # GUI modules (window, tabs, workers...)
├── rename_photos.py           # command-line renaming
├── enroll_faces.py            # command-line face enrollment
├── requirements.txt
├── run_app.bat                # Windows launcher (no console)
├── run_app_debug.bat          # Windows launcher with console (shows errors)
├── app_icon.ico, app_icon.png
│
│   Created automatically at runtime (git-ignored):
├── Reference/
│   ├── Alice/                 # reference photos for Alice
│   └── Bob/
├── faces_db.json              # enrolled face embeddings
├── face_cache.json            # detection cache
└── rename_history.json        # rename batch history
```

Nothing to create by hand: the app generates these files and folders as needed, always inside its own folder (regardless of which photo folder you open).

| File | Role |
| --- | --- |
| `app_gui.py` + `photoapp/` | The GUI application |
| `rename_photos.py`, `enroll_faces.py` | Command-line versions of renaming and enrollment (usable without the GUI, `--help` for options) |
| `faces_db.json` | Enrolled face embeddings (generated by enrollment) |
| `face_cache.json` | Detection cache (safe to delete, it will rebuild) |
| `rename_history.json` | Rename batch history |
| `Reference/<name>/` | Reference photos per person |

The last four items contain personal data (photos, biometric embeddings): they are generated locally and excluded from the repository by `.gitignore`. They should never be published.

## 13. Troubleshooting

The app does not start: use `run_app_debug.bat` and read the error shown.

"Impossible de contacter Ollama" (cannot reach Ollama) during analysis: Ollama is not running (start the Ollama app or `ollama serve`) or is not listening on `localhost:11434` (see Requirements). Then restart the analysis. Quick check: opening `http://localhost:11434` in a browser should reply "Ollama is running".

The model is not installed: `ollama pull gemma4:12b` (the exact name is given in the error message).

Timeouts on the first photos: the vision model is cold-starting; increase the timeout (600 s) or just retry, it will be loaded.

A known face is not recognized: lower the threshold and watch the scores in the viewer; if the score stays very low, enroll additional reference photos that are closer (age, angle, glasses...).

Someone is missing from the filter: the face database does not know them yet ("Enrolement" tab), or the photos have not been analyzed ("Analyser tout le dossier" button).

## License

[MIT](LICENSE)
