from pathlib import Path
import datetime
import sys
import unittest

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.app.infrastructure.optimizer.multistage.pipeline import (
    run_multistage_optimization,
)
from src.app.infrastructure.optimizer.multistage.shared import (
    Stage1Config,
    Stage2Config,
)
from src.app.infrastructure.optimizer.multistage.stage1 import (
    optimize_pure_astronomical_core,
)
from src.app.infrastructure.optimizer.multistage.stage2 import (
    optimize_high_latitude_parameters,
)
from src.app.infrastructure.reference_repository import load_reference_times


class TestMultistageStage2HighLat(unittest.TestCase):
    def test_stage2_runs_on_stage1_excluded_ranges(self):
        reference_file = REPO_ROOT / "reference" / "FR" / "france_paris.txt"
        reference_times, available_dates = load_reference_times(reference_file)

        location_data = {
            "name": "France, Paris",
            "latitude": 48.8566,
            "longitude": 2.3522,
            "elevation": 35.0,
            "timezone": "Europe/Paris",
            "temp": 10.0,
            "pressure": 1010.0,
            "fajr_angle": 14.45,
            "isha_angle": 13.35,
            "isha_minutes": 0.0,
            "asr_madhab": 0,
            "calculation_method": "angle_based",
            "high_lat_method": 0,
            "isha_harag": 0,
            "isha_shafaq": "general",
        }

        context, _s1_diag = optimize_pure_astronomical_core(
            location_data=location_data,
            reference_times=reference_times,
            available_dates=available_dates,
            tz_name="Europe/Paris",
            config=Stage1Config(),
        )

        self.assertTrue(context.excluded_date_ranges)

        # Initialise high-lat context from location_data (pipeline does this)
        context.high_lat_method = int(location_data.get("high_lat_method", 0) or 0)
        context.isha_harag = int(location_data.get("isha_harag", 0) or 0)

        s2_diag = optimize_high_latitude_parameters(
            context=context,
            location_data=location_data,
            reference_times=reference_times,
            available_dates=available_dates,
            tz_name="Europe/Paris",
            config=Stage2Config(),
        )

        self.assertTrue(s2_diag.ran)
        self.assertGreaterEqual(s2_diag.problematic_dates_count, 20)
        self.assertGreaterEqual(s2_diag.safe_dates_count, 20)
        self.assertIn(context.high_lat_method, (0, 1, 2, 3))
        self.assertIn(context.isha_harag, (0, 1, 2, 3))
        self.assertIsInstance(context.high_lat_start_date, datetime.date)
        self.assertIsInstance(context.high_lat_end_date, datetime.date)
        self.assertLessEqual(
            context.high_lat_start_date,
            context.high_lat_end_date,
        )
        self.assertTrue(np.isfinite(s2_diag.problematic_mae_before))
        self.assertTrue(np.isfinite(s2_diag.problematic_mae_after))

    def test_pipeline_applies_stage2_dates(self):
        reference_file = REPO_ROOT / "reference" / "FR" / "france_paris.txt"
        reference_times, available_dates = load_reference_times(reference_file)

        location_data = {
            "name": "France, Paris",
            "latitude": 48.8566,
            "longitude": 2.3522,
            "elevation": 35.0,
            "timezone": "Europe/Paris",
            "temp": 10.0,
            "pressure": 1010.0,
            "fajr_angle": 14.45,
            "isha_angle": 13.35,
            "isha_minutes": 0.0,
            "asr_madhab": 0,
            "calculation_method": "angle_based",
            "high_lat_method": 0,
            "isha_harag": 0,
            "high_lat_start_date": None,
            "high_lat_end_date": None,
            "isha_shafaq": "general",
        }

        result = run_multistage_optimization(
            location_data=location_data,
            reference_times=reference_times,
            available_dates=available_dates,
            tz_name="Europe/Paris",
            stage1_config=Stage1Config(),
            stage2_config=Stage2Config(require_mae_improvement=False),
        )

        self.assertIsNotNone(result.high_lat_start_date)
        self.assertIsNotNone(result.high_lat_end_date)
        self.assertIn(int(result.high_lat_method or 0), (0, 1, 2, 3))
        self.assertIn(int(result.isha_harag or 0), (0, 1, 2, 3))
        self.assertIn("stage2", (result.phase_timings or {}))


if __name__ == "__main__":
    unittest.main()
