<p align="center"><img src="apps/web/public/skyroot_logo.png" alt="Skyroot" height="80"></p>

# Shruti — Private AI Meeting Notes

*श्रुति — "that which is heard."*

Shruti listens to your meetings and gives you back three things: a transcript
with every speaker named, ready-made minutes, and a chat where you can ask the
meeting questions — *"what are my action items?"*

Everything runs on your own computer. No cloud AI, no subscriptions, and no
meeting audio ever leaves your machine.

> Built at Skyroot Aerospace as a private alternative to Read.ai / Otter /
> Fireflies. Each person runs their own copy.

---

## Get started

1. **Download** `Shruti-Desktop-win64.zip` from [Releases](../../releases), or
   from the IT shared folder:
   `\Dep - Digital Transformation\Workflow Automatic\shruti-desktop`
2. **Unzip it anywhere** and run `Shruti.exe`. The app opens in your browser,
   and a Skyroot icon appears in the system tray (right-click → Quit).
3. That's it — you can transcribe right away.

Want more? Open **⚙ Settings** inside the app:

- **Better accuracy** — pick a bigger Whisper model. It downloads with a
  progress bar and works offline from then on.
- **AI minutes & Q&A** — install [Ollama](https://ollama.com) and run
  `ollama pull qwen2.5:3b`. Shruti finds it automatically. (Or paste a company
  GPU endpoint URL and key.)

Your data stays in `%APPDATA%\Shruti-Desktop\`.

## What it does

- **Record a meeting live** — click **● Record here**. It captures your mic and
  the other participants too (share the Teams tab, or your screen with *"also
  share system audio"*). A tray notification nudges you when it spots a
  Teams/Zoom/Meet window, so you don't forget to hit record.
- **Or upload any recording** — mp3, m4a, mp4, wav, webm…
- **Transcript** — timestamped, speakers separated and nameable. Click a line
  to hear that moment. Double-click to fix a word. Search and replace names the
  model misheard.
- **Minutes** — summary, key points, decisions, action items — with timestamps
  you can click to verify. Edit them, regenerate them, export to Markdown.
- **Ask** — chat with the meeting. Answers cite the exact moment they came from.

## Privacy

Transcription (faster-whisper), speaker separation (pyannote models via
sherpa-onnx), and minutes (your local Ollama) all run on your machine. The only
internet use is downloading models once. Your recordings never go anywhere.

## For developers

**Build the exe:**

```powershell
git clone https://github.com/sashank-sr/shruti-desktop ; cd shruti-desktop
.\desktop\build.ps1     # needs: uv, Node 20+, ffmpeg (winget Gyan.FFmpeg)
```

That produces `desktop\dist\Shruti\Shruti.exe` and `release\Shruti-Desktop-win64.zip`.
Packaging notes live in [`desktop/README.md`](desktop/README.md).

**Work on the code:**

```powershell
uv sync --all-packages                              # Python deps
uv run pytest                                       # fast tests, no services needed
uv run uvicorn shruti_api.main:app --port 8000      # terminal 1: API
uv run python -m shruti_worker.main                 # terminal 2: worker
cd apps\web ; npm install ; npm run dev             # terminal 3: UI on :5173
```

**How it works:** one Python process runs a FastAPI backend and a job worker
over SQLite; the React UI is served locally; PyInstaller freezes it all into a
folder app. A recording flows through a chain of jobs — extract audio →
waveform → transcribe → separate speakers → minutes. Diagrams and the
reasoning behind the design: [`docs/architecture.md`](docs/architecture.md) ·
[`docs/decisions/`](docs/decisions/)

```
apps/api        FastAPI backend
apps/worker     the processing pipeline
apps/web        React UI
packages/core   settings, database models, job queue, storage, LLM client
desktop/        exe entry point + build
```

A hosted multi-user variant (Postgres, GPU, a Teams bot that joins meetings)
lives in the original internal repo — this repo is the desktop app only.

---

*Internal Skyroot Aerospace project · Sashank T · built with Claude Code.*
