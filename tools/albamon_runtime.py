"""Small runtime primitives for atomic files and single-instance jobs."""

from __future__ import annotations

import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, TextIO

try:
    import fcntl
except ImportError:  # pragma: no cover - scheduled production runs use macOS.
    fcntl = None


class AlreadyRunningError(RuntimeError):
    """Raised when another process already owns a non-blocking task lock."""


@contextmanager
def exclusive_file_lock(path: Path) -> Iterator[None]:
    """Hold one non-blocking process lock for the lifetime of the context."""

    if fcntl is None:
        raise RuntimeError("cross-process file locking is unavailable")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise AlreadyRunningError(f"task lock is already held: {path}") from exc
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


@contextmanager
def atomic_text_writer(
    path: Path,
    *,
    encoding: str = "utf-8",
    newline: str | None = None,
) -> Iterator[TextIO]:
    """Write a text file durably and expose it only after a complete flush."""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with temporary.open(
            "w",
            encoding=encoding,
            newline=newline,
        ) as handle:
            yield handle
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_write_text(
    path: Path,
    content: str,
    *,
    encoding: str = "utf-8",
) -> None:
    with atomic_text_writer(path, encoding=encoding) as handle:
        handle.write(content)
