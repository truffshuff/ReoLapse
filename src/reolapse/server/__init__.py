"""Local HTTP server behind the desktop app."""


def serve(host: str = "127.0.0.1", port: int = 8765) -> None:
    import uvicorn

    from .app import create_app

    uvicorn.run(create_app(), host=host, port=port, log_level="info")
