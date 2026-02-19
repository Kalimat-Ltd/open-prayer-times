import datetime
from pathlib import Path

from src.app.application.use_cases import calculate_city_day_from_loc_csv
from src.app.config import LOC_CSV_PATH


def calculate_city_day(city_name: str, target_date: datetime.date):
    return calculate_city_day_from_loc_csv(
        city_name=city_name,
        target_date=target_date,
        loc_csv_path=Path(LOC_CSV_PATH),
    )
