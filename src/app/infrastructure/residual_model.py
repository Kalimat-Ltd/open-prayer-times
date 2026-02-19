"""
Fourier Residual Correction Model for Prayer Times
====================================================

After physics-based optimization (angles, offsets, temp, pressure), systematic
seasonal errors may remain — typically caused by authority-specific rounding
rules, terrain effects, or safety margins that vary by season.

This module fits a Fourier series to those residuals and validates the model
across multiple cities in the same country to prevent overfitting.

The model is only accepted when:
  1. There is sufficient data (≥90 reference dates)
  2. At least 2 cities in the country have reference data
  3. Leave-one-city-out cross-validation shows improvement on held-out cities
  4. No held-out city is made significantly worse (>3% RMSE increase)
  5. Average RMSE improvement across all held-out cities ≥ threshold

The Fourier representation is compact — typically 5–9 coefficients per prayer
— and stores easily as JSON in a single CSV column.

Model:
    correction(day) = a₀ + Σ_{k=1}^{K} [aₖ·cos(2πk·d/T) + bₖ·sin(2πk·d/T)]
    where T = 365.25, K = number of harmonics (selected via BIC)
"""

import json
import math
import datetime
import numpy as np
from typing import Dict, List, Optional, Tuple, Any

PRAYER_NAMES = ["fajr", "shurooq", "dhuhr", "asr", "maghrib", "isha"]
PERIOD = 365.25  # tropical year in days


# =============================================================================
# Core Fourier Algebra
# =============================================================================


def day_of_year_fractional(date_obj: datetime.date) -> float:
    """Convert a date to its zero-indexed day-of-year (0–365)."""
    return float(date_obj.timetuple().tm_yday - 1)


def build_fourier_design_matrix(days: np.ndarray, n_harmonics: int) -> np.ndarray:
    """
    Build the design matrix for Fourier regression.

    Columns: [1, cos(2π·1·d/T), sin(2π·1·d/T), ..., cos(2π·K·d/T), sin(2π·K·d/T)]

    Parameters
    ----------
    days : array of day-of-year values (shape (n,))
    n_harmonics : number of Fourier harmonic pairs (K)

    Returns
    -------
    X : design matrix (n, 1 + 2K)
    """
    n = len(days)
    X = np.ones((n, 1 + 2 * n_harmonics))
    for k in range(1, n_harmonics + 1):
        phase = 2.0 * math.pi * k * days / PERIOD
        X[:, 2 * k - 1] = np.cos(phase)
        X[:, 2 * k] = np.sin(phase)
    return X


def fit_fourier_ridge(
    days: np.ndarray,
    residuals: np.ndarray,
    n_harmonics: int,
    alpha: float = 0.1,
) -> np.ndarray:
    """
    Fit a Fourier model with Ridge (Tikhonov / L2) regularisation.

    Solves  min ‖X β − y‖² + α ‖β‖²   (intercept is NOT regularised).

    Parameters
    ----------
    days : day-of-year values (n,)
    residuals : target residuals in minutes (n,)
    n_harmonics : number of harmonic pairs
    alpha : regularisation strength (≥0)

    Returns
    -------
    beta : coefficients (1 + 2K,)
    """
    X = build_fourier_design_matrix(days, n_harmonics)
    n_params = X.shape[1]
    reg = alpha * np.eye(n_params)
    reg[0, 0] = 0.0  # don't penalise the intercept
    beta = np.linalg.solve(X.T @ X + reg, X.T @ residuals)
    return beta


def predict_fourier(
    days: np.ndarray, coeffs: np.ndarray, n_harmonics: int
) -> np.ndarray:
    """Predict corrections (minutes) for the given day-of-year values."""
    X = build_fourier_design_matrix(days, n_harmonics)
    return X @ coeffs


# =============================================================================
# Model Selection
# =============================================================================


def _select_harmonics_bic(
    days: np.ndarray,
    residuals: np.ndarray,
    max_harmonics: int = 6,
    alpha: float = 0.1,
) -> Tuple[int, np.ndarray]:
    """
    Select the optimal number of harmonics using the Bayesian Information
    Criterion (BIC), which balances fit quality against model complexity.

        BIC = n · ln(RSS / n) + k · ln(n)

    where k = 1 + 2·harmonics (number of free parameters).

    Returns (best_harmonics, best_coefficients).
    """
    n = len(days)
    if n < 10:
        coeffs = fit_fourier_ridge(days, residuals, 1, alpha)
        return 1, coeffs

    best_bic = float("inf")
    best_h = 1
    best_coeffs = None

    for h in range(1, max_harmonics + 1):
        k = 1 + 2 * h
        if k >= n - 1:
            break  # insufficient data for this complexity

        coeffs = fit_fourier_ridge(days, residuals, h, alpha)
        pred = build_fourier_design_matrix(days, h) @ coeffs
        rss = float(np.sum((residuals - pred) ** 2))

        bic = n * np.log(rss / n + 1e-10) + k * np.log(n)

        if bic < best_bic:
            best_bic = bic
            best_h = h
            best_coeffs = coeffs

    if best_coeffs is None:
        best_h = 1
        best_coeffs = fit_fourier_ridge(days, residuals, best_h, alpha)
    return best_h, best_coeffs


# =============================================================================
# PrayerResidualModel  — the serialisable artefact
# =============================================================================


class PrayerResidualModel:
    """
    Compact Fourier-based seasonal residual correction for all six prayers.

    Serialises to / deserialises from a JSON string suitable for storage in a
    single CSV column.
    """

    def __init__(self) -> None:
        self.prayer_models: Dict[str, Dict[str, Any]] = {}
        self.fitted: bool = False
        self.validation_rmse_improvement: float = 0.0
        self.n_cities_validated: int = 0
        self.active_month_day_ranges: List[Tuple[str, str]] = []

    def set_active_month_day_ranges(self, ranges: List[Tuple[str, str]]) -> None:
        cleaned: List[Tuple[str, str]] = []
        for start, end in ranges:
            s = str(start or "").strip()
            e = str(end or "").strip()
            if len(s) == 5 and len(e) == 5:
                cleaned.append((s, e))
        self.active_month_day_ranges = cleaned

    @staticmethod
    def _month_day_in_range(month_day: str, start: str, end: str) -> bool:
        if start <= end:
            return start <= month_day <= end
        return month_day >= start or month_day <= end

    def is_active_for_date(self, date_obj: datetime.date) -> bool:
        if not self.active_month_day_ranges:
            return False
        md = date_obj.strftime("%m-%d")
        for start, end in self.active_month_day_ranges:
            if self._month_day_in_range(md, start, end):
                return True
        return False

    # ---- fitting -----------------------------------------------------------

    def fit_prayer(
        self,
        prayer: str,
        days: np.ndarray,
        residuals: np.ndarray,
        n_harmonics: int,
        alpha: float = 0.1,
    ) -> None:
        """Fit a single prayer's Fourier correction model."""
        coeffs = fit_fourier_ridge(days, residuals, n_harmonics, alpha)
        self.prayer_models[prayer] = {
            "coeffs": coeffs,
            "harmonics": n_harmonics,
        }

    # ---- prediction --------------------------------------------------------

    def predict(self, prayer: str, date_obj: datetime.date) -> float:
        """
        Return the correction in minutes to **add** to the calculated time
        for *prayer* on *date_obj*.  Returns 0.0 if no model exists for this
        prayer.
        """
        if prayer not in self.prayer_models:
            return 0.0
        model = self.prayer_models[prayer]
        day = np.array([day_of_year_fractional(date_obj)])
        return float(predict_fourier(day, model["coeffs"], model["harmonics"])[0])

    def predict_all(self, date_obj: datetime.date) -> Dict[str, float]:
        """Return corrections for every prayer on a given date."""
        if not self.is_active_for_date(date_obj):
            return {p: 0.0 for p in PRAYER_NAMES}
        return {p: self.predict(p, date_obj) for p in PRAYER_NAMES}

    # ---- serialisation -----------------------------------------------------

    def to_json(self) -> str:
        """Serialise to a compact JSON string (safe for CSV embedding)."""
        data: Dict[str, Any] = {}
        for prayer in PRAYER_NAMES:
            model = self.prayer_models.get(prayer)
            if model is not None:
                data[prayer] = {
                    "c": [round(float(v), 6) for v in model["coeffs"]],
                    "h": model["harmonics"],
                }
        data["_m"] = {
            "vi": round(self.validation_rmse_improvement, 2),
            "nc": self.n_cities_validated,
            "ar": [
                [str(start), str(end)] for start, end in self.active_month_day_ranges
            ],
        }
        return json.dumps(data, separators=(",", ":"))

    @classmethod
    def from_json(cls, json_str: str) -> "PrayerResidualModel":
        """Deserialise from a JSON string produced by ``to_json``."""
        model = cls()
        if not json_str or not json_str.strip():
            return model
        try:
            data = json.loads(json_str)
        except (json.JSONDecodeError, TypeError):
            return model
        meta = data.pop("_m", {})
        model.validation_rmse_improvement = meta.get("vi", 0.0)
        model.n_cities_validated = meta.get("nc", 0)
        active_ranges = meta.get("ar", [])
        if isinstance(active_ranges, list):
            parsed_ranges: List[Tuple[str, str]] = []
            for item in active_ranges:
                if not isinstance(item, (list, tuple)) or len(item) != 2:
                    continue
                start = str(item[0]).strip()
                end = str(item[1]).strip()
                if len(start) == 5 and len(end) == 5:
                    parsed_ranges.append((start, end))
            model.active_month_day_ranges = parsed_ranges
        for prayer, pdata in data.items():
            if prayer.startswith("_"):
                continue
            model.prayer_models[prayer] = {
                "coeffs": np.array(pdata["c"], dtype=float),
                "harmonics": pdata["h"],
            }
        model.fitted = bool(model.prayer_models)
        return model


# =============================================================================
# Residual computation
# =============================================================================


def _parse_time_to_seconds(time_str: str) -> Optional[int]:
    """Parse H:M or H:M:S to total seconds from midnight.  Returns None on failure."""
    try:
        parts = time_str.strip().split(":")
        h, m = int(parts[0]), int(parts[1])
        s = int(parts[2]) if len(parts) > 2 else 0
        return h * 3600 + m * 60 + s
    except (ValueError, IndexError):
        return None


def compute_city_residuals(
    optimized_params: np.ndarray,
    offsets: Dict[str, float],
    reference_times: Dict,
    available_dates: List,
    elevation: float,
    timezone_val: float,
    tz_name: Optional[str],
    isha_minutes: float,
    extra_calc_kwargs: Dict,
) -> Dict[str, Tuple[np.ndarray, np.ndarray]]:
    """
    Compute per-prayer, per-date residuals  (reference − calculated)  for a
    single city *after* offsets have been applied.

    Parameters
    ----------
    optimized_params : [fajr_angle, isha_angle, lat, lon, temp, pressure]
    offsets : constant per-prayer offsets (minutes)
    reference_times : {date: {prayer: time_str}}
    available_dates : dates with reference data
    elevation, timezone_val, tz_name, isha_minutes, extra_calc_kwargs :
        forwarded to ``calculate_prayer_times``

    Returns
    -------
    {prayer_name: (days_array, residuals_array)}  — minutes, positive means
    the reference is later than the calculation.
    """
    from src.app.infrastructure.prayer_calculator import calculate_prayer_times

    fajr_angle, isha_angle, lat, lon, temp, pressure = optimized_params

    offset_map = {
        "fajr": float(offsets.get("fajr_offset", 0.0) or 0.0),
        "shurooq": float(offsets.get("shurooq_offset", 0.0) or 0.0),
        "dhuhr": float(offsets.get("dhuhr_offset", 0.0) or 0.0),
        "asr": float(offsets.get("asr_offset", 0.0) or 0.0),
        "maghrib": float(offsets.get("maghrib_offset", 0.0) or 0.0),
        "isha": float(offsets.get("isha_offset", 0.0) or 0.0),
    }

    prayer_days: Dict[str, List[float]] = {p: [] for p in PRAYER_NAMES}
    prayer_resids: Dict[str, List[float]] = {p: [] for p in PRAYER_NAMES}

    for date_obj in sorted(available_dates):
        ref = reference_times.get(date_obj)
        if not ref:
            continue

        calc_result = calculate_prayer_times(
            lat_dec=lat,
            lon_dec=lon,
            elevation=elevation,
            tz_offset_hours=timezone_val,
            fajr_angle=fajr_angle,
            isha_angle=isha_angle,
            temp=temp,
            pressure=pressure,
            target_date=date_obj,
            tz_name=tz_name,
            isha_minutes=isha_minutes,
            rounding="none",
            **extra_calc_kwargs,
        )
        # calculate_prayer_times returns (times_dict, methods_dict, error_msg)
        if not calc_result or len(calc_result) < 3:
            continue
        calc, _, error_msg = calc_result
        if error_msg or not calc:
            continue

        day_frac = day_of_year_fractional(date_obj)

        for prayer in PRAYER_NAMES:
            ref_str = ref.get(prayer)
            calc_str = calc.get(prayer)
            if (
                not ref_str
                or not calc_str
                or ref_str.strip() in ("N/A", "")
                or calc_str.strip() in ("N/A", "")
            ):
                continue

            ref_secs = _parse_time_to_seconds(ref_str)
            calc_secs = _parse_time_to_seconds(calc_str)
            if ref_secs is None or calc_secs is None:
                continue

            # Apply the constant offset that was already optimised
            calc_secs_adj = calc_secs + int(offset_map[prayer] * 60)

            diff_secs = ref_secs - calc_secs_adj
            # Handle midnight wrap-around
            if diff_secs > 43200:
                diff_secs -= 86400
            elif diff_secs < -43200:
                diff_secs += 86400

            residual_min = diff_secs / 60.0

            # Discard outliers (>15 min residual = likely data error)
            if abs(residual_min) < 15.0:
                prayer_days[prayer].append(day_frac)
                prayer_resids[prayer].append(residual_min)

    result = {}
    for prayer in PRAYER_NAMES:
        if prayer_days[prayer]:
            result[prayer] = (
                np.array(prayer_days[prayer]),
                np.array(prayer_resids[prayer]),
            )
    return result
