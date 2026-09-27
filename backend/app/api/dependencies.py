"""FastAPI dependencies shared by HTTP adapters.

Keeping construction here gives tests and future alternate persistence adapters
one override point.  Domain services receive the store explicitly and never
depend on FastAPI.
"""


def get_store():
    """Return the initialized application store for one request."""
    from backend.app.modules.mail.repository import initialize
    from backend.app.persistence.store import store

    initialize(store)
    return store
