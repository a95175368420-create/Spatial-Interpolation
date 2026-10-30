from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from spatial_monitor.config import load_config
from spatial_monitor.io import conversion_factor, load_dataset


@pytest.fixture
def bundle(tmp_path):
    def make(analyses, *, samples=None, points=None, geology=None):
        points = points if points is not None else [dict(point_id="P01", x=250000, y=2650000, crs="EPSG:3826", surface_elevation=10, site="A")]
        samples = samples if samples is not None else [dict(sample_id="S01", point_id="P01", date="2026-01-01", medium="groundwater", depth_top=0, depth_bottom=1, batch="A", site="A")]
        pd.DataFrame(points).to_csv(tmp_path / "points.csv", index=False)
        pd.DataFrame(samples).to_csv(tmp_path / "samples.csv", index=False)
        pd.DataFrame(analyses).to_csv(tmp_path / "analyses.csv", index=False)
        if geology is not None:
            pd.DataFrame(geology).to_csv(tmp_path / "geology.csv", index=False)
        return tmp_path
    return make


def reading(parameter, value, unit, **metadata):
    return dict(sample_id="S01", parameter=parameter, value=value, unit=unit, **metadata)


def test_compatible_conversions_and_separate_do(bundle):
    data = load_dataset(bundle([reading("EC", .5, "mS/cm"), reading("DO", 8, "mg/L"), reading("DO", 90, "%"), reading("ORP", -180, "mV"), reading("pH", 7.2, "1")]))
    rows = data.observations.set_index("parameter")
    assert rows.loc["EC", "value"] == 500
    assert rows.loc["EC", "unit"] == "uS/cm"
    assert rows.loc["DO_SAT", "unit"] == "%"
    assert rows.loc["DO", "value"] == 8
    assert rows.loc["ORP", "value"] == -180
    assert rows.loc["pH", "value"] == 7.2
    assert rows.valid.all()


def test_censored_invalid_and_missing_links_remain_visible(bundle):
    path = bundle([reading("TOC", "<0.1", "mg/L"), reading("EC", "oops", "uS/cm"),
                   reading("ORP", "ND", "mV"), dict(sample_id="unknown", parameter="pH", value=7, unit="1")])
    data = load_dataset(path)
    assert len(data.observations) == 4
    assert not data.observations.valid.any()
    assert data.observations.value.isna().all()
    assert data.observations.loc[0, "detection_limit"] == .1
    assert data.observations.loc[0, "raw_value"] == "<0.1"
    assert {"censored_value", "invalid_value", "unresolved_sample"} <= set(data.issues.code)


def test_unsupported_unit_does_not_silently_convert(bundle):
    data = load_dataset(bundle([reading("TOC", 5, "ppm")]))
    assert not data.observations.valid.any()
    assert "incompatible_unit" in data.issues.code.tolist()
    with pytest.raises(ValueError):
        conversion_factor("mg/kg", "mg/L", "soil")


def test_duplicate_samples_cannot_multiply_measurements(bundle):
    sample = dict(sample_id="S01", point_id="P01", date="2026-01-01", medium="water", depth_top=0, depth_bottom=1)
    data = load_dataset(bundle([reading("pH", 7, "1")], samples=[sample, sample]))
    assert len(data.observations) == 1
    assert not data.observations.valid.any()
    assert "unresolved_sample" in data.issues.code.tolist()


def test_duplicate_analyses_are_not_averaged(bundle):
    data = load_dataset(bundle([reading("pH", 7, "1"), reading("pH", 8, "1")]))
    assert data.observations.value.tolist() == [7, 8]
    assert not data.observations.valid.any()
    assert "duplicate_measurement" in data.issues.code.tolist()


def test_toc_medium_and_basis_preserved(bundle):
    samples = [dict(sample_id="S01", point_id="P01", date="2026-01-01", medium="soil", depth_top=0, depth_bottom=1),
               dict(sample_id="S02", point_id="P01", date="2026-01-01", medium="water", depth_top=0, depth_bottom=1)]
    data = load_dataset(bundle([reading("TOC", 1, "%", basis="dry"),
                               dict(sample_id="S02", parameter="TOC", value=1, unit="mg/L")], samples=samples))
    assert data.observations.value.tolist() == [10000, 1]
    assert data.observations.unit.tolist() == ["mg/kg", "mg/L"]
    assert data.observations.valid.all()


def test_geology_overlap_and_gaps_retained(bundle):
    geology = [dict(point_id="P01", depth_top=0, depth_bottom=2, lithology="sand"),
               dict(point_id="P01", depth_top=1, depth_bottom=3, lithology="clay"),
               dict(point_id="P01", depth_top=4, depth_bottom=5, lithology="gravel")]
    data = load_dataset(bundle([reading("pH", 7, "1")], geology=geology))
    assert len(data.geology) == 3
    assert data.geology.valid.tolist() == [False, False, True]
    assert {"overlapping_interval", "geology_gap"} <= set(data.issues.code)
    assert data.geology.surface_elevation.eq(10).all()


def test_configuration_rejects_ph_or_orp_log():
    for parameter in ["pH", "ORP"]:
        with pytest.raises(ValueError, match="logarithmic"):
            load_config({"parameters": {parameter: {"transform": "log10"}}})


def test_category_requires_geology_table():
    with pytest.raises(ValueError, match="categorical"):
        load_config({"parameters": {"rock": {"unit": "1", "data_type": "categorical"}}})


def test_ec_comparison_temperature_uses_explicit_reference(bundle):
    data = load_dataset(bundle([reading("EC", 1, "mS/cm", temperature=20, temperature_compensated="yes", reference_temperature=25)]))
    row = data.observations.iloc[0]
    assert row.temperature == 20
    assert row.reference_temperature == 25
    assert row.comparison_temperature == 25
    data = load_dataset(bundle([reading("EC", 1, "mS/cm", temperature=20, temperature_compensated="no")]))
    assert data.observations.comparison_temperature.iloc[0] == 20
    data = load_dataset(bundle([reading("EC", 1, "mS/cm", temperature=20, temperature_compensated="yes")]))
    assert pd.isna(data.observations.comparison_temperature.iloc[0])
    assert "missing_comparison_temperature" in data.issues.code.tolist()


def test_missing_required_column_invalidates_rows(bundle):
    path = bundle([reading("pH", 7, "1")])
    table = pd.read_csv(path / "analyses.csv").drop(columns="unit")
    table.to_csv(path / "analyses.csv", index=False)
    data = load_dataset(path)
    assert not data.observations.valid.any()
    assert "missing_column" in data.issues.code.tolist()


def test_malformed_configuration_has_actionable_error(tmp_path):
    config = tmp_path / "bad.yaml"
    config.write_text("parameters: [broken", encoding="utf-8")
    with pytest.raises(ValueError, match="YAML"):
        load_config(config)
    with pytest.raises(ValueError, match="finite"):
        load_config({"interpolation": {"grid_spacing": float("nan")}})


def test_csv_mapping_cannot_escape_selected_directory(bundle):
    path = bundle([reading("pH", 7, "1")])
    with pytest.raises(ValueError, match="inside"):
        load_dataset(path, {"tables": {"samples": {"filename": "../samples.csv"}}})


def test_excel_mapping_matches_csv(bundle, tmp_path):
    path = bundle([reading("pH", 7, "1")])
    target = tmp_path / "monitoring.xlsx"
    with pd.ExcelWriter(target) as writer:
        for name in ("samples", "analyses", "points"):
            table = pd.read_csv(path / f"{name}.csv")
            if name == "samples":
                table = table.rename(columns={"sample_id": "樣品編號"})
            table.to_excel(writer, sheet_name=name, index=False)
    data = load_dataset(target, {"tables": {"samples": {"columns": {"樣品編號": "sample_id"}}}})
    assert data.observations.valid.all()
    assert data.observations.source_file.iloc[0] == "monitoring.xlsx:analyses"


def test_custom_chinese_sheets_are_not_mistaken_for_native_v34(bundle, tmp_path):
    path = bundle([reading('pH', 7, '1')])
    target = tmp_path / 'custom.xlsx'
    names = dict(samples='01_樣品登錄', analyses='02_分析資料庫', points='03_點位主檔')
    with pd.ExcelWriter(target) as writer:
        for name, sheet in names.items():
            pd.read_csv(path / f'{name}.csv').to_excel(writer, sheet_name=sheet, index=False)
    data = load_dataset(target, {'tables': {k: {'sheet': v} for k, v in names.items()}})
    assert data.observations.valid.all()
    assert not data.metadata


def test_demo_is_valid_and_reproducible():
    root = Path(__file__).parents[1]
    config = load_config(root / "examples/config.yaml")
    first = load_dataset(root / "examples/demo", config)
    second = load_dataset(root / "examples/demo", config)
    assert first.observations.valid.all()
    assert first.geology.valid.all()
    assert len(first.observations) == 288
    assert first.issues.query("severity == 'error'").empty
    pd.testing.assert_frame_equal(first.observations, second.observations)


def test_bad_dates_depths_and_crs_invalidate_rows(bundle):
    samples = [dict(sample_id="S01", point_id="P01", date="nonsense", medium="water", depth_top=2, depth_bottom=1)]
    points = [dict(point_id="P01", x=250000, y=2650000, crs="")]
    data = load_dataset(bundle([reading("pH", 7, "1")], samples=samples, points=points))
    assert not data.observations.valid.any()
    assert {"invalid_date", "invalid_interval", "missing_crs"} <= set(data.issues.code)
