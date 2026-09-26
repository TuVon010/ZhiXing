"""TLS-only IMAP connection adapter with deterministic cleanup."""

from contextlib import contextmanager
import imaplib
import ssl


@contextmanager
def open_imap(account: dict, password: str):
    """Authenticate one account and always attempt a clean logout."""
    context = ssl.create_default_context()
    if account["imap_tls"] == "ssl":
        client = imaplib.IMAP4_SSL(
            account["imap_host"],
            account["imap_port"],
            ssl_context=context,
            timeout=30,
        )
    else:
        client = imaplib.IMAP4(
            account["imap_host"], account["imap_port"], timeout=30
        )
    try:
        if account["imap_tls"] == "starttls":
            client.starttls(ssl_context=context)
        client.login(account["username"], password)
        yield client
    finally:
        try:
            client.logout()
        except Exception:
            pass
