from typing import Optional


def parse_time_to_seconds(time_str: str) -> int:
    parts = time_str.strip().split(":")
    hour = int(parts[0])
    minute = int(parts[1])
    second = int(parts[2]) if len(parts) > 2 else 0
    return hour * 3600 + minute * 60 + second


def time_diff_seconds(calc_str: str, ref_str: str) -> Optional[int]:
    try:
        if calc_str in ("N/A", " N/A ", "Error", None) or ref_str in (
            "N/A",
            " N/A ",
            "Error",
            None,
        ):
            return None
        calc_s = parse_time_to_seconds(calc_str)
        ref_s = parse_time_to_seconds(ref_str)
        diff = calc_s - ref_s
        if diff > 12 * 3600:
            diff -= 24 * 3600
        elif diff < -12 * 3600:
            diff += 24 * 3600
        return diff
    except (ValueError, TypeError, KeyError, RuntimeError, OSError):
        return None
