"""Small, bounded model context for the mail assistant's decision loop.

The full evidence and tool results remain in the turn and audit records. Only
the compact view is sent again on the next model call.
"""

import json
import math
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from backend.app.modules.mail.schemas import SearchRequest


INPUT_TOKEN_LIMIT = 24_000
MAX_DECISIONS = 6
MAX_SEARCHES = 3
LOCAL_ZONE = ZoneInfo("Asia/Shanghai")


def estimate_prompt_tokens(messages: list[dict], schema: dict | None) -> int:
    """Conservative token estimate for an arbitrary OpenAI-compatible model.

    Provider usage is authoritative after the call. Before a call, a local
    estimate is needed because providers need not expose a tokenizer. Wide
    characters and remaining UTF-8 bytes are converted separately; raw byte
    length is never compared directly with a Token limit.
    """
    wire = [*messages]
    if schema is not None:
        wire.append({"role": "system", "content": "返回严格 JSON，符合 schema：" + json.dumps(schema, ensure_ascii=False)})
    serialized = json.dumps(wire, ensure_ascii=False)
    wide = sum(unicodedata.east_asian_width(char) in {"W", "F"} for char in serialized)
    narrow_bytes = sum(len(char.encode("utf-8")) for char in serialized if unicodedata.east_asian_width(char) not in {"W", "F"})
    return math.ceil(wide * 1.5 + narrow_bytes / 3) + 32 * len(wire) + 128


def observed_prompt_tokens(calls: list[dict]) -> int:
    """Use actual provider usage; estimate only calls with missing usage."""
    total = 0
    for call in calls:
        body = call["body"]
        if body.get("purpose") != "mail_agent":
            continue
        prompt = (body.get("usage") or {}).get("prompt_tokens")
        if isinstance(prompt, int) and prompt >= 0:
            total += prompt
        else:
            # model_json persists the exact outbound messages, including schema.
            wire = body.get("messages") or []
            total += estimate_prompt_tokens(wire, None) if wire else 0
    return total


def compact_evidence(items: list[dict]) -> list[dict]:
    return [{
        "id": item.get("id"), "message_id": item.get("message_id"),
        "subject": str(item.get("subject") or "")[:120],
        "sender": str(item.get("sender") or "")[:100],
        "received_at": item.get("received_at"),
        "location": str(item.get("location") or "")[:120],
        "text": str(item.get("text") or "")[:700],
    } for item in items[-8:]]


def compact_steps(steps: list[dict]) -> list[dict]:
    compact = []
    for step in steps[-4:]:
        result = step.get("result")
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except (ValueError, TypeError):
                pass
        if isinstance(result, dict):
            if "error" in result:
                summary = {"error": str(result["error"])[:300]}
            elif step.get("tool") == "search":
                summary = {"matches": len(result.get("evidence") or []),
                           "degraded": result.get("degraded"),
                           "warnings": result.get("warnings", [])[:2]}
            else:
                summary = json.dumps(result, ensure_ascii=False, default=str)[:700]
        else:
            summary = json.dumps(result, ensure_ascii=False, default=str)[:700]
        compact.append({"round": step.get("round"), "tool": step.get("tool"), "result": summary})
    return compact


def compact_history(turns: list[dict]) -> list[dict]:
    return [{"text": str(turn["body"].get("text") or "")[:300],
             "answer": str(turn["body"].get("answer") or "")[:500]}
            for turn in turns[-4:]]


def _search_time(value, *, end: bool):
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value.strip()):
        parsed = datetime.fromisoformat(value.strip()).replace(tzinfo=LOCAL_ZONE)
        if end:
            parsed += timedelta(days=1)  # SearchRequest.end is exclusive.
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    else:
        raise ValueError("时间格式无法识别")
    return parsed.replace(tzinfo=LOCAL_ZONE) if not parsed.tzinfo else parsed


def normalize_search_args(args: dict, accounts: list[str], question: str, reference_now=None) -> tuple[dict, list[str]]:
    """Repair model-supplied filters without widening the account boundary."""
    args = args if isinstance(args, dict) else {}
    warnings = []
    query = str(args.get("query") or question).strip()[:2000] or question[:2000]
    value = {"account_ids": accounts, "query": query}
    for key in ("start", "end"):
        try:
            parsed = _search_time(args.get(key), end=key == "end")
            if parsed:
                value[key] = parsed
        except (TypeError, ValueError):
            warnings.append(f"模型给出的{key}时间无效，已忽略该筛选")
    if value.get("start") and value.get("end") and value["start"] >= value["end"]:
        value.pop("start"); value.pop("end")
        warnings.append("模型给出的时间范围前后颠倒，已忽略该范围")
    sender = str(args.get("sender") or "").strip().lower()
    if sender:
        if "@" in sender:
            value["sender"] = sender
        else:
            warnings.append("联系人名称不是准确邮箱地址，保留关键词检索")
    if "start" not in value and any(word in question for word in ("最近", "近期", "近来")):
        amount = re.search(r"(?:最近|近)(\d{1,2})(天|周|个月)", question)
        days = int(amount.group(1)) * {"天": 1, "周": 7, "个月": 30}[amount.group(2)] if amount else 30
        value["start"] = (reference_now or datetime.now(timezone.utc)) - timedelta(days=min(days, 365))
        warnings.append(f"按问题中的近期语义限定最近 {min(days, 365)} 天")
    return SearchRequest.model_validate(value).model_dump(mode="json"), warnings
