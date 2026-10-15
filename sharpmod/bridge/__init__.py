"""Headless JSON-lines bridge between sharpmod and the Tauri desktop frontend.

Run as ``python -I -u -X utf8 -m sharpmod.bridge``. The process reads one JSON
request per line on stdin and writes one JSON response per line on stdout
(protocol version 1). It serves semantic operations by calling existing
sharpmod functions and never imports Qt.

This package is imported before ``__main__`` runs, so it must stay free of
side effects and heavy imports.
"""
