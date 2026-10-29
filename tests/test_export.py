import json
from pathlib import Path

import pandas as pd

from spatial_monitor.config import load_config
from spatial_monitor.io import load_dataset
from spatial_monitor.export import export_dataset, _default_section, zip_outputs


def test_default_section_includes_measured_points_and_records_orientation():
    points = pd.DataFrame({"point_id": ["A", "B", "C"], "x": [0, 10, 4], "y": [0, 0, 3]})
    result = _default_section(points)
    assert result["start_point"] == "A"
    assert result["end_point"] == "B"
    assert result["tolerance"] >= 3


def test_export_records_sections_and_filters_water_by_event(tmp_path, monkeypatch):
    root = Path(__file__).parents[1]
    config = load_config(root / "examples/config.yaml")
    dataset = load_dataset(root / "examples/demo", config)
    dataset.observations = dataset.observations.loc[
        dataset.observations.parameter.eq("pH")
        & dataset.observations.date.eq(pd.Timestamp("2026-01-15"))
        & dataset.observations.depth_top.eq(0)
    ].copy()
    import spatial_monitor.export as module
    original = module.scene_figure
    water_seen = []

    def scene(*args, **kwargs):
        water_seen.append(kwargs["water_levels"].copy())
        return original(*args, **kwargs)

    monkeypatch.setattr(module, "scene_figure", scene)
    output = tmp_path / "results"
    manifest = export_dataset(dataset, config, output)
    assert manifest["maps"] == 1
    assert manifest["sections"] == 1
    assert water_seen[0].date.eq(pd.Timestamp("2026-01-15")).all()
    assert len(water_seen[0]) == 6
    sections = json.loads((output / manifest["section_reference"]).read_text())
    assert sections[0]["date"] == "2026-01-15T00:00:00"
    assert (output / sections[0]["figure"]).exists()
    assert len(zip_outputs(output)) > 0
