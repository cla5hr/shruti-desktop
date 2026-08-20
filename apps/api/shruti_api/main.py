"""Shruti API — app assembly. Feature endpoints live in routers/."""

import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from shruti_api.routers import (
    app_settings,
    chat,
    jobs,
    media,
    meetings,
    speakers,
    summaries,
    uploads,
)
from shruti_core.db import new_session


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    # the GPU probe imports ctranslate2 (~2.5s) — warm it in the background so the
    # first Settings page load doesn't pay for it
    threading.Thread(target=app_settings._cuda_usable, daemon=True).start()
    yield


app = FastAPI(title="Shruti API", lifespan=_lifespan)

# Vite dev server origin; prod serves web + api behind one origin (Caddy)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(meetings.router)
app.include_router(uploads.router)
app.include_router(jobs.router)
app.include_router(media.router)
app.include_router(speakers.router)
app.include_router(summaries.router)
app.include_router(chat.router)
app.include_router(app_settings.router)


@app.get("/api/healthz")
def healthz() -> dict:
    session = new_session()
    try:
        session.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception as exc:  # pragma: no cover - surfaced in smoke tests
        db_status = f"error: {exc.__class__.__name__}"
    finally:
        session.close()
    return {"status": "ok" if db_status == "ok" else "degraded", "db": db_status}


# ── Serve the built web app from the API process ─────────────────────────────
# The desktop exe sets SHRUTI_WEB_DIST (PyInstaller relocates the files); a repo
# checkout falls back to apps/web/dist when one has been built. Dev mode (vite on
# :5173) is unaffected: without a dist build these routes simply don't mount.
_WEB_DIST = Path(
    os.environ.get("SHRUTI_WEB_DIST") or Path(__file__).resolve().parents[3] / "apps/web/dist"
)

if _WEB_DIST.is_dir():  # pragma: no cover - exercised by the exe, not tests
    app.mount("/assets", StaticFiles(directory=_WEB_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str) -> FileResponse:
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="not found")
        candidate = _WEB_DIST / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_WEB_DIST / "index.html")
