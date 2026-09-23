"""Shared external-effect contract. Live sends use mail_send + approved drafts.

The former Feishu and single-mailbox adapters are no longer executable.
"""


class ExternalUnknown(Exception):
    """An external write may have happened; reconcile before retrying."""
