from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.app.domain.models import PipelineContext
from src.app.infrastructure.optimizer.multistage.pipeline import (
    run_multistage_optimization,
)
from src.app.infrastructure.optimizer.multistage.shared import (
    Stage1Config,
    Stage2Config,
    Stage3Config,
)
from src.app.infrastructure.optimizer.multistage.stage1 import (
    optimize_pure_astronomical_core,
)
from src.app.infrastructure.optimizer.multistage.stage2 import (
    optimize_high_latitude_parameters,
)
from src.app.infrastructure.optimizer.multistage.stage3 import (
    optimize_correction_layers,
)
from src.app.infrastructure.residual_model import PrayerResidualModel
from src.app.infrastructure.reference_repository import load_reference_times


def _subsample_dates(dates, *, max_days: int = 180, stride: int = 2):
    sampled = list(dates)[:: max(1, int(stride))]
    if len(sampled) > max_days:
        sampled = sampled[:max_days]
    return sampled


def _fast_stage1_config(min_clean_core_days: int = 60):
    return Stage1Config(
        min_clean_core_days=min_clean_core_days,
        max_refinement_iterations=1,
        clean_day_lookahead_days=1,
        enable_final_mae_angle_polish=False,
        enable_geographic_calibration=False,
        enable_asr_madhab_detection=False,
        geo_search_grid_points=9,
        env_search_grid_points=9,
    )


def _fast_stage2_config():
    return Stage2Config(
        candidate_methods=(0, 1),
        candidate_harag_values=(0,),
        min_problematic_days=10,
        require_mae_improvement=False,
        optimize_custom_angles=False,
    )


class TestMultistageStage3Corrections(unittest.TestCase):
    def test_stage3_uses_stage1_offsets_without_recomputing(self):
        import datetime

        d1 = datetime.date(2026, 1, 1)
        d2 = datetime.date(2026, 1, 2)

        context = PipelineContext(
            fajr_angle=18.0,
            isha_angle=17.0,
            lat=41.33,
            lon=19.82,
            temp=10.0,
            pressure=1010.0,
            elevation=100.0,
            calculation_method="angle_based",
            asr_madhab=0,
            dates_used_for_core=[d1],
            excluded_date_ranges=[],
            offsets={"dhuhr_offset": 5.0, "asr_offset": 5.0},
            offsets_accepted=True,
            stable_mae_before_offsets=1.0,
            stable_mae_after_offsets=0.8,
        )
        location_data = {
            "latitude": 41.33,
            "longitude": 19.82,
            "timezone": "Europe/Tirane",
            "temp": 10.0,
            "pressure": 1010.0,
            "elevation": 100.0,
        }
        reference_times = {
            d1: {"dhuhr": "12:00", "asr": "15:30"},
            d2: {"dhuhr": "12:00", "asr": "15:30"},
        }
        available_dates = [d1, d2]

        with patch(
            "src.app.infrastructure.optimizer.multistage.stage3._evaluate_mae",
            side_effect=[
                (1.00, {"dhuhr": 1.00, "asr": 1.00}),
                (1.20, {"dhuhr": 1.30, "asr": 1.35}),
            ],
        ):
            s3_diag = optimize_correction_layers(
                context=context,
                location_data=location_data,
                reference_times=reference_times,
                available_dates=available_dates,
                tz_name="Europe/Tirane",
                config=Stage3Config(
                    fit_residual_corrections=False,
                ),
            )

        self.assertTrue(context.offsets_accepted)
        self.assertEqual(context.stable_mae_before_offsets, 1.0)
        self.assertEqual(context.stable_mae_after_offsets, 0.8)
        self.assertEqual(s3_diag.all_dates_mae_before_offsets, 1.00)
        self.assertEqual(s3_diag.all_dates_mae_after_offsets, 1.20)

    def test_residual_model_active_ranges_apply_only_within_configured_window(self):
        model = PrayerResidualModel()
        model.prayer_models = {}
        model.fitted = True
        model.set_active_month_day_ranges([("04-01", "09-30")])
        payload = model.to_json()
        loaded = PrayerResidualModel.from_json(payload)

        self.assertTrue(
            loaded.is_active_for_date(__import__("datetime").date(2026, 6, 15))
        )
        self.assertFalse(
            loaded.is_active_for_date(__import__("datetime").date(2026, 12, 15))
        )

    def test_residual_model_without_active_ranges_is_inactive(self):
        model = PrayerResidualModel()
        model.prayer_models = {"fajr": {"coeffs": np.array([0.0]), "harmonics": 0}}
        model.fitted = True

        self.assertFalse(
            model.is_active_for_date(__import__("datetime").date(2026, 6, 15))
        )
        self.assertEqual(
            model.predict_all(__import__("datetime").date(2026, 6, 15))["fajr"], 0.0
        )

    def test_stage3_ordered_layers_run_and_metrics_are_finite(self):
        reference_file = REPO_ROOT / "reference" / "FR" / "france_paris.txt"
        reference_times, available_dates = load_reference_times(reference_file)
        available_dates = _subsample_dates(available_dates, max_days=120, stride=3)

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
            config=_fast_stage1_config(min_clean_core_days=60),
        )

        # Initialise high-lat context (pipeline does this from location_data)
        context.high_lat_method = int(location_data.get("high_lat_method", 0) or 0)
        context.isha_harag = int(location_data.get("isha_harag", 0) or 0)

        _s2_diag = optimize_high_latitude_parameters(
            context=context,
            location_data=location_data,
            reference_times=reference_times,
            available_dates=available_dates,
            tz_name="Europe/Paris",
            config=_fast_stage2_config(),
        )

        s3_diag = optimize_correction_layers(
            context=context,
            location_data=location_data,
            reference_times=reference_times,
            available_dates=available_dates,
            tz_name="Europe/Paris",
            config=Stage3Config(fit_residual_corrections=False),
        )

        self.assertIsNotNone(s3_diag)
        self.assertIsNotNone(context.clock_offsets or context.clock_offsets == "")
        self.assertIsNotNone(context.offsets)
        # residual_corrections may be None when fit_residual_corrections=False

        for val in (
            context.stable_mae_before_offsets,
            context.stable_mae_after_offsets,
            s3_diag.stable_mae_before_residual,
            s3_diag.stable_mae_after_residual,
        ):
            self.assertTrue(np.isfinite(float(val)))

        if s3_diag.unstable_dates_count > 0 and context.residuals_accepted:
            self.assertTrue(np.isfinite(float(s3_diag.unstable_mae_before_residual)))
            self.assertTrue(np.isfinite(float(s3_diag.unstable_mae_after_residual)))

        if context.offsets_accepted:
            self.assertLessEqual(
                float(context.stable_mae_after_offsets),
                float(context.stable_mae_before_offsets) + 1e-9,
            )

        if context.residuals_accepted:
            self.assertLessEqual(
                float(s3_diag.unstable_mae_after_residual),
                float(s3_diag.unstable_mae_before_residual) + 1e-9,
            )

    def test_stage3_pipeline_improves_or_holds_germany_city(self):
        reference_file = REPO_ROOT / "reference" / "DE" / "germany_achim.txt"
        reference_times, available_dates = load_reference_times(reference_file)
        available_dates = _subsample_dates(available_dates, max_days=120, stride=3)

        location_data = {
            "name": "Germany, Achim",
            "latitude": 53.0114,
            "longitude": 9.0386,
            "elevation": 16.0,
            "timezone": "Europe/Berlin",
            "temp": 10.0,
            "pressure": 1010.0,
            "fajr_angle": 18.0,
            "isha_angle": 17.0,
            "isha_minutes": 0.0,
            "asr_madhab": 0,
            "calculation_method": "angle_based",
            "high_lat_method": 0,
            "isha_harag": 0,
            "isha_shafaq": "general",
        }

        baseline = run_multistage_optimization(
            location_data=location_data,
            reference_times=reference_times,
            available_dates=available_dates,
            tz_name="Europe/Berlin",
            stage1_config=_fast_stage1_config(min_clean_core_days=55),
            stage2_config=_fast_stage2_config(),
            stage3_config=Stage3Config(
                fit_residual_corrections=False,
            ),
        )

        improved = run_multistage_optimization(
            location_data=location_data,
            reference_times=reference_times,
            available_dates=available_dates,
            tz_name="Europe/Berlin",
            stage1_config=_fast_stage1_config(min_clean_core_days=55),
            stage2_config=_fast_stage2_config(),
            stage3_config=Stage3Config(),
        )

        self.assertTrue(np.isfinite(float(baseline.mae_total)))
        self.assertTrue(np.isfinite(float(improved.mae_total)))
        self.assertLessEqual(
            float(improved.mae_total), float(baseline.mae_total) + 0.05
        )
        self.assertIn("stage3", (improved.phase_timings or {}))

    def test_aachen_residual_gate_uses_unstable_improvement_only(self):
        reference_file = REPO_ROOT / "reference" / "DE" / "germany_aachen.txt"
        reference_times, available_dates = load_reference_times(reference_file)
        available_dates = _subsample_dates(available_dates, max_days=150, stride=2)

        location_data = {
            "id": "52490",
            "country_code": "DE",
            "name": "Germany, Aachen",
            "latitude": 50.7756,
            "longitude": 6.0836,
            "optimized_lat": 50.68574803051944,
            "optimized_lon": 5.94116158799868,
            "timezone": "Europe/Berlin",
            "temp": 0.0,
            "pressure": 1013.2597644548246,
            "elevation": -21.99923456762282,
            "calculation_method": "angle_based",
            "fajr_angle": 18.1,
            "isha_angle": 15.9,
            "isha_minutes": 0.0,
            "isha_shafaq": "abyad",
            "asr_madhab": 1,
            "high_lat_start_date": "2026-04-25",
            "high_lat_end_date": "2026-08-19",
            "high_lat_method": 0,
            "isha_harag": 0,
            "fajr_offset": 0.0,
            "shurooq_offset": 0.0,
            "dhuhr_offset": 0.0,
            "asr_offset": 0.0,
            "maghrib_offset": 0.0,
            "isha_offset": 0.0,
            "is_optimized": 1,
            "residual_corrections": "",
            "clock_offsets": "",
        }

        context, _s1_diag = optimize_pure_astronomical_core(
            location_data=location_data,
            reference_times=reference_times,
            available_dates=available_dates,
            tz_name="Europe/Berlin",
            config=_fast_stage1_config(min_clean_core_days=60),
        )

        # Initialise high-lat context (pipeline does this from location_data)
        context.high_lat_method = int(location_data.get("high_lat_method", 0) or 0)
        context.isha_harag = int(location_data.get("isha_harag", 0) or 0)

        _s2_diag = optimize_high_latitude_parameters(
            context=context,
            location_data=location_data,
            reference_times=reference_times,
            available_dates=available_dates,
            tz_name="Europe/Berlin",
            config=_fast_stage2_config(),
        )
        s3_diag = optimize_correction_layers(
            context=context,
            location_data=location_data,
            reference_times=reference_times,
            available_dates=available_dates,
            tz_name="Europe/Berlin",
            config=Stage3Config(),
        )

        self.assertGreaterEqual(float(s3_diag.unstable_mae_before_residual), 0.0)
        self.assertLess(
            float(s3_diag.unstable_mae_after_residual),
            float(s3_diag.unstable_mae_before_residual),
        )
        self.assertTrue(context.residuals_accepted)


if __name__ == "__main__":
    unittest.main()
