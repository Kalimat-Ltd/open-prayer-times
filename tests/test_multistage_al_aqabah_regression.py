from pathlib import Path
import sys

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.app.infrastructure.location_repository import CsvLocationRepository
from src.app.infrastructure.optimizer.multistage.pipeline import (
    run_multistage_optimization,
)
from src.app.infrastructure.optimizer.objective import _compute_detailed_errors
from src.app.infrastructure.optimizer.shared import (
    OFFSET_FIELDS,
    _load_residual_model_from_json,
)
from src.app.infrastructure.reference_repository import load_reference_times


def test_al_aqabah_multistage_post_eval_uses_optimized_elevation():
    """
    Regression guard for GUI popup evaluation for:
    26423, JO, "Jordan, Al `Aqabah"

    The post-optimization evaluation must use the optimized elevation from
    multistage output. Using the original city elevation can artificially
    inflate MAE/RMSE (especially sunrise/sunset-dependent prayers).
    """
    repository = CsvLocationRepository(REPO_ROOT / "loc.csv")
    city = repository.get_by_name("Jordan, Al `Aqabah")

    reference_file = REPO_ROOT / "reference" / "JO" / "jordan_al_`aqabah.txt"
    reference_times, available_dates = load_reference_times(reference_file)

    baseline_params = np.array(
        [
            float(city.fajr_angle),
            float(city.isha_angle),
            float(city.effective_lat),
            float(city.effective_lon),
            float(city.temp),
            float(city.pressure),
        ],
        dtype=float,
    )
    baseline_offsets = {
        field: float(getattr(city, field, 0.0) or 0.0) for field in OFFSET_FIELDS
    }

    baseline_rmse, baseline_mae, *_ = _compute_detailed_errors(
        baseline_params,
        available_dates=available_dates,
        reference_times=reference_times,
        elevation=float(city.elevation),
        timezone=city.timezone,
        tz_name=city.timezone,
        isha_minutes=float(city.isha_minutes),
        offsets=baseline_offsets,
        residual_model=_load_residual_model_from_json(city.residual_corrections),
        settings_source=city.__dict__,
        clock_offsets_json=city.clock_offsets or "",
    )

    opt_result = run_multistage_optimization(
        location_data=city.__dict__,
        reference_times=reference_times,
        available_dates=available_dates,
        tz_name=city.timezone,
    )

    after_params = np.array(
        [
            float(opt_result.fajr_angle),
            float(opt_result.isha_angle),
            float(opt_result.latitude),
            float(opt_result.longitude),
            float(opt_result.temp),
            float(opt_result.pressure),
        ],
        dtype=float,
    )

    after_rmse, after_mae, *_ = _compute_detailed_errors(
        after_params,
        available_dates=available_dates,
        reference_times=reference_times,
        elevation=float(opt_result.elevation),
        timezone=city.timezone,
        tz_name=city.timezone,
        isha_minutes=float(city.isha_minutes),
        offsets=dict(opt_result.offsets) if opt_result.offsets else {},
        residual_model=_load_residual_model_from_json(opt_result.residual_corrections),
        settings_source=[city.__dict__, opt_result],
        clock_offsets_json=opt_result.clock_offsets or "",
    )

    # Baseline row is already very accurate.
    assert baseline_mae < 0.5
    assert baseline_rmse < 0.5

    # After fixing popup evaluation to use optimized elevation, there should be
    # no large artificial regression.
    assert after_mae < 0.5
    assert after_rmse < 0.5
    assert abs(after_mae - baseline_mae) < 0.05
