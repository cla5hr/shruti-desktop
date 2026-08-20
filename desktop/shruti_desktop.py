"""Shruti desktop: the whole app (API + worker + web UI) in one local process.

Packaged by PyInstaller (desktop/shruti.spec) into a distributable folder with
Shruti.exe. On launch: data lands under %APPDATA%/Shruti (SQLite + audio files +
settings.json), the API binds 127.0.0.1 only, one worker thread processes jobs,
a tray icon offers Open/Quit, and the default browser opens the UI.

Everything a user configures lives in the in-app Settings tab — no .env, no
terminal. Meetings never leave the machine.
"""

import logging
import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

PREFERRED_PORT = 8477  # fixed when free so bookmarks survive restarts


def appdata() -> Path:
    # NOT "%APPDATA%/Shruti": the older Electron Shruti app owns that folder on
    # some machines, and sharing it would corrupt both apps' data.
    root = Path(os.environ.get("APPDATA", str(Path.home()))) / "Shruti-Desktop"
    root.mkdir(parents=True, exist_ok=True)
    return root


def bundle_dir() -> Path:
    return Path(getattr(sys, "_MEIPASS", str(Path(__file__).resolve().parent)))


def configure_env(data: Path) -> None:
    """Seed config BEFORE any shruti module is imported. setdefault everywhere so
    a power user can still override via real env vars."""
    os.environ.setdefault("DATABASE_URL", f"sqlite:///{(data / 'shruti.db').as_posix()}")
    os.environ.setdefault("STORAGE_ROOT", str(data / "storage"))
    os.environ["SETTINGS_FILE"] = str(data / "settings.json")
    os.environ.setdefault("HF_HOME", str(data / "hf-cache"))  # whisper model downloads
    # sensible first-run defaults for an ordinary laptop; Settings tab can change all
    os.environ.setdefault("ASR_MODEL", "small")
    os.environ.setdefault("ASR_DEVICE", "cpu")
    os.environ.setdefault("ASR_COMPUTE_TYPE", "int8")
    # speaker separation via sherpa-onnx (always on; ~107 MB one-time model download)
    os.environ.setdefault("SHERPA_MODELS_DIR", str(data / "models" / "sherpa"))
    os.environ.setdefault("LLM_MODE", "live" if _ollama_running() else "stub")

    ffmpeg = bundle_dir() / "ffmpeg"
    if ffmpeg.is_dir():  # bundled ffmpeg/ffprobe win over anything on the machine
        os.environ["PATH"] = str(ffmpeg) + os.pathsep + os.environ.get("PATH", "")

    web_dist = bundle_dir() / "webdist"
    if web_dist.is_dir():
        os.environ["SHRUTI_WEB_DIST"] = str(web_dist)


def _ollama_running() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 11434), timeout=0.3):
            return True
    except OSError:
        return False


def _shruti_already_running(port: int) -> bool:
    try:
        import httpx

        r = httpx.get(f"http://127.0.0.1:{port}/api/healthz", timeout=1.5)
        return r.status_code == 200 and r.json().get("status") in ("ok", "degraded")
    except Exception:
        return False


def _pick_port() -> int:
    try:
        s = socket.socket()
        s.bind(("127.0.0.1", PREFERRED_PORT))
        s.close()
        return PREFERRED_PORT
    except OSError:
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        return port


def _meeting_window_open() -> bool:
    """True when a Teams/Zoom/Meet meeting window is on screen (title heuristics)."""
    import ctypes
    from ctypes import wintypes

    found: list[str] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _lparam):
        if not ctypes.windll.user32.IsWindowVisible(hwnd):
            return True
        n = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
        if n:
            buf = ctypes.create_unicode_buffer(n + 1)
            ctypes.windll.user32.GetWindowTextW(hwnd, buf, n + 1)
            t = buf.value.lower()
            if (
                "zoom meeting" in t
                or ("microsoft teams" in t and ("meeting" in t or "call in progress" in t))
                or t.startswith("meet - ")
            ):
                found.append(t)
        return True

    try:
        ctypes.windll.user32.EnumWindows(cb, 0)
    except Exception:
        return False
    return bool(found)


def _meeting_watcher(get_icon, stop: "threading.Event") -> None:
    """Poll for meeting windows; toast once when one appears so people don't forget
    to record. Detection only — recording still needs a click (browser rule)."""
    in_meeting = False
    while not stop.wait(10):
        try:
            now = _meeting_window_open()
        except Exception:
            continue
        if now and not in_meeting:
            icon = get_icon()
            if icon is not None:
                try:
                    icon.notify(
                        "Meeting detected — open Shruti and hit ● Record here to capture it.",
                        "Shruti",
                    )
                except Exception:
                    logging.getLogger("shruti.desktop").info("toast failed", exc_info=True)
        in_meeting = now


def _tray(url: str, stop: "threading.Event") -> None:
    """Tray icon with Open/Quit. If the tray can't start, just block until stopped."""
    try:
        import pystray
        from PIL import Image, ImageDraw

        logo = bundle_dir() / "skyroot_logo.png"
        if logo.is_file():
            src = Image.open(logo).convert("RGBA")
            img = Image.new("RGBA", (64, 64), "#f4f1e8")  # paper behind the black glyph
            src.thumbnail((52, 52))
            img.paste(src, ((64 - src.width) // 2, (64 - src.height) // 2), src)
        else:
            img = Image.new("RGB", (64, 64), "#147a5c")
            d = ImageDraw.Draw(img)
            d.rectangle([8, 8, 56, 56], outline="#f4f1e8", width=4)
            d.text((22, 18), "S", fill="#f4f1e8")

        def do_open(_icon=None, _item=None):
            webbrowser.open(url)

        def do_quit(icon, _item=None):
            stop.set()
            icon.stop()

        icon = pystray.Icon(
            "shruti",
            img,
            "Shruti — meeting notes",
            menu=pystray.Menu(
                pystray.MenuItem("Open Shruti", do_open, default=True),
                pystray.MenuItem("Quit", do_quit),
            ),
        )
        threading.Thread(
            target=_meeting_watcher, args=(lambda: icon, stop), daemon=True, name="meeting-watch"
        ).start()
        icon.run()  # blocks until Quit
    except Exception:
        logging.getLogger("shruti.desktop").warning("tray unavailable", exc_info=True)
        stop.wait()


def main() -> int:
    data = appdata()
    # cwd → our data dir BEFORE any shruti import: pydantic-settings reads ".env"
    # relative to cwd, and launching the exe from a repo checkout must never pick
    # up a stray dev .env (found the hard way: ASR_DEVICE=cuda leaked into a run)
    os.chdir(data)
    logging.basicConfig(
        filename=str(data / "shruti.log"),
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    log = logging.getLogger("shruti.desktop")

    # Windows proactor logs a full ERROR traceback every time a browser tab drops
    # its connection (WinError 10054) — pure noise that makes the log look broken
    class _MuteClientDisconnects(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            return "ConnectionResetError" not in record.getMessage() and (
                record.exc_info is None
                or not isinstance(record.exc_info[1], ConnectionResetError)
            )

    logging.getLogger("asyncio").addFilter(_MuteClientDisconnects())

    if _shruti_already_running(PREFERRED_PORT):  # second launch = just open the window
        webbrowser.open(f"http://127.0.0.1:{PREFERRED_PORT}")
        # hard exit: a plain return sometimes left this process alive in the frozen
        # exe (observed: a "second launch" from 13:26 still running an hour later)
        os._exit(0)

    configure_env(data)

    from shruti_core.userconfig import apply_overlay

    apply_overlay()  # user's saved Settings win over the seeded defaults

    from shruti_core.db import get_engine
    from shruti_core.models import Base

    Base.metadata.create_all(get_engine())  # schema comes straight from models.py

    from shruti_worker.main import main as worker_main

    threading.Thread(target=worker_main, daemon=True, name="shruti-worker").start()

    import uvicorn

    from shruti_api.main import app

    port = _pick_port()
    url = f"http://127.0.0.1:{port}"
    # log_config=None: uvicorn's dictConfig references formatter classes by import
    # string, which breaks frozen (PyInstaller) apps — our root file logger is enough
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", log_config=None)
    )
    threading.Thread(target=server.run, daemon=True, name="shruti-api").start()

    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and not _shruti_already_running(port):
        time.sleep(0.3)
    log.info("shruti desktop up at %s (data: %s)", url, data)
    webbrowser.open(url)

    stop = threading.Event()
    _tray(url, stop)  # blocks until Quit (or forever if the tray failed)
    server.should_exit = True
    time.sleep(0.5)
    return 0


if __name__ == "__main__":
    # MUST be first: diarization runs in a spawned child process (sherpa holds the
    # GIL for minutes and would freeze the app in-process). In a frozen exe, spawn
    # re-executes Shruti.exe — freeze_support() makes the child run its worker
    # function instead of launching a whole second app.
    import multiprocessing

    multiprocessing.freeze_support()

    # windowed (noconsole) exe: an uncaught exception would only show as a cryptic
    # dialog — put the traceback in the log file where it can actually be read
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        logging.getLogger("shruti.desktop").exception("fatal startup error")
        raise
