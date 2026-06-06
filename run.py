"""Convenience launcher: python run.py"""
from pathlib import Path
from nicegui import ui, app

app.add_static_files('/static', Path(__file__).parent / 'app' / 'static')

import app.ui.main  # registers the @ui.page('/') route  # noqa: F401

ui.run(
    host="127.0.0.1",   # localhost only — not exposed on the network
    title="Collection",
    port=8080,
    reload=False,
    show=False,
    favicon=Path(__file__).parent / "favicon.ico",
)
