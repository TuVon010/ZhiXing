"""Compatibility entry point for ``python -m backend.worker``."""

from backend.app.workers.runner import main

__all__ = ["main"]

if __name__ == "__main__":
    main()
