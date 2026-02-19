import datetime
from pathlib import Path
import sys
import unittest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.app.application.use_cases import calculate_city_day_from_loc_csv
from src.app.presentation.city_day_service import calculate_city_day


class TestCityDayService(unittest.TestCase):
    def test_wrapper_matches_use_case(self):
        city_name = "Afghanistan, Kabul"
        target_date = datetime.date(2025, 1, 15)

        wrapped = calculate_city_day(city_name, target_date)
        direct = calculate_city_day_from_loc_csv(
            city_name=city_name,
            target_date=target_date,
            loc_csv_path=REPO_ROOT / "loc.csv",
        )

        self.assertEqual(wrapped.times, direct.times)
        self.assertEqual(wrapped.method_used, direct.method_used)
        self.assertEqual(wrapped.error, direct.error)


if __name__ == "__main__":
    unittest.main()
