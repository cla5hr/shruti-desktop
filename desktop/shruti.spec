# PyInstaller spec for the Shruti desktop exe. Build via desktop/build.ps1
# (it prepares the venv, the web build, and the ffmpeg folder this spec expects).
from PyInstaller.utils.hooks import collect_all

datas, binaries, hiddenimports = [], [], []
# faster-whisper's runtime pieces carry native DLLs and data files PyInstaller
# can't see through imports alone.
for pkg in (
    "ctranslate2",
    "faster_whisper",
    "av",
    "tokenizers",
    "huggingface_hub",
    "onnxruntime",
    "sherpa_onnx",  # speaker separation (native dlls inside the wheel)
    "uvicorn",  # protocol/loop modules are chosen at runtime by name
):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(
    ["shruti_desktop.py"],
    pathex=[],
    binaries=binaries,
    datas=datas
    + [
        ("../apps/web/dist", "webdist"),
        ("ffmpeg", "ffmpeg"),
        ("skyroot_logo.png", "."),
    ],
    hiddenimports=hiddenimports
    + [
        # fastapi discovers form-data support by importing this dynamically
        "multipart",
        "python_multipart",
        "pystray._win32",
    ],
    excludes=[
        # server-only / heavyweight things the desktop build must never drag in
        "torch",
        "torchaudio",
        "pyannote",
        "playwright",
        "psycopg",
        "alembic",
        "matplotlib",
        "IPython",
        "tkinter",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Shruti",
    console=False,
    icon="icon.ico",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Shruti",
)
