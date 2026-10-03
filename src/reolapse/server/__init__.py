"""Local HTTP server behind the desktop app."""

import sys


def _single_instance():
    """Hold a lock for the server's lifetime: two servers would run the same saved queue twice."""
    from ..settings import SETTINGS_PATH

    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    fh = open(SETTINGS_PATH.parent / "server.lock", "w")
    try:
        if sys.platform == "win32":
            import msvcrt

            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        raise SystemExit("Another Reolapse server is already running (only one may run the job queue).")
    return fh  # keep open: closing releases the lock


def serve(host: str = "127.0.0.1", port: int = 8765) -> None:
    import uvicorn

    from .app import create_app

    lock = _single_instance()  # noqa: F841
    uvicorn.run(create_app(), host=host, port=port, log_level="info")
