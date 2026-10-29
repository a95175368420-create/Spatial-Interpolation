import numpy as np
import pandas as pd
import pytest

from spatial_monitor.interpolation import filter_layer, idw_values, interpolate_layer
from spatial_monitor.plotting import (borehole_figure, export_map_png, map_figure,
    profile_figure, project_section, scene_figure, section_figure, trend_figure, _located)


@pytest.fixture
def observations():
    return pd.DataFrame({
        "point_id": ["A", "B", "C", "D"], "sample_id": ["S1", "S2", "S3", "S4"],
        "parameter": "ORP", "value": [-100., -50., 0., 50.], "unit": "mV",
        "valid": True, "medium": "water", "basis": "", "method": "field",
        "date": pd.Timestamp("2026-01-01"), "batch": "one", "site": "site",
        "depth_top": 1., "depth_bottom": 1.5, "x": [0., 10., 0., 10.],
        "y": [0., 0., 10., 10.], "surface_elevation": 100., "crs": "EPSG:3826",
    })


@pytest.fixture
def geology(observations):
    return pd.DataFrame({"point_id": ["A", "A", "B"], "depth_top": [0., 2., 0.],
                         "depth_bottom": [1., 3., 3.], "lithology": ["sand", "clay", "sand"],
                         "stratum": ["upper", "lower", "upper"], "description": ["observed"] * 3, "valid": True})


@pytest.fixture
def config():
    return dict(label="ORP", unit="mV", colorscale="RdBu", transform="none", thresholds=[])


@pytest.fixture
def settings():
    return dict(grid_spacing=1, max_distance=20, min_width=2, power=2, max_cells=100000)


def test_exact_idw_samples_and_signed_values(observations):
    xy = observations[["x", "y"]].to_numpy()
    np.testing.assert_allclose(idw_values(xy, observations.value, xy), observations.value)
    assert idw_values(xy, observations.value, [[5., 5.]])[0] == pytest.approx(-25.)


def test_mask_is_separate_from_idw_search(observations, config, settings):
    settings.update(max_distance=2)
    grid = interpolate_layer(observations, config, settings, bounds=(-1, 11, -1, 11))
    assert grid.available
    assert np.isnan(grid.z[grid.y == 5]).all()
    assert np.isnan(grid.z[grid.x < 0]).all()
    target = (grid.x == 1) & (grid.y == 1)
    expected = idw_values(observations[["x", "y"]].to_numpy(), observations.value, [[1., 1.]])[0]
    assert grid.z[target][0] == pytest.approx(expected)
    assert expected != -100  # Every point participates, including points outside 2 m.


def test_sparse_collinear_and_narrow_are_points_only(observations, config, settings):
    assert "three" in interpolate_layer(observations.iloc[:2], config, settings).reason
    line = observations.copy()
    line["x"] = [0, 1, 2, 3]
    line["y"] = 0
    assert "collinear" in interpolate_layer(line, config, settings).reason
    narrow = observations.copy()
    narrow["y"] *= .01
    assert "minimum width" in interpolate_layer(narrow, config, settings).reason


def test_ambiguous_coordinates_and_dates_not_averaged(observations, config, settings):
    duplicate = pd.concat([observations, observations.iloc[[0]].assign(value=999)], ignore_index=True)
    assert "Repeated XY" in interpolate_layer(duplicate, config, settings).reason
    dated = observations.copy()
    dated.loc[0, "date"] = pd.Timestamp("2026-01-02")
    assert "single date" in interpolate_layer(dated, config, settings).reason


def test_transform_safety_and_geology_rejection(observations, config, settings):
    with pytest.raises(ValueError, match="pH and ORP"):
        interpolate_layer(observations, {**config, "transform": "log10"}, settings)
    ph = observations.assign(parameter="pH", value=7.)
    with pytest.raises(ValueError, match="pH and ORP"):
        interpolate_layer(ph, {"label": "Acidity", "transform": "log10"}, settings)
    with pytest.raises(ValueError, match="Categorical"):
        interpolate_layer(observations, {"data_type": "categorical"}, settings)
    assert "positive" in interpolate_layer(observations.assign(parameter="Zn"), {"transform": "log10"}, settings).reason


def test_metric_crs_and_grid_resource_limit(observations, config, settings):
    assert "metres" in interpolate_layer(observations.assign(crs="EPSG:4326"), config, settings).reason
    assert "max_cells" in interpolate_layer(observations, config, {**settings, "max_cells": 10}).reason


def test_layer_does_not_split_long_samples(observations):
    data = pd.concat([observations, observations.iloc[[0]].assign(depth_bottom=2.)], ignore_index=True)
    assert len(filter_layer(data, 1, 1.5)) == 4
    assert len(filter_layer(data, 1.25, 1.5)) == 0


def test_section_chainage_offset_and_finite_segment():
    points = pd.DataFrame({"point_id": ["A", "B", "C", "D"], "x": [2, 8, 11, 5], "y": [1, -2, 0, 4]})
    result = project_section(points, [0, 0], [10, 0], 2)
    assert list(result.point_id) == ["A", "B"]
    np.testing.assert_allclose(result.chainage, [2, 8])
    np.testing.assert_allclose(result.offset, [1, 2])
    with pytest.raises(ValueError):
        project_section(points, [0, 0], [0, 0], 2)


def test_plot_views_and_static_png(observations, config, settings, tmp_path):
    grid = interpolate_layer(observations, config, settings)
    for fig in (map_figure(observations, config, grid), trend_figure(observations, config), profile_figure(observations, config)):
        assert len(fig.data)
        assert fig.to_json()
    output = export_map_png(observations, config, tmp_path / "map.png", grid)
    assert output.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")


def test_geology_gap_is_explicit(observations, geology):
    points = observations[["point_id", "x", "y", "surface_elevation"]]
    fig = borehole_figure(geology, points)
    unknown = next(trace for trace in fig.data if trace.name == "Unknown")
    assert list(unknown.base) == [98.]
    assert list(unknown.y) == [1.]


def test_section_contacts_require_confirmation(observations, geology):
    points = observations[["point_id", "x", "y", "surface_elevation"]]
    contact = dict(name="interface", point_ids=["A", "B"], depths=[1., 2.], confirmed=False)
    fig = section_figure(geology, points, [0, 0], [10, 0], 1, observations=observations, confirmed_contacts=[contact])
    assert not any("interpretation" in trace.name for trace in fig.data)
    contact["confirmed"] = True
    fig = section_figure(geology, points, [0, 0], [10, 0], 1, confirmed_contacts=[contact])
    interpretation = next(t for t in fig.data if "interpretation" in t.name)
    np.testing.assert_allclose(interpretation.y, [99, 98])


def test_scene_preserves_intervals_and_relative_mode(observations, geology, config):
    points = observations[["point_id", "x", "y", "surface_elevation"]]
    absolute = scene_figure(observations, geology, points, config)
    measured = next(t for t in absolute.data if t.legendgroup == "measurements")
    np.testing.assert_allclose(measured.z, [99., 98.5])
    relative_points = points.copy()
    relative_points.loc[0, "surface_elevation"] = np.nan
    relative_observations = observations.drop(columns="surface_elevation")
    relative = scene_figure(relative_observations, geology, relative_points, config)
    assert "relative depth" in relative.layout.title.text
    for trace in relative.data:
        if trace.legendgroup == "measurements":
            np.testing.assert_allclose(trace.z, [-1., -1.5])


def test_scene_stack_is_labeled_and_water_level_located(observations, geology, config, settings):
    points = observations[["point_id", "x", "y", "surface_elevation"]]
    grid = interpolate_layer(observations, config, settings)
    water = pd.DataFrame({"point_id": ["A"], "date": ["2026-01-01"], "depth": [2.]})
    fig = scene_figure(observations, geology, points, config, grids=[{"grid": grid, "depth": 1.25}], water_levels=water)
    surface = next(t for t in fig.data if t.type == "surface")
    assert "not 3D interpolation" in surface.name
    assert np.nanmean(surface.z) == pytest.approx(98.75)
    level = next(t for t in fig.data if t.legendgroup == "water")
    assert list(level.z) == [98.]


def test_soil_unit_label_uses_observation_unit(observations):
    data = observations.assign(parameter="TOC", medium="soil", unit="mg/kg", value=20)
    fig = map_figure(data, dict(label="TOC", unit="mg/L"))
    assert "mg/kg" in fig.layout.title.text
    assert "mg/L" not in fig.layout.title.text


def test_located_enforces_point_selection_even_with_existing_coordinates(observations):
    points = observations.loc[observations.point_id.eq("A"), ["point_id", "x", "y", "surface_elevation"]]
    result = _located(observations, points)
    assert list(result.point_id) == ["A"]
    assert _located(observations, points.iloc[:0]).empty
    assert _located(observations, pd.concat([points, points])).empty


def test_geology_leading_gap_and_invalid_trailing_extent_are_unknown(observations):
    points = observations.iloc[[0]][["point_id", "x", "y", "surface_elevation"]]
    geology = pd.DataFrame({"point_id": ["A", "A"], "depth_top": [1., 2.], "depth_bottom": [2., 4.],
                            "lithology": ["sand", "bad rock"], "valid": [True, False]})
    fig = borehole_figure(geology, points)
    assert not any(t.name == "bad rock" for t in fig.data)
    unknown = next(t for t in fig.data if t.name == "Unknown")
    np.testing.assert_allclose(np.asarray(unknown.base, float), [99., 96.])
    np.testing.assert_allclose(unknown.y, [1., 2.])


def test_conflicting_geology_never_draws_conflicting_categories(observations):
    points = observations.iloc[[0]][["point_id", "x", "y", "surface_elevation"]]
    geology = pd.DataFrame({"point_id": ["A", "A"], "depth_top": [0., 1.], "depth_bottom": [2., 3.],
                            "lithology": ["sand", "clay"], "valid": [False, False]})
    fig = borehole_figure(geology, points)
    assert len(fig.data) == 1
    assert fig.data[0].name == "Unknown"
    assert sum(fig.data[0].y) == 3.


def test_scene_without_elevation_column_uses_relative_model(observations, geology, config):
    points = observations[["point_id", "x", "y"]]
    fig = scene_figure(observations.drop(columns="surface_elevation"), geology, points, config)
    assert "relative depth" in fig.layout.title.text
    measured = next(t for t in fig.data if t.legendgroup == "measurements")
    np.testing.assert_allclose(measured.z, [-1., -1.5])
