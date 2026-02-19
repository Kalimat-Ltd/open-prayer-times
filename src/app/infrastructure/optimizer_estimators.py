from typing import Dict, Optional, Tuple

import numpy as np
from scipy.optimize import minimize

from src.app.domain.constants import PRAYER_NAMES
from src.app.infrastructure.optimizer_objective import compute_rmse_objective


def quick_estimate_angles_native(
    reference_times: Dict,
    available_dates,
    lat: float,
    lon: float,
    elevation: float,
    timezone: float,
    tz_name: str,
    isha_minutes: float = 0,
    extra_calc_kwargs=None,
) -> Optional[Tuple[float, float]]:
    if not reference_times or not available_dates:
        return None

    max_dates = 12
    sorted_dates = sorted(available_dates)
    if len(sorted_dates) > max_dates:
        step = len(sorted_dates) / max_dates
        estimation_dates = [sorted_dates[int(i * step)] for i in range(max_dates)]
    else:
        estimation_dates = sorted_dates

    prayer_weights = {name: 1.0 for name in PRAYER_NAMES}
    prayer_weights["fajr"] = 3.0
    prayer_weights["isha"] = 3.0

    def angle_objective(angles):
        fajr_a, isha_a = angles
        params = np.array([fajr_a, isha_a, lat, lon, 10.0, 1010.0], dtype=float)
        return compute_rmse_objective(
            params_vector=params,
            available_dates=estimation_dates,
            reference_times=reference_times,
            elevation=elevation,
            timezone=timezone,
            tz_name=tz_name,
            isha_minutes=isha_minutes,
            prayer_weights=prayer_weights,
            fixed_offsets=None,
            extra_calc_kwargs=extra_calc_kwargs or {},
        )

    try:
        result = minimize(
            angle_objective,
            x0=[18.0, 17.0],
            method="L-BFGS-B",
            bounds=[(10.0, 22.0), (10.0, 22.0)],
            options={"maxiter": 500, "ftol": 1e-8},
        )
        fajr_result = max(10.0, min(22.0, result.x[0]))
        isha_result = max(10.0, min(22.0, result.x[1]))
        return (round(fajr_result, 2), round(isha_result, 2))
    except (ValueError, TypeError, KeyError, RuntimeError, OSError):
        return None
