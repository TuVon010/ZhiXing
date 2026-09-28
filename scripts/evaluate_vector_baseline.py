"""Small, independently worded synthetic keyword-vs-vector pilot.

This pilot is deliberately separate from the 200 templated regression cases. It
does not replace the larger, human-adjudicated holdout described in the V2 plan.
"""

import hashlib
import json
import os
import statistics
import sys
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["ZHIXING_MAIL_WORKER"] = "1"
os.environ.pop("ZHIXING_DISABLE_LOCAL_MODELS", None)
os.environ["ZHIXING_MODE"] = "demo"


def main():
    fixture = ROOT / "tests" / "fixtures" / "rag_vector_baseline.jsonl"
    cases = [json.loads(line) for line in fixture.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len({case["id"] for case in cases}) == len(cases)
    assert len({case["question"] for case in cases}) == len(cases)
    output = ROOT / "artifacts" / "mail-evaluations" / f"vector-baseline-{datetime.now():%Y%m%d-%H%M%S}"
    output.mkdir(parents=True, exist_ok=False)
    os.environ["ZHIXING_DATA_DIR"] = str(output / "data")
    from backend.app.persistence.store import Store
    from backend.app.modules.mail.repository import initialize, save_account
    from backend.app.modules.mail.schemas import MailAccount
    from backend.app.modules.mail.ingestion import store_message
    from backend.app.modules.mail.retrieval import index_message, load_models, model_manifest, search
    from backend.app.modules.mail.filtering import DEFAULT_RULES

    load_models()  # An unavailable model fails the run; keyword fallback is not a vector result.
    db = Store(output / "data" / "benchmark.db")
    initialize(db)
    accounts = []
    for i in range(3):
        aid = save_account(db, MailAccount(name=f"评测账号{i}", address=f"pilot{i}@qq.com"))["id"]
        accounts.append(aid)
        db.insert("setting", {**DEFAULT_RULES, "whitelist_senders": ["synthetic@example.com"]}, id="mail-filter:" + aid)
    truth = {}
    with (output / "index.jsonl").open("w", encoding="utf-8") as log:
        for i, case in enumerate(cases):
            aid = accounts[i % len(accounts)]
            ids = []
            for j, body in enumerate(case["messages"]):
                msg = EmailMessage()
                msg["From"] = "synthetic@example.com"
                msg["To"] = f"pilot{i % len(accounts)}@qq.com"
                msg["Subject"] = case["subject"]
                msg["Message-ID"] = f"<{case['id']}-{j}@evaluation.local>"
                if j:
                    msg["In-Reply-To"] = f"<{case['id']}-0@evaluation.local>"
                msg.set_content(body)
                mid = store_message(db, aid, "evaluation", i * 10 + j + 1, msg.as_bytes(), "2026-09-22T10:00:00+00:00")
                indexed = index_message(db, mid)
                if indexed.get("degraded"):
                    raise RuntimeError(f"Index degraded: {case['id']} {indexed}")
                ids.append(mid)
                log.write(json.dumps({"case": case["id"], "message_id": mid, "index": indexed}, ensure_ascii=False) + "\n")
            truth[case["id"]] = {"account_id": aid, "relevant_ids": [ids[n] for n in case["relevant_indices"]]}
    modes = ("keyword", "vector", "fusion", "hybrid")
    rows = []
    with (output / "rankings.jsonl").open("w", encoding="utf-8") as log:
        for case in cases:
            for mode in modes:
                result = search(db, {"account_ids": [truth[case["id"]]["account_id"]],
                                     "query": case["question"], "mode": mode})
                if result.get("degraded") or result["mode"] != mode:
                    raise RuntimeError(f"Search degraded: {case['id']} {mode} {result.get('reason')}")
                ranked = list(dict.fromkeys(e["message_id"] for e in result["evidence"]))
                relevant = set(truth[case["id"]]["relevant_ids"])
                row = {"case": case["id"], "category": case["category"], "mode": mode,
                       "ranked_message_ids": ranked, "relevant_message_ids": sorted(relevant),
                       "recall_at_5": len(set(ranked[:5]) & relevant) / len(relevant),
                       "recall_at_8": len(set(ranked[:8]) & relevant) / len(relevant),
                       "precision_at_1": int(bool(ranked) and ranked[0] in relevant),
                       "latency_ms": result["latency_ms"], "retrieval": result}
                rows.append(row)
                log.write(json.dumps(row, ensure_ascii=False) + "\n")
    metrics = {}
    for category in ("all", *sorted({case["category"] for case in cases})):
        metrics[category] = {}
        for mode in modes:
            group = [row for row in rows if row["mode"] == mode and (category == "all" or row["category"] == category)]
            metrics[category][mode] = {key: round(statistics.mean(row[key] for row in group), 4)
                                       for key in ("recall_at_5", "recall_at_8", "precision_at_1", "latency_ms")}
            metrics[category][mode]["n"] = len(group)
    manifest = {"status": "completed", "kind": "synthetic_pilot", "case_count": len(cases),
                "fixture_sha256": hashlib.sha256(fixture.read_bytes()).hexdigest(), "models": model_manifest(),
                "metrics": metrics, "limitations": ["Small synthetic pilot, not the sealed V2 holdout",
                "Author-labelled messages, no independent human adjudication", "No answer-generation or memory metrics"]}
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "cases.json").write_text(json.dumps(cases, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output), "metrics": metrics}, ensure_ascii=False, indent=2), flush=True)
    db.engine.dispose()


if __name__ == "__main__":
    main()
