from types import SimpleNamespace

from src.app.infrastructure.optimizer.batch_gui import (
    _apply_optimization_result_to_location,
    _is_stage1_output,
)


def _make_location_row():
    return {
        "name": "Test City",
        "country_code": "DE",
        "fajr_angle": 17.0,
        "isha_angle": 18.0,
        "pressure": 1010.0,
        "temp": 10.0,
        "elevation": 0.0,
        "optimized_lat": None,
        "optimized_lon": None,
        "fajr_offset": None,
        "shurooq_offset": None,
        "dhuhr_offset": None,
        "asr_offset": None,
        "maghrib_offset": None,
        "isha_offset": None,
        "asr_madhab": 0,
        "asr_madhab_overrides": "",
        "calculation_method": "angle_based",
        "isha_shafaq": "general",
        "high_lat_method": 0,
        "high_lat_start_date": None,
        "high_lat_end_date": None,
        "isha_harag": 0,
        "residual_corrections": "",
        "clock_offsets": "",
        "future_field": "old",
        "is_optimized": 0,
    }


def _make_opt_result(**extra):
    payload = {
        "fajr_angle": 14.8,
        "isha_angle": 13.7,
        "latitude": 53.01,
        "longitude": 9.03,
        "temp": 12.5,
        "pressure": 1007.0,
        "elevation": 15.0,
        "offsets": {
            "fajr_offset": -1.0,
            "shurooq_offset": 1.0,
            "dhuhr_offset": 0.0,
            "asr_offset": 2.0,
            "maghrib_offset": 0.0,
            "isha_offset": -2.0,
        },
        "asr_madhab": 1,
        "asr_madhab_overrides": '[{"start":"03-29","end":"10-24","asr_madhab":0}]',
        "calculation_method": "moonsighting",
        "isha_shafaq": "ahmer",
        "high_lat_method": 2,
        "high_lat_start_date": "05-01",
        "high_lat_end_date": "08-10",
        "isha_harag": 2,
        "residual_corrections": '{"fitted":true}',
        "clock_offsets": '[{"start":"03-31","end":"10-27","offset":60}]',
        "future_field": "new-value",
        "rmse_total": 3.0,
        "mae_total": 2.0,
    }
    payload.update(extra)
    return SimpleNamespace(**payload)


def test_apply_optimization_result_auto_applies_matching_fields_and_offsets():
    loc = _make_location_row()
    opt = _make_opt_result()

    _apply_optimization_result_to_location(
        loc,
        opt,
        stage1_only=False,
        apply_coordinates=True,
    )

    assert loc["fajr_angle"] == 14.8
    assert loc["isha_angle"] == 13.7
    assert loc["pressure"] == 1007.0
    assert loc["temp"] == 12.5
    assert loc["elevation"] == 15.0
    assert loc["optimized_lat"] == 53.01
    assert loc["optimized_lon"] == 9.03

    assert loc["fajr_offset"] == -1.0
    assert loc["asr_offset"] == 2.0
    assert loc["isha_offset"] == -2.0

    assert loc["residual_corrections"] == '{"fitted":true}'
    assert loc["clock_offsets"] == '[{"start":"03-31","end":"10-27","offset":60}]'
    assert (
        loc["asr_madhab_overrides"]
        == '[{"start":"03-29","end":"10-24","asr_madhab":0}]'
    )
    assert loc["future_field"] == "new-value"
    assert loc["is_optimized"] == 1


def test_apply_optimization_result_stage1_only_resets_and_skips_offsets():
    loc = _make_location_row()
    loc["fajr_offset"] = 7.0
    loc["residual_corrections"] = "existing-model"
    opt = _make_opt_result(
        fajr_angle=16.2,
        isha_angle=17.1,
        offsets={"fajr_offset": -4.0},
        residual_corrections=None,
        clock_offsets=None,
        asr_madhab_overrides=None,
    )

    _apply_optimization_result_to_location(
        loc,
        opt,
        stage1_only=True,
        apply_coordinates=False,
    )

    assert loc["fajr_angle"] == 16.2
    assert loc["isha_angle"] == 17.1
    assert loc["optimized_lat"] is None
    assert loc["optimized_lon"] is None
    assert loc["fajr_offset"] is None
    assert loc["residual_corrections"] == ""
    assert loc["clock_offsets"] == ""
    assert loc["asr_madhab_overrides"] == ""
    assert loc["is_optimized"] == 1


def test_stage1_output_classifier_does_not_flag_full_multistage_result():
    opt = _make_opt_result(
        convergence_info=(
            "multistage-stage1 loss=249.843; core_months=242; "
            "flagged_months=[4, 5, 6]; stage2=ok; "
            "stage3_offsets=True; stage3_residuals=False"
        )
    )

    assert _is_stage1_output(opt) is False


def test_stage1_output_classifier_flags_legacy_stage1_only_marker():
    opt = _make_opt_result(convergence_info="multistage-stage1 only")

    assert _is_stage1_output(opt) is True


def test_apply_optimization_result_clears_stale_residual_and_clock_when_none():
    loc = _make_location_row()
    loc["residual_corrections"] = "stale-model"
    loc["clock_offsets"] = "stale-clock"

    opt = _make_opt_result(
        residual_corrections=None,
        clock_offsets=None,
        asr_madhab_overrides=None,
    )

    _apply_optimization_result_to_location(
        loc,
        opt,
        stage1_only=False,
        apply_coordinates=True,
    )

    assert loc["residual_corrections"] == ""
    assert loc["clock_offsets"] == ""
    assert loc["asr_madhab_overrides"] == ""
