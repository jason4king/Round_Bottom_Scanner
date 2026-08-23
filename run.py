from __future__ import annotations

import threading
import webbrowser

import uvicorn

from app.config import get_settings


def open_browser(url: str) -> None:
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()


if __name__ == "__main__":
    settings = get_settings()
    url = f"http://{settings.scanner_host}:{settings.scanner_port}"
    open_browser(url)
    uvicorn.run("app.main:app", host=settings.scanner_host, port=settings.scanner_port)

