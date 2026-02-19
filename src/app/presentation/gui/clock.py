"""Clock-shift block utilities used to apply per-date reference-time offset normalization."""

import json


def get_clock_offset_for_date(clock_offsets_json, target_date):
    if not clock_offsets_json:
        return 0

    try:
        blocks = json.loads(clock_offsets_json)
    except (TypeError, ValueError):
        return 0

    month_day = (target_date.month, target_date.day)
    for block in blocks:
        start_parts = block["start"].split("-")
        end_parts = block["end"].split("-")
        start = (int(start_parts[0]), int(start_parts[1]))
        end = (int(end_parts[0]), int(end_parts[1]))
        offset = block["offset"]

        if start <= end:
            if start <= month_day <= end:
                return -offset
        else:
            if month_day >= start or month_day <= end:
                return -offset

    return 0
