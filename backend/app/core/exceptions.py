"""Shared external-effect contract. Live sends use mail_send + approved drafts.

The former single-mailbox adapter is no longer executable.
"""


class ExternalUnknown(Exception):
    """An external write may have happened; reconcile before retrying."""
