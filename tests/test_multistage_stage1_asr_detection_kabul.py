from pathlib import Path
import sys
import unittest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.app.infrastructure.reference_repository import load_reference_times
from src.app.infrastructure.optimizer.multistage.shared import Stage1Config
from src.app.infrastructure.optimizer.multistage.stage1 import (
    optimize_pure_astronomical_core,
)


class TestMultistageStage1AsrDetectionKabul(unittest.TestCase):
    def test_kabul_phase1_detects_hanafi_asr_madhab(self):
        reference_file = REPO_ROOT / "reference" / "AF" / "afghanistan_kabul.txt"
        reference_times, available_dates = load_reference_times(reference_file)

        location_data = {
            "id": 31,
            "country_code": "AF",
            "name": "Afghanistan, Kabul",
            "latitude": 34.5253,
            "longitude": 69.1783,
            "optimized_lat": 34.5253,
            "optimized_lon": 69.1783,
            "timezone": "Asia/Kabul",
            "elevation": 0.0,
            "pressure": 1084.6,
            "temp": -39.0,
            "calculation_method": "angle_based",
            "fajr_angle": 18.2,
            "isha_angle": 17.5,
            "isha_minutes": 0.0,
            "isha_shafaq": "general",
            "high_lat_method": 0,
            "asr_madhab": 0,
            "isha_harag": 0,
            "fajr_offset": 0.99,
            "shurooq_offset": 1.27,
            "dhuhr_offset": 0.49,
            "asr_offset": 0.29,
            "maghrib_offset": 1.99,
            "isha_offset": 2.23,
        }

        context, diag = optimize_pure_astronomical_core(
            location_data=location_data,
            reference_times=reference_times,
            available_dates=available_dates,
            tz_name="Asia/Kabul",
            config=Stage1Config(),
        )

        self.assertEqual(context.asr_madhab, 1)  # 1 = hanafi
        detection = diag.asr_madhab_detection or {}
        self.assertTrue(detection.get("enabled"))
        self.assertEqual(detection.get("selected"), "hanafi")
        self.assertEqual(detection.get("reason"), "hanafi_reduces_asr_mae")
        self.assertGreaterEqual(
            float(detection.get("standard_mae", 0.0)),
            float(Stage1Config().asr_high_error_threshold_minutes),
        )


if __name__ == "__main__":
    unittest.main()
