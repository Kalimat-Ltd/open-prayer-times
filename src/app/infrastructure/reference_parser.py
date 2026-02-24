import datetime
from pathlib import Path
from typing import Dict, List, Tuple


def load_reference_file(
    filepath: Path | str, year: int | None = None
) -> Tuple[Dict, List]:
    all_reference_times = {}
    available_dates = []
    current_year = year if year is not None else datetime.date.today().year

    try:
        with Path(filepath).open("r", encoding="utf-8") as file_obj:
            for line in file_obj:
                parts = line.strip().split("\t")
                if len(parts) != 7:
                    continue

                date_str, fajr, sunrise, dhuhr, asr, maghrib, isha = parts
                try:
                    for fmt in ("%d-%b", "%d/%m", "%m/%d", "%Y-%m-%d", "%d-%m-%Y"):
                        try:
                            date_obj_tmp = datetime.datetime.strptime(date_str, fmt)
                            year_to_use = (
                                date_obj_tmp.year
                                if date_obj_tmp.year != 1900
                                else current_year
                            )
                            date_obj = datetime.date(
                                year_to_use, date_obj_tmp.month, date_obj_tmp.day
                            )
                            break
                        except ValueError:
                            pass
                    else:
                        continue

                    datetime.datetime.strptime(
                        fajr.split(":")[0] + ":" + fajr.split(":")[1], "%H:%M"
                    )
                    datetime.datetime.strptime(
                        isha.split(":")[0] + ":" + isha.split(":")[1], "%H:%M"
                    )

                    all_reference_times[date_obj] = {
                        "fajr": fajr,
                        "shurooq": sunrise,
                        "dhuhr": dhuhr,
                        "asr": asr,
                        "maghrib": maghrib,
                        "isha": isha,
                    }
                    available_dates.append(date_obj)
                except ValueError:
                    continue
    except (ValueError, TypeError, KeyError, RuntimeError, OSError):
        return {}, []

    return all_reference_times, available_dates
