import os
import re
import hashlib
import multiprocessing
import resource
from typing import Callable, Any
from urllib.parse import quote
from fastapi import HTTPException

SAFE_NAME_RE = re.compile(r'^[A-Za-z0-9_.-]+$')


def sanitize_filename(name: str) -> str:
    """Validate and return a safe filename component."""
    if not SAFE_NAME_RE.fullmatch(name):
        raise HTTPException(status_code=400, detail="Invalid name")
    return name


def hashed_path(identifier: str, prefix: str, ext: str, output_dir: str) -> str:
    """Create a hashed path ensuring no traversal."""
    sanitize_filename(identifier)
    digest = hashlib.sha256(identifier.encode()).hexdigest()
    filename = f"{prefix}_{digest}.{ext}"
    path = os.path.abspath(os.path.join(output_dir, filename))
    return path


def content_disposition(filename: str) -> dict:
    """Return safe Content-Disposition headers."""
    fname = sanitize_filename(filename)
    return {"Content-Disposition": f"attachment; filename*=UTF-8''{quote(fname)}"}


def run_isolated(func: Callable[[], Any], *, timeout: int = 5, max_memory: int = 256 * 1024 * 1024) -> Any:
    """Run callable in a subprocess with resource limits."""
    q: multiprocessing.Queue = multiprocessing.Queue()

    def target() -> None:
        try:
            if max_memory:
                resource.setrlimit(resource.RLIMIT_AS, (max_memory, max_memory))
            result = func()
            q.put((True, result))
        except Exception as exc:  # pragma: no cover - forwarded to parent
            q.put((False, str(exc)))

    p = multiprocessing.Process(target=target)
    p.start()
    p.join(timeout)
    if p.is_alive():
        p.terminate()
        p.join()
        raise TimeoutError("Operation timed out")

    if not q.empty():
        ok, data = q.get()
        if ok:
            return data
        raise RuntimeError(data)
    raise RuntimeError("No result from subprocess")
