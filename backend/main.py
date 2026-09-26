"""Compatibility entry point for ``uvicorn backend.main:app``.

Application assembly lives in :mod:`backend.app.main`.  Keeping this tiny
module avoids coupling deployment scripts to the internal package layout.
"""

from backend.app.main import app

__all__ = ["app"]
