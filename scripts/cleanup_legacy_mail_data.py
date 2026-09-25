"""Remove the retired pre-mail-agent runtime from the active database.

Run only while API and worker are stopped and after scripts/backup.ps1.  The
current mail accounts, credentials, mail_message records, and the synthetic
Agent test account are deliberately outside the deletion rules.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "zhixing.db"
CHECKPOINTS = ROOT / "data" / "checkpoints.db"


def placeholders(values: set[str]) -> str:
    return ",".join("?" for _ in values) or "''"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if (ROOT / "data" / "processes.json").exists():
        raise SystemExit("Stop API and worker before cleaning data")

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    legacy_runs = {
        row[0]
        for row in conn.execute(
            "SELECT id FROM records WHERE kind='run' "
            "AND json_extract(body,'$.message.source') IN ('email','demo')"
        )
    }
    legacy_messages = {
        row[0]
        for row in conn.execute(
            "SELECT id FROM records WHERE kind='message' "
            "AND json_extract(body,'$.source') IN ('email','demo')"
        )
    }
    run_slots = placeholders(legacy_runs)
    run_args = tuple(legacy_runs)
    delete_ids = set(legacy_runs) | set(legacy_messages)
    if legacy_runs:
        for row in conn.execute(
            f"SELECT id FROM records WHERE scope IN ({run_slots}) "
            f"OR json_extract(body,'$.run_id') IN ({run_slots})",
            run_args + run_args,
        ):
            delete_ids.add(row[0])
    # These kinds and scopes belong to the retired Feishu/email workflow.
    for row in conn.execute(
        "SELECT id FROM records WHERE kind='draft' "
        "OR (kind IN ('todo','calendar','reminder','notification') "
        "AND scope NOT LIKE 'web:mail:%') "
        "OR kind='pairing' OR id='mail-cursor' "
        "OR (kind='schedule' AND id LIKE 'daily:%')"
    ):
        delete_ids.add(row[0])

    kinds: dict[str, int] = {}
    if delete_ids:
        slots = placeholders(delete_ids)
        for row in conn.execute(
            f"SELECT kind,COUNT(*) FROM records WHERE id IN ({slots}) GROUP BY kind",
            tuple(delete_ids),
        ):
            kinds[row[0]] = row[1]
    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "database": str(DB),
        "policy": "remove retired email/demo workflow; preserve current mail_* data and test mailbox",
        "legacy_run_count": len(legacy_runs),
        "legacy_message_count": len(legacy_messages),
        "record_count": len(delete_ids),
        "record_kinds": kinds,
        "applied": bool(args.apply),
    }
    out = ROOT / "artifacts" / "data-cleanup" / datetime.now().strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=False)
    (out / "cleanup-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if not args.apply:
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return

    with conn:
        if delete_ids:
            slots = placeholders(delete_ids)
            conn.execute(f"DELETE FROM records WHERE id IN ({slots})", tuple(delete_ids))
        if legacy_runs:
            slots = placeholders(legacy_runs)
            conn.execute(f"DELETE FROM jobs WHERE run_id IN ({slots})", tuple(legacy_runs))
        action_ids = []
        for run_id in legacy_runs:
            # Action ids are stable run-id-prefixed values in the old runtime.
            action_ids.extend(row[0] for row in conn.execute(
                "SELECT action_id FROM ledger WHERE action_id LIKE ?", (run_id + "-%",)
            ))
        if action_ids:
            conn.execute(
                f"DELETE FROM ledger WHERE action_id IN ({placeholders(set(action_ids))})",
                tuple(set(action_ids)),
            )
    conn.execute("VACUUM")
    conn.close()

    checkpoint_deleted = 0
    if CHECKPOINTS.exists() and legacy_runs:
        cp = sqlite3.connect(CHECKPOINTS)
        slots = placeholders(legacy_runs)
        with cp:
            checkpoint_deleted = cp.execute(
                f"SELECT COUNT(*) FROM checkpoints WHERE thread_id IN ({slots})",
                tuple(legacy_runs),
            ).fetchone()[0]
            cp.execute(f"DELETE FROM writes WHERE thread_id IN ({slots})", tuple(legacy_runs))
            cp.execute(f"DELETE FROM checkpoints WHERE thread_id IN ({slots})", tuple(legacy_runs))
        cp.execute("VACUUM")
        cp.close()
    manifest["checkpoint_count"] = checkpoint_deleted
    (out / "cleanup-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(out)


if __name__ == "__main__":
    main()
