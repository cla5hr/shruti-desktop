"""Storage abstraction: filesystem now, S3/MinIO later without touching callers.

Note: pipeline steps that shell out to ffmpeg need a real filesystem path via
`path()`. A future S3 backend must materialize to a temp file there — callers
stay unchanged.
"""

from abc import ABC, abstractmethod
from collections.abc import Iterator
from pathlib import Path
from typing import BinaryIO

from shruti_core.settings import get_settings


class Storage(ABC):
    @abstractmethod
    def save(self, key: str, stream: BinaryIO) -> int:
        """Write stream to key, returns bytes written."""

    @abstractmethod
    def open(self, key: str) -> BinaryIO: ...

    @abstractmethod
    def path(self, key: str) -> Path:
        """Local filesystem path for tools like ffmpeg."""

    @abstractmethod
    def exists(self, key: str) -> bool: ...

    @abstractmethod
    def size(self, key: str) -> int: ...

    @abstractmethod
    def delete(self, key: str) -> None: ...

    @abstractmethod
    def append(self, key: str, data: bytes) -> int:
        """Append bytes to key (create if absent). Returns total size after append."""


class LocalFSStorage(Storage):
    CHUNK = 1024 * 1024

    def __init__(self, root: str | Path | None = None):
        self.root = Path(root if root is not None else get_settings().storage_root)

    def _p(self, key: str) -> Path:
        p = (self.root / key).resolve()
        root = self.root.resolve()
        if root not in p.parents and p != root:
            raise ValueError(f"storage key escapes root: {key!r}")
        return p

    def save(self, key: str, stream: BinaryIO) -> int:
        p = self._p(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        tmp = p.with_suffix(p.suffix + ".part")
        with tmp.open("wb") as out:
            while chunk := stream.read(self.CHUNK):
                out.write(chunk)
                written += len(chunk)
        tmp.replace(p)  # atomic-ish: no partial files under the final key
        return written

    def open(self, key: str) -> BinaryIO:
        return self._p(key).open("rb")

    def path(self, key: str) -> Path:
        return self._p(key)

    def exists(self, key: str) -> bool:
        return self._p(key).is_file()

    def size(self, key: str) -> int:
        return self._p(key).stat().st_size

    def delete(self, key: str) -> None:
        p = self._p(key)
        if p.is_file():
            p.unlink()

    def append(self, key: str, data: bytes) -> int:
        p = self._p(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("ab") as f:
            f.write(data)
        return p.stat().st_size

    def iter_keys(self, prefix: str = "") -> Iterator[str]:
        root = self.root.resolve()
        base = self._p(prefix) if prefix else root
        if not base.exists():
            return
        for f in base.rglob("*"):
            if f.is_file():
                yield f.relative_to(root).as_posix()


def get_storage() -> Storage:
    return LocalFSStorage()
