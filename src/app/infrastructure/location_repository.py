import csv
import datetime
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional


@dataclass(frozen=True)
class LocationRecord:
    country_code: str
    name: str
    latitude: float
    longitude: float
    timezone: str
    elevation: float
    pressure: float
    temp: float
    calculation_method: str
    fajr_angle: float
    isha_angle: float
    isha_minutes: float
    isha_shafaq: str
    high_lat_method: int
    asr_madhab: int
    isha_harag: int
    fajr_offset: float
    shurooq_offset: float
    dhuhr_offset: float
    asr_offset: float
    maghrib_offset: float
    isha_offset: float
    optimized_lat: Optional[float] = None
    optimized_lon: Optional[float] = None
    high_lat_start_date: Optional[datetime.date] = None
    high_lat_end_date: Optional[datetime.date] = None
    residual_corrections: Optional[str] = None
    clock_offsets: Optional[str] = None
    aqrab_al_bilad: Optional[str] = None
    is_optimized: bool = False
    is_official: bool = False

    @property
    def effective_lat(self) -> float:
        """Return optimized_lat if available, otherwise latitude."""
        return self.optimized_lat if self.optimized_lat is not None else self.latitude

    @property
    def effective_lon(self) -> float:
        """Return optimized_lon if available, otherwise longitude."""
        return self.optimized_lon if self.optimized_lon is not None else self.longitude


def _parse_optional_float(value: str) -> Optional[float]:
    """Parse a string to float, returning None for empty/None strings."""
    if not value or value.strip() in ("", "None"):
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _parse_optional_date(value: str) -> Optional[datetime.date]:
    """Parse an ISO date string, returning None for empty/None strings."""
    if not value or value.strip() in ("", "None"):
        return None
    try:
        return datetime.date.fromisoformat(value.strip())
    except (ValueError, TypeError):
        return None


def _parse_optional_bool(value: str) -> bool:
    """Parse a boolean-ish string (True/1/yes → True, else False)."""
    if not value or value.strip() in ("", "None"):
        return False
    return value.strip().lower() in ("true", "1", "yes")


def _parse_optional_str(value: str) -> Optional[str]:
    """Return the string if non-empty, else None."""
    if not value or value.strip() in ("", "None"):
        return None
    return value.strip()


class CsvLocationRepository:
    def __init__(self, csv_path: Path):
        self.csv_path = csv_path

    def load_all(self) -> List[LocationRecord]:
        records: List[LocationRecord] = []
        with self.csv_path.open("r", encoding="utf-8", newline="") as file_obj:
            reader = csv.DictReader(file_obj)
            for row in reader:
                records.append(
                    LocationRecord(
                        country_code=(row.get("country_code") or "").upper(),
                        name=row["name"],
                        latitude=float(row["latitude"]),
                        longitude=float(row["longitude"]),
                        timezone=row["timezone"],
                        elevation=float(row["elevation"] or 0.0),
                        pressure=float(row["pressure"] or 1010.0),
                        temp=float(row["temp"] or 10.0),
                        calculation_method=row.get("calculation_method")
                        or "angle_based",
                        fajr_angle=float(row["fajr_angle"] or 18.0),
                        isha_angle=float(row["isha_angle"] or 17.0),
                        isha_minutes=float(row["isha_minutes"] or 0.0),
                        isha_shafaq=row.get("isha_shafaq") or "general",
                        high_lat_method=int(float(row.get("high_lat_method") or 0)),
                        asr_madhab=int(float(row.get("asr_madhab") or 0)),
                        isha_harag=int(float(row.get("isha_harag") or 0)),
                        fajr_offset=float(row.get("fajr_offset") or 0.0),
                        shurooq_offset=float(row.get("shurooq_offset") or 0.0),
                        dhuhr_offset=float(row.get("dhuhr_offset") or 0.0),
                        asr_offset=float(row.get("asr_offset") or 0.0),
                        maghrib_offset=float(row.get("maghrib_offset") or 0.0),
                        isha_offset=float(row.get("isha_offset") or 0.0),
                        optimized_lat=_parse_optional_float(
                            row.get("optimized_lat", "")
                        ),
                        optimized_lon=_parse_optional_float(
                            row.get("optimized_lon", "")
                        ),
                        high_lat_start_date=_parse_optional_date(
                            row.get("high_lat_start_date", "")
                        ),
                        high_lat_end_date=_parse_optional_date(
                            row.get("high_lat_end_date", "")
                        ),
                        residual_corrections=_parse_optional_str(
                            row.get("residual_corrections", "")
                        ),
                        clock_offsets=_parse_optional_str(row.get("clock_offsets", "")),
                        aqrab_al_bilad=_parse_optional_str(
                            row.get("aqrab_al_bilad", "")
                        ),
                        is_optimized=_parse_optional_bool(row.get("is_optimized", "")),
                        is_official=_parse_optional_bool(row.get("is_official", "")),
                    )
                )
        return records

    def get_by_name(self, name: str) -> LocationRecord:
        for record in self.load_all():
            if record.name == name:
                return record
        raise ValueError(f"Location not found: {name}")
