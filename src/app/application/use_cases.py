import datetime
from pathlib import Path

from src.app.domain.models import PrayerCalculationRequest
from src.app.domain.prayer_times import calculate_prayer_times
from src.app.infrastructure.location_repository import CsvLocationRepository


def calculate_city_day_from_loc_csv(
    city_name: str,
    target_date: datetime.date,
    loc_csv_path: Path,
):
    repository = CsvLocationRepository(loc_csv_path)
    city = repository.get_by_name(city_name)

    request = PrayerCalculationRequest(
        lat_dec=city.effective_lat,
        lon_dec=city.effective_lon,
        elevation=city.elevation,
        pressure=city.pressure,
        temp=city.temp,
        tz_name=city.timezone,
        tz_offset_hours=0,
        fajr_angle=city.fajr_angle,
        isha_angle=city.isha_angle,
        isha_minutes=city.isha_minutes,
        target_date=target_date,
        asr_madhab=city.asr_madhab,
        fajr_offset=city.fajr_offset,
        shurooq_offset=city.shurooq_offset,
        dhuhr_offset=city.dhuhr_offset,
        asr_offset=city.asr_offset,
        maghrib_offset=city.maghrib_offset,
        isha_offset=city.isha_offset,
        high_lat_method=city.high_lat_method,
        calculation_method=city.calculation_method,
        isha_shafaq=city.isha_shafaq,
        isha_harag=city.isha_harag,
        high_lat_start_date=city.high_lat_start_date,
        high_lat_end_date=city.high_lat_end_date,
    )

    return calculate_prayer_times(request)
