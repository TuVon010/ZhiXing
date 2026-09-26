"""TLS-only SMTP connection adapter with deterministic cleanup."""

from contextlib import contextmanager
import smtplib
import ssl


@contextmanager
def open_smtp(account: dict, password: str):
    """Authenticate one account; callers remain responsible for idempotency."""
    context = ssl.create_default_context()
    if account["smtp_tls"] == "ssl":
        client = smtplib.SMTP_SSL(
            account["smtp_host"],
            account["smtp_port"],
            context=context,
            timeout=30,
        )
    else:
        client = smtplib.SMTP(
            account["smtp_host"], account["smtp_port"], timeout=30
        )
    try:
        if account["smtp_tls"] == "starttls":
            client.ehlo()
            client.starttls(context=context)
            client.ehlo()
        client.login(account["username"], password)
        yield client
    finally:
        try:
            client.quit()
        except Exception:
            pass
