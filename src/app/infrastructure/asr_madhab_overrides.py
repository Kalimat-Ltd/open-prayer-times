from __future__ import annotations

import datetime
import json
from typing import Any, Dict, List, Optional


OverrideBlock = Dict[str, Any]


def _normalize_month_day(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    parts = text.split("-")
    if len(parts) != 2:
        return None
    try:
        month = int(parts[0])
        day = int(parts[1])
        datetime.date(2000, month, day)
    except (TypeError, ValueError):
        return None
    return f"{month:02d}-{day:02d}"


def normalize_asr_madhab(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        text = value.strip().lower()
        if not text:
            return None
        if text in {"standard", "0"}:
            return 0
        if text in {"hanafi", "1"}:
            return 1
    try:
        numeric = int(float(value))
    except (TypeError, ValueError):
        return None
    if numeric in (0, 1):
        return numeric
    return None


def parse_asr_madhab_overrides(overrides_json: Any) -> List[OverrideBlock]:
    if not overrides_json:
        return []
    if isinstance(overrides_json, list):
        raw_blocks = overrides_json
    else:
        try:
            raw_blocks = json.loads(str(overrides_json))
        except (TypeError, ValueError):
            return []
    if not isinstance(raw_blocks, list):
        return []

    cleaned: List[OverrideBlock] = []
    for block in raw_blocks:
        if not isinstance(block, dict):
            continue
        start = _normalize_month_day(block.get("start"))
        end = _normalize_month_day(block.get("end"))
        asr_madhab = normalize_asr_madhab(block.get("asr_madhab"))
        if start is None or end is None or asr_madhab is None:
            continue
        cleaned.append({"start": start, "end": end, "asr_madhab": asr_madhab})
    return cleaned


def dumps_asr_madhab_overrides(blocks: List[OverrideBlock]) -> str:
    cleaned = parse_asr_madhab_overrides(blocks)
    if not cleaned:
        return ""
    return json.dumps(cleaned, separators=(",", ":"))


def month_day_in_range(target_date: datetime.date, start: str, end: str) -> bool:
    month_day = target_date.strftime("%m-%d")
    if start <= end:
        return start <= month_day <= end
    return month_day >= start or month_day <= end


def get_asr_madhab_override_for_date(
    overrides_json: Any,
    target_date: Optional[datetime.date],
) -> Optional[int]:
    if target_date is None:
        return None
    for block in parse_asr_madhab_overrides(overrides_json):
        start = str(block.get("start") or "")
        end = str(block.get("end") or "")
        if month_day_in_range(target_date, start, end):
            return normalize_asr_madhab(block.get("asr_madhab"))
    return None


def resolve_effective_asr_madhab(
    base_asr_madhab: Any,
    overrides_json: Any,
    target_date: Optional[datetime.date],
) -> int:
    override = get_asr_madhab_override_for_date(overrides_json, target_date)
    if override is not None:
        return int(override)
    normalized = normalize_asr_madhab(base_asr_madhab)
    return 0 if normalized is None else int(normalized)
