"""Database transaction primitives shared by persistence adapters."""


def begin_immediate(connection) -> None:
    """Acquire SQLite's write reservation before a read-modify-write sequence."""
    connection.exec_driver_sql("BEGIN IMMEDIATE")
