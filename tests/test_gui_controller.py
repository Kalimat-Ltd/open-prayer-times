import datetime
import json
from pathlib import Path
import sys
import unittest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.app.application.use_cases import calculate_city_day_from_loc_csv
from src.app.presentation.gui_controller import PrayerGuiController


class TestGuiController(unittest.TestCase):
    def setUp(self):
        self.controller = PrayerGuiController(REPO_ROOT / "loc.csv")
        fixture_path = REPO_ROOT / "tests" / "fixtures" / "parity_matrix.json"
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        self.reference_file = REPO_ROOT / fixture["reference_file"]

    def test_calculate_city_day_matches_use_case(self):
        city_name = "Afghanistan, Kabul"
        target_date = datetime.date(2025, 1, 15)

        via_controller = self.controller.calculate_city_day(city_name, target_date)
        direct = calculate_city_day_from_loc_csv(
            city_name, target_date, REPO_ROOT / "loc.csv"
        )

        self.assertEqual(via_controller.times, direct.times)
        self.assertEqual(via_controller.method_used, direct.method_used)
        self.assertEqual(via_controller.error, direct.error)

    def test_estimate_city_angles_returns_result(self):
        estimate = self.controller.estimate_city_angles(
            city_name="Russia, Kazan",
            reference_file=self.reference_file,
            timezone_offset_hours=3,
        )
        self.assertIsNotNone(estimate)
        if estimate is not None:
            self.assertEqual(len(estimate), 2)


if __name__ == "__main__":
    unittest.main()
