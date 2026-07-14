# Local AI Photo Renamer

Rename your photo library intelligently, 100% locally: `IMG_1234.jpg` becomes `20230703_143551_alice_beach.jpg`. Scene descriptions come from a local vision model (via [Ollama](https://ollama.com)) and face recognition runs on-device (insightface). No photo ever leaves your computer.

> **Note**: the GUI is currently French-only. Command-line scripts are included.

*Documentation française complète : [README.fr.md](README.fr.md).*

<!-- TODO: add a screenshot (thumbnail grid, viewer with face boxes, rename preview table) -->

## Features

- **Smart renaming** to `YYYYMMDD_HHMMSS_description.ext`. Generic camera names (`IMG_1234`, `IMG-20230703-WA0072`, `PXL_...`, screenshots...) get an AI-generated description; explicit names are kept and just timestamped. Timestamps come from EXIF, then from a date embedded in the filename (WhatsApp/Android formats), then from the file's modification time.
- **Face recognition** (optional): enroll a few reference photos per person; recognized names are injected into the new filenames (`alice_beach` instead of `child_beach`) and power a per-person photo filter.
- **Preview before renaming**: every proposal is shown in an editable table; nothing is renamed without your confirmation, and every batch can be undone from the History tab.
- **Fully local and private**: Ollama for scene description, insightface for faces, JSON files on disk. Reference photos, face embeddings, caches and history stay on your machine (and are git-ignored).
- Thumbnail grid with lazy loading, face viewer with adjustable recognition threshold, per-person filter, subfolder mode, live activity log. CLI scripts (`rename_photos.py`, `enroll_faces.py`) cover renaming and enrollment without the GUI.

## Requirements

- **OS**: Windows, Linux or macOS (developed and tested on Windows).
- **Python 3.10+** with the packages from `pip install -r requirements.txt`.
- **[Ollama](https://ollama.com)** running locally with a vision model (default: `gemma4:12b`, ~8 GB download). Avoid `gemma4:e4b`: a known bug prevents it from seeing images. Ollama must be running during analysis (the desktop app, or `ollama serve` in a terminal); the app connects to `http://localhost:11434`, Ollama's default address and port. If you use a custom `OLLAMA_HOST`, edit the `OLLAMA_URL` constant at the top of `rename_photos.py`. Quick check: opening `http://localhost:11434` in a browser should reply "Ollama is running".
- **Hardware**: 16 GB RAM recommended for the vision model; a GPU speeds Ollama up considerably but is not required. Face recognition runs on CPU by default.
- **Internet on first run only**: to download the Ollama model, and the insightface model (`buffalo_l`, ~300 MB) the first time faces are used. Everything runs offline afterwards.

Face recognition dependencies (insightface, onnxruntime, opencv) are only needed if you use faces; everything else works without them. Without Ollama, viewing, faces, enrollment and timestamp-only renaming still work.

## Installation

Using a virtual environment is recommended, so the dependencies don't interfere with your system Python:

```bash
# Windows
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

## Quick start

With your environment activated:

```bash
python app_gui.py
```

**Windows launcher**: `run_app.bat` starts the app without a console window, but it uses whatever `pythonw` is in your PATH. If your dependencies live in a venv or conda environment, edit the file and uncomment/adapt the activation line at the top (examples are included). If nothing happens or a message flashes by, run `run_app_debug.bat`: it keeps the console open and shows the actual error.

1. Open a photo folder with "Ouvrir un dossier..." (check "Sous-dossiers" to include subfolders).
2. *(Optional)* Teach the app faces in the "Enrolement" (enrollment) tab: 3-5 reference photos per person.
3. In the "Renommage" (renaming) tab, click "Analyser le dossier (apercu)" to preview proposed names, review and edit them, then apply your selection.
4. Made a mistake? Undo the whole batch in the "Historique" (history) tab.

## Project layout

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

## Privacy

All processing is local. The app generates `faces_db.json` (face embeddings), `face_cache.json` (detection cache), `rename_history.json` (rename batches) and a `Reference/` folder (your reference photos). These contain personal data: they are excluded by `.gitignore` and should never be published.

## License

[MIT](LICENSE)
