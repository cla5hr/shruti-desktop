# How Shruti works

One process does everything: a FastAPI backend serves the app in your browser, a
worker thread processes recordings one job at a time, and SQLite stores it all.
PyInstaller freezes that into `Shruti.exe`. Nothing listens beyond your own
computer, and no meeting audio ever leaves it.

```mermaid
flowchart TB
    subgraph user["Your computer"]
        subgraph exe["Shruti.exe — one process"]
            ui["The app<br/>(in your browser, 127.0.0.1:8477)"]
            api["FastAPI<br/>REST API"]
            worker["Worker thread<br/>(one job at a time)"]
            tray["Tray icon + meeting reminder"]
        end
        db[("SQLite<br/>%APPDATA%/Shruti-Desktop")]
        files[("Audio & waveforms<br/>on local disk")]
        ollama["Ollama (optional)<br/>local AI"]
    end
    hf["One-time model downloads<br/>(Hugging Face / GitHub)"]

    browser["Your browser"] --> ui
    ui --> api
    api <--> db
    api <--> files
    worker <--> db
    worker --> files
    worker -- "minutes / Q&A" --> ollama
    worker -. "first use only" .-> hf
```

## What happens to a recording

Each arrow is a job in a queue — if one fails, the earlier results survive
(a transcript never disappears because minutes failed).

```mermaid
flowchart LR
    rec["Record / Upload"] --> ex["extract audio<br/>(ffmpeg)"]
    ex --> wf["waveform<br/>(for the player)"]
    ex --> asr["transcribe<br/>(faster-whisper)"]
    asr --> dia["separate speakers<br/>(TitaNet + pyannote, via sherpa-onnx)"]
    dia --> sum["minutes<br/>(local AI)"]
    sum --> done(["ready"])
```

## Why a job queue in the database, and not Celery/Redis?

Because the queue is ~140 lines over the SQLite file we already have — with
retries, progress reporting, and crash recovery — while a broker would mean two
more services to install and monitor, for exactly one worker. Full reasoning:
[ADR-002](decisions/ADR-002-db-job-queue.md).
