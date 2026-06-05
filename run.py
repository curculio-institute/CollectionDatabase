"""Convenience launcher: python run.py"""
from nicegui import ui
import app.ui.main  # registers the @ui.page('/') route  # noqa: F401

ui.run(
    host="127.0.0.1",   # localhost only — not exposed on the network
    title="Collection",
    port=8080,
    reload=False,
    show=True,
    favicon="🪲",
)
