from dataclasses import dataclass, field
import datetime
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Pipeline context – mutable state threaded through Stage 1 → 2 → 3
# ---------------------------------------------------------------------------


@dataclass
class PipelineContext:
    """Mutable context object threaded through every optimisation stage.

    Stage 1 creates it with core astronomical results.
    Stage 2 updates high-latitude fields.
    Stage 3 updates correction fields (offsets / residuals).
    """

    # ── Core astronomical parameters (set by Stage 1) ─────────────────────
    lat: float = 0.0
    lon: float = 0.0
    elevation: float = 0.0
    pressure: float = 1010.0
    temp: float = 10.0
    fajr_angle: float = 18.0
    isha_angle: float = 17.0
    calculation_method: str = "angle_based"  # "angle_based" | "moonsighting"
    asr_madhab: int = 0  # 0 = standard, 1 = hanafi
    isha_shafaq: Optional[str] = None  # "general" | "ahmer" | "abyad"

    # ── High-latitude parameters (set by Stage 2) ────────────────────────
    high_lat_method: int = 0
    high_lat_start_date: Optional[datetime.date] = None
    high_lat_end_date: Optional[datetime.date] = None
    isha_harag: int = 0
    custom_fajr_angle: Optional[float] = None
    custom_isha_angle: Optional[float] = None
    high_lat_fallback_method: Optional[int] = None

    # ── Correction parameters (Stage 1 + refined by Stage 3) ─────────────
    offsets: Dict[str, float] = field(default_factory=dict)
    clock_offsets: Optional[str] = None  # JSON string
    residual_corrections: Optional[str] = None  # JSON string
    offsets_accepted: bool = False
    residuals_accepted: bool = False
    clock_blocks_count: int = 0

    # ── Date management ──────────────────────────────────────────────────
    excluded_date_ranges: List[Dict[str, Any]] = field(default_factory=list)
    dates_used_for_core: List[datetime.date] = field(default_factory=list)
    artifact_ignored_dates: List[str] = field(default_factory=list)
    residual_active_dates: List[datetime.date] = field(default_factory=list)

    # ── Reference data ───────────────────────────────────────────────────
    reference_times_for_corrections: Optional[Dict] = None
    reference_times_for_evaluation: Optional[Dict] = None

    # ── MAE tracking (flows between stages) ──────────────────────────────
    stable_mae_before_offsets: float = float("inf")
    stable_mae_after_offsets: float = float("inf")

    # ── Helpers ───────────────────────────────────────────────────────────

    def to_extra_calc_kwargs(self) -> Dict[str, Any]:
        """Build the *extra_calc_kwargs* dict consumed by the calculator."""
        kwargs: Dict[str, Any] = {
            "calculation_method": self.calculation_method,
            "asr_madhab": self.asr_madhab,
            "high_lat_method": self.high_lat_method,
            "isha_harag": self.isha_harag,
        }
        if self.isha_shafaq is not None:
            kwargs["isha_shafaq"] = self.isha_shafaq
        if self.high_lat_start_date is not None:
            kwargs["high_lat_start_date"] = self.high_lat_start_date
        if self.high_lat_end_date is not None:
            kwargs["high_lat_end_date"] = self.high_lat_end_date
        if self.custom_fajr_angle is not None:
            kwargs["custom_fajr_angle"] = self.custom_fajr_angle
        if self.custom_isha_angle is not None:
            kwargs["custom_isha_angle"] = self.custom_isha_angle
        if self.high_lat_fallback_method is not None:
            kwargs["high_lat_fallback_method"] = self.high_lat_fallback_method
        return kwargs


# ---------------------------------------------------------------------------
# Per-stage diagnostics – lightweight, immutable result objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Stage1Diagnostics:
    """Diagnostic metrics produced by Stage 1."""

    loss: float = float("inf")
    geographic_calibration: Optional[Dict[str, Any]] = None
    asr_madhab_detection: Optional[Dict[str, Any]] = None
    method_comparison: Optional[Dict[str, float]] = None
    step_timings: Dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class Stage2Diagnostics:
    """Diagnostic metrics produced by Stage 2."""

    ran: bool = False
    accepted: bool = False
    reason: str = ""
    problematic_dates_count: int = 0
    safe_dates_count: int = 0
    problematic_mae_before: float = float("inf")
    problematic_mae_after: float = float("inf")
    step_timings: Dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class Stage3Diagnostics:
    """Diagnostic metrics produced by Stage 3."""

    stable_dates_count: int = 0
    unstable_dates_count: int = 0
    all_dates_mae_before_offsets: float = float("inf")
    all_dates_mae_after_offsets: float = float("inf")
    stable_mae_before_residual: float = float("inf")
    stable_mae_after_residual: float = float("inf")
    unstable_mae_before_residual: float = float("inf")
    unstable_mae_after_residual: float = float("inf")
    step_timings: Dict[str, float] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Calculator request / result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PrayerCalculationRequest:
    lat_dec: float
    lon_dec: float
    elevation: float
    pressure: float
    temp: float
    tz_name: str
    tz_offset_hours: float
    fajr_angle: float
    isha_angle: float
    isha_minutes: float
    target_date: datetime.date
    asr_madhab: int = 0
    fajr_offset: float = 0.0
    shurooq_offset: float = 0.0
    dhuhr_offset: float = 0.0
    asr_offset: float = 0.0
    maghrib_offset: float = 0.0
    isha_offset: float = 0.0
    high_lat_method: int = 0
    skip_fallback: bool = False
    calculation_method: str = "angle_based"
    isha_shafaq: str = "general"
    high_lat_start_date: Optional[datetime.date] = None
    high_lat_end_date: Optional[datetime.date] = None
    custom_fajr_angle: Optional[float] = None
    custom_isha_angle: Optional[float] = None
    high_lat_fallback_method: Optional[int] = None
    isha_harag: int = 0
    rounding: str = "nearest"

    def to_calculator_kwargs(self) -> dict:
        return {
            "lat_dec": self.lat_dec,
            "lon_dec": self.lon_dec,
            "elevation": self.elevation,
            "pressure": self.pressure,
            "temp": self.temp,
            "tz_name": self.tz_name,
            "tz_offset_hours": self.tz_offset_hours,
            "fajr_angle": self.fajr_angle,
            "isha_angle": self.isha_angle,
            "isha_minutes": self.isha_minutes,
            "target_date": self.target_date,
            "asr_madhab": self.asr_madhab,
            "fajr_offset": self.fajr_offset,
            "shurooq_offset": self.shurooq_offset,
            "dhuhr_offset": self.dhuhr_offset,
            "asr_offset": self.asr_offset,
            "maghrib_offset": self.maghrib_offset,
            "isha_offset": self.isha_offset,
            "high_lat_method": self.high_lat_method,
            "skip_fallback": self.skip_fallback,
            "calculation_method": self.calculation_method,
            "isha_shafaq": self.isha_shafaq,
            "high_lat_start_date": self.high_lat_start_date,
            "high_lat_end_date": self.high_lat_end_date,
            "custom_fajr_angle": self.custom_fajr_angle,
            "custom_isha_angle": self.custom_isha_angle,
            "high_lat_fallback_method": self.high_lat_fallback_method,
            "isha_harag": self.isha_harag,
            "rounding": self.rounding,
        }


@dataclass(frozen=True)
class PrayerCalculationResult:
    times: Dict[str, str]
    method_used: Dict[str, int]
    error: Optional[str]
