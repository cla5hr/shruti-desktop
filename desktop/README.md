# Shruti Desktop (.exe)

The whole Shruti app — record/upload → transcript → minutes → Q&A — packaged as a
single-user Windows app. No server, no install steps, no terminal: each person runs
their own copy and their meetings never leave their machine.

## Give it to someone

1. Send them `release/Shruti-Desktop-win64.zip`.
2. They unzip it anywhere and run `Shruti.exe` — their browser opens the app
   (a tray icon appears; right-click → Quit to stop it).
3. First transcription downloads the chosen speech model automatically (needs
   internet once; everything runs offline after that).
4. Optional, in **⚙ Settings** inside the app:
   - **Transcription model** — Tiny → Large-v3; bigger = more accurate, slower.
   - **Minutes & Q&A** — install [Ollama](https://ollama.com) + `ollama pull qwen2.5:3b`
     for fully-local AI minutes, or paste the company GPU endpoint URL + API key,
     or turn AI off.

Their data lives in `%APPDATA%\Shruti-Desktop\` (database, audio, settings, log).

## How the exe is put together

- `shruti_desktop.py` — the entry point: seeds config env vars, creates the SQLite
  schema, starts the worker thread + uvicorn (127.0.0.1 only), opens the browser,
  and runs the tray icon (with a "meeting window detected" toast).
- `shruti.spec` — PyInstaller spec: bundles the web build, ffmpeg, and the native
  wheels (ctranslate2, sherpa-onnx, onnxruntime, av) that plain import analysis misses.
- `build.ps1` — one command: web build → CPU-only venv → ffmpeg copy → PyInstaller → zip.

## Build it yourself

```powershell
.\desktop\build.ps1   # needs uv, Node 20+, ffmpeg (winget Gyan.FFmpeg)
```

Outputs `desktop\dist\Shruti\Shruti.exe` and `release\Shruti-Desktop-win64.zip`.
The build uses its own venv (`desktop/.venv-desktop`) so dev-only packages never
bloat the bundle.

## Gotchas learned building this

- uvicorn's default logging config references formatter classes by import string,
  which fails inside PyInstaller — always pass `log_config=None`.
- FastAPI discovers `python-multipart` by dynamic import; it must be listed in
  `hiddenimports` or uploads break only in the frozen app.
- `%APPDATA%\Shruti` belongs to the older Electron Shruti app — this app deliberately
  uses `%APPDATA%\Shruti-Desktop` so the two never fight.
- pydantic-settings reads `.env` from the CWD: the exe chdirs to its data folder first,
  or launching it from a repo checkout inherits stray dev config (found the hard way:
  `ASR_DEVICE=cuda` leaked into a user save).
- sherpa-onnx `FastClustering` threshold is embedder-specific and sensitive — sweep it
  on labeled fixtures whenever the embedding model changes. Current: WeSpeaker
  ResNet34-LM (the same embedder pyannote 3.1 uses) at threshold 0.45, correct across
  a wide band on labeled 2- and 3-speaker test audio. The earlier CAM++ embedder never
  got speaker count and attribution right at the same threshold.
