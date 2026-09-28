"""Re-score an immutable mail retrieval archive with message-level, fixed-K metrics.

This does not run models or create new samples. It is an audit of a prior run.
"""

import argparse
import hashlib
import json
import math
import statistics
from collections import defaultdict
from datetime import datetime
from pathlib import Path


MODES = ("keyword", "vector", "fusion", "hybrid")
KS = (1, 3, 5, 8)


def read_jsonl(path):
    with path.open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def percentile(values, fraction):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, math.ceil(fraction * len(ordered)) - 1)]


def score(ranked, relevance, latency):
    result = {"latency_ms": latency}
    for k in KS:
        top = ranked[:k]
        hits = sum(message_id in relevance for message_id in top)
        result[f"precision_at_{k}"] = hits / k
        result[f"recall_at_{k}"] = hits / len(relevance)
    dcg = sum((2 ** relevance.get(mid, 0) - 1) / math.log2(rank + 2)
              for rank, mid in enumerate(ranked[:8]))
    ideal = sum((2 ** grade - 1) / math.log2(rank + 2)
                for rank, grade in enumerate(sorted(relevance.values(), reverse=True)[:8]))
    result["ndcg_at_8"] = dcg / ideal
    result["mrr_at_8"] = next((1 / (rank + 1) for rank, mid in enumerate(ranked[:8])
                               if mid in relevance), 0)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path, help="Existing immutable evaluation archive")
    parser.add_argument("--output", type=Path, help="New audit directory (must not exist)")
    args = parser.parse_args()
    source = args.source.resolve()
    output = args.output or source.parent / f"audit-{datetime.now():%Y%m%d-%H%M%S}"
    output.mkdir(parents=True, exist_ok=False)
    files = {name: source / name for name in ("cases.json", "index.jsonl", "rankings.jsonl", "manifest.json")}
    for path in files.values():
        if not path.is_file():
            raise FileNotFoundError(path)
    cases = json.loads(files["cases.json"].read_text(encoding="utf-8"))
    index = read_jsonl(files["index.jsonl"])
    rankings = read_jsonl(files["rankings.jsonl"])
    by_case = defaultdict(list)
    for item in index:
        by_case[item["case"]].append(item["message_id"])
        if item.get("degraded"):
            raise ValueError(f"Index degraded: {item['case']}")
    case_map = {case["id"]: case for case in cases}
    if len(case_map) != len(cases) or len(rankings) != len(cases) * len(MODES):
        raise ValueError("Missing or duplicate cases/rankings")
    per_query = []
    seen = set()
    for item in rankings:
        case_id, mode = item["case"], item["mode"]
        if case_id not in case_map or mode not in MODES or (case_id, mode) in seen:
            raise ValueError(f"Unknown or duplicate ranking: {case_id}/{mode}")
        seen.add((case_id, mode))
        case = case_map[case_id]
        mids = by_case[case_id]
        if len(mids) != len(case["relevance"]):
            raise ValueError(f"Label-message alignment failed: {case_id}")
        relevance = dict(zip(mids, case["relevance"]))
        retrieval = item["retrieval"]
        if retrieval.get("degraded") or retrieval.get("mode") != mode:
            raise ValueError(f"Retrieval degraded or changed mode: {case_id}/{mode}")
        ranked = list(dict.fromkeys(e["message_id"] for e in retrieval["evidence"]))
        metrics = score(ranked, relevance, retrieval["latency_ms"])
        per_query.append({"case": case_id, "split": case["split"], "mode": mode,
                          "family": case_id.rsplit("-", 1)[-1], "ranked_message_ids": ranked,
                          "relevant_message_ids": mids, "metrics": metrics})
    if len(seen) != len(cases) * len(MODES):
        raise ValueError("Incomplete mode coverage")
    summary = {}
    for split in sorted({case["split"] for case in cases}):
        summary[split] = {}
        for mode in MODES:
            group = [row["metrics"] for row in per_query if row["split"] == split and row["mode"] == mode]
            names = [name for name in group[0] if name != "latency_ms"]
            summary[split][mode] = {name: round(statistics.mean(row[name] for row in group), 4)
                                    for name in names}
            times = [row["latency_ms"] for row in group]
            summary[split][mode].update({"latency_mean_ms": round(statistics.mean(times), 2),
                                         "latency_p95_ms": round(percentile(times, .95), 2)})
    checksums = {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in files.items()}
    audit = {"source": str(source), "source_sha256": checksums, "case_count": len(cases),
             "ranking_count": len(rankings), "independent_template_families":
             len({row["family"] for row in per_query}), "metrics": summary,
             "limitations": ["Reanalysis only: no new retrieval run", "Templated synthetic queries; splits share templates",
                             "Evidence is truncated to 8 messages; this cannot measure pre-truncation recall",
                             "No human relevance or answer-support labels"]}
    (output / "audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    with (output / "per_query.jsonl").open("w", encoding="utf-8") as stream:
        for row in per_query:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(json.dumps({"output": str(output), "metrics": summary}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
