import itertools

import numpy as np
import pandas as pd
import pytest

from spatial_monitor.extensions import (
    build_geology_model,
    correlation_figure,
    estimate_volume,
    geology_model_figure,
    geology_slice_figure,
    interpolate_volume,
    pair_parameters,
    volume_figure,
)


def observations():
    rows = []
    for i in range(4):
        for parameter, value, unit, method in [("pH", 6 + i / 10, "", "meter"), ("ORP", -200 + i * 50, "mV", "electrode")]:
            rows.append(dict(sample_id=f"S{i}", point_id=f"P{i}", parameter=parameter, value=value,
                             unit=unit, method=method, valid=True, site="A", medium="water", basis="",
                             date="2025-01-01", depth_top=1, depth_bottom=1.5, batch="round1"))
    return pd.DataFrame(rows)


def volume_observations():
    locations = list(itertools.product([0, 10], [0, 10], [0.5, 3.5]))
    locations += [(3, 3, 1.5), (5, 5, 2), (7, 7, 2.5)]
    return pd.DataFrame([dict(point_id=f"P{i}", x=x, y=y, depth_top=depth - .25,
                              depth_bottom=depth + .25, surface_elevation=100,
                              value=10 + x + y + depth, parameter="Zn", unit="mg/kg",
                              medium="soil", basis="dry", method="lab", batch="round1", valid=True,
                              date="2025-01-01", site="A", crs="EPSG:3826")
                         for i, (x, y, depth) in enumerate(locations)])


def geology_tables():
    points = pd.DataFrame([dict(point_id=f"P{i}", x=x, y=y, surface_elevation=100, crs="EPSG:3826", site="A")
                           for i, (x, y) in enumerate(itertools.product([0, 10], [0, 10]))])
    geology = pd.DataFrame([dict(point_id=p, stratum=s, depth_top=a, depth_bottom=b, valid=True)
                            for p in points.point_id for s, a, b in [("sand", 0, 2), ("clay", 2, 4)]])
    return geology, points


def test_pairing_retains_metadata_and_signed_values():
    result = pair_parameters(observations(), "pH", "ORP")
    assert result["n"] == 4
    assert result["pearson"] == pytest.approx(1)
    assert result["spearman"] == pytest.approx(1)
    assert result["pairs"].right_value.min() == -200
    assert {"left_date", "right_date", "left_depth_top", "right_depth_bottom", "left_sample_id"}.issubset(result["pairs"])
    assert result["excluded"].empty
    assert len(correlation_figure(result).data) == 1


def test_pairing_tolerances_explicit_and_no_nearest_ambiguity():
    frame = observations()
    frame.loc[frame.parameter == "ORP", "date"] = "2025-01-02"
    assert pair_parameters(frame, "pH", "ORP")["n"] == 0
    assert pair_parameters(frame, "pH", "ORP", time_tolerance_days=1)["n"] == 4
    frame = pd.concat([frame, frame.iloc[[1]]], ignore_index=True)
    result = pair_parameters(frame, "pH", "ORP", time_tolerance_days=1)
    assert result["n"] == 3
    assert len(result["excluded"]) == 3
    assert result["excluded"].reason.str.contains("ambiguous").all()


def test_pairing_never_mixes_media_mass_bases_or_unit_method_groups():
    frame = observations()
    frame.loc[1, "medium"] = "soil"
    frame.loc[3, "basis"] = "dry"
    assert pair_parameters(frame, "pH", "ORP")["n"] == 2
    frame = observations()
    frame.loc[5, "method"] = "different electrode"
    result = pair_parameters(frame, "pH", "ORP")
    assert result["n"] == 4
    assert result["pearson"] is None
    assert len(result["groups"]) == 2


def test_pairing_separates_electrodes_temperature_conditions_and_sites():
    frame = observations()
    frame["reference_electrode"] = "Ag/AgCl"
    frame.loc[3, "reference_electrode"] = "SHE"
    result = pair_parameters(frame, "pH", "ORP")
    assert result["n"] == 4
    assert len(result["groups"]) == 2
    assert result["pearson"] is None
    frame = observations()
    frame.loc[[0, 1], "site"] = "another site"
    assert len(pair_parameters(frame, "pH", "ORP")["groups"]) == 2


def test_pairing_excludes_invalid_nonfinite_and_constant_coefficients():
    frame = observations()
    frame.loc[1, "valid"] = False
    frame.loc[3, "value"] = np.inf
    frame.loc[frame.parameter == "pH", "value"] = 7
    result = pair_parameters(frame, "pH", "ORP")
    assert result["n"] == 2
    assert result["pearson"] is None
    assert len(result["excluded"]) == 4
    with pytest.raises(ValueError, match="different"):
        pair_parameters(frame, "pH", "pH")


def test_volume_is_bounded_validated_and_has_real_cell_volume():
    result = interpolate_volume(volume_observations(), {"transform": "none"},
                                {"grid_spacing": 1, "vertical_spacing": .5, "max_distance": 100})
    assert result.value.shape == (10, 10, 6)
    assert np.isfinite(result.value[result.mask]).all()
    assert result.validation["n"] == 3
    assert result.validation["unpredicted"] == 8
    assert result.validation["rmse"] >= 0
    assert result.metadata["vertical_reference"] == "elevation"
    assert result.z.min() == pytest.approx(96.75)
    assert result.z.max() == pytest.approx(99.25)
    with pytest.raises(ValueError, match="explicitly accept"):
        estimate_volume(result, 0)
    volume = estimate_volume(result, 0, accepted_validation=True)
    assert volume["volume_m3"] == pytest.approx(300)
    assert len(volume_figure(result).data) == 1


def test_volume_too_sparse_validation_cannot_authorize_stats():
    frame = volume_observations().iloc[:8]
    result = interpolate_volume(frame, {}, {"max_distance": 100})
    assert result.validation["n"] == 0
    assert result.validation["rmse"] is None
    with pytest.raises(ValueError, match="three supported"):
        estimate_volume(result, 10, accepted_validation=True)


def test_volume_rejects_mixed_groups_coplanar_locations_and_unsafe_allocation():
    frame = volume_observations()
    frame.loc[0, "unit"] = "mg/L"
    with pytest.raises(ValueError, match="single unit"):
        interpolate_volume(frame, {}, {})
    frame = volume_observations().iloc[::2][:4]
    with pytest.raises(ValueError, match="non-coplanar"):
        interpolate_volume(frame, {}, {})
    with pytest.raises(ValueError, match="max_cells"):
        interpolate_volume(volume_observations(), {}, {"grid_spacing": 1e-12})


@pytest.mark.parametrize("crs", ["", "EPSG:4326", "EPSG:2263", "unrecognized"])
def test_volume_requires_declared_metric_projected_crs(crs):
    frame = volume_observations()
    frame["crs"] = crs
    with pytest.raises(ValueError, match="CRS|metre"):
        interpolate_volume(frame, {}, {})


def test_categorical_models_and_mixed_electrodes_rejected():
    with pytest.raises(ValueError, match="Categorical"):
        interpolate_volume(volume_observations(), {"data_type": "categorical"}, {})
    frame = volume_observations()
    frame["reference_electrode"] = "SHE"
    frame.loc[0, "reference_electrode"] = "Ag/AgCl"
    with pytest.raises(ValueError, match="single reference_electrode"):
        interpolate_volume(frame, {}, {})


@pytest.mark.parametrize("parameter", ["pH", "ORP"])
def test_volume_disallows_inappropriate_log_transform(parameter):
    frame = volume_observations()
    frame["parameter"] = parameter
    with pytest.raises(ValueError, match="must not"):
        interpolate_volume(frame, {"transform": "log10"}, {})


def test_volume_log_preserves_original_unit_validation_and_relative_model():
    frame = volume_observations()
    frame.loc[0, "surface_elevation"] = np.nan
    result = interpolate_volume(frame, {"transform": "log10"}, {"max_distance": 100})
    assert result.metadata["vertical_reference"].startswith("relative depth")
    assert result.z.max() < 0
    assert result.validation["n"] == 3
    assert min(p["predicted"] for p in result.validation["predictions"]) > 10


def test_geology_requires_confirmation_and_preserves_order_and_revision():
    geology, points = geology_tables()
    with pytest.raises(ValueError, match="Confirm"):
        build_geology_model(geology, points, ["sand", "clay"], {})
    model = build_geology_model(geology, points, ["sand", "clay"], {}, confirmed=True, revision="R1")
    assert model.interfaces.shape == (3, 10, 10)
    assert np.allclose(model.interfaces[:, model.mask], np.array([100, 98, 96])[:, None])
    assert model.metadata["revision"] == "R1"
    assert model.metadata["validation"]["n"] == 0
    assert model.metadata["validation"]["unpredicted"] == 4
    assert len(geology_model_figure(model).data) == 3
    assert len(geology_slice_figure(model).data) == 3


def test_geology_validates_held_out_boreholes_in_metres():
    geology, points = geology_tables()
    center = pd.DataFrame([dict(point_id="CENTER", x=5, y=5, surface_elevation=101, crs="EPSG:3826", site="A")])
    points = pd.concat([points, center], ignore_index=True)
    logs = geology.loc[geology.point_id == "P0"].copy()
    logs["point_id"] = "CENTER"
    geology = pd.concat([geology, logs], ignore_index=True)
    model = build_geology_model(geology, points, ["sand", "clay"], {}, confirmed=True, revision="R1")
    assert model.metadata["validation"]["n"] == 1
    assert model.metadata["validation"]["rmse_by_interface_m"] == pytest.approx([1, 1, 1])


def test_geology_excludes_missing_overlapping_or_discordant_boreholes():
    geology, points = geology_tables()
    geology["depth_top"] = geology.depth_top.astype(float)
    geology.loc[1, "depth_top"] = 1.5
    model = build_geology_model(geology, points, ["sand", "clay"], {}, confirmed=True, revision="R2")
    assert len(model.metadata["source_points"]) == 3
    assert model.metadata["excluded_boreholes"][0]["reason"] == "gap or overlap between strata"
    geology.loc[3, "stratum"] = "unknown"
    with pytest.raises(ValueError, match="three complete"):
        build_geology_model(geology, points, ["sand", "clay"], {}, confirmed=True, revision="R3")


def test_stratum_constraint_requires_model_and_limits_3d_domain():
    with pytest.raises(ValueError, match="confirmed ordered"):
        interpolate_volume(volume_observations(), {}, {"stratum": "clay"})
    geology, points = geology_tables()
    model = build_geology_model(geology, points, ["sand", "clay"], {}, confirmed=True, revision="R1")
    frame = volume_observations()
    # Two depths in clay at all four corners support a genuine 3D hull.
    for idx in range(8):
        depth = 2.5 if idx % 2 == 0 else 3.5
        frame.loc[idx, ["depth_top", "depth_bottom"]] = [depth - .25, depth + .25]
    result = interpolate_volume(frame, {}, {"stratum": "clay", "max_distance": 100}, geology=model)
    assert result.z[result.mask].max() <= 98
    assert result.z[result.mask].min() >= 96
    assert result.metadata["excluded_by_stratum"] == 2


def test_stratum_constraint_rejects_other_projected_crs_and_site():
    geology, points = geology_tables()
    points["crs"] = "EPSG:3825"
    model = build_geology_model(geology, points, ["sand", "clay"], {}, confirmed=True, revision="R1")
    with pytest.raises(ValueError, match="same coordinate reference"):
        interpolate_volume(volume_observations(), {}, {"stratum": "clay"}, geology=model)
    points["crs"] = "EPSG:3826"
    points["site"] = "another site"
    model = build_geology_model(geology, points, ["sand", "clay"], {}, confirmed=True, revision="R2")
    with pytest.raises(ValueError, match="same site"):
        interpolate_volume(volume_observations(), {}, {"stratum": "clay"}, geology=model)


def test_partial_domain_surfaces_do_not_bridge_unsupported_grid_nodes():
    from spatial_monitor.extensions import VolumeResult
    x, y, z = np.meshgrid(np.arange(4.), np.arange(4.), np.arange(4.), indexing="ij")
    values = x + y + z - 5  # Signed values must not use zero as a missing-data fill.
    mask = np.ones_like(x, dtype=bool)
    mask[0] = False
    values[~mask] = np.nan
    result = VolumeResult(x, y, z, values, mask, {}, {"parameter": "ORP", "unit": "mV", "vertical_reference": "relative"})
    figure = volume_figure(result)
    assert figure.data
    assert all(trace.type == "mesh3d" for trace in figure.data)
    assert all(min(trace.x) >= 1 for trace in figure.data)
    assert all(np.isfinite(trace.x).all() for trace in figure.data)
    assert all(isinstance(trace.to_plotly_json()["x"], list) for trace in figure.data)


def test_complete_domain_isosurface_serializes_plain_grid_arrays():
    import json
    frame = volume_observations()
    result = interpolate_volume(frame, {"transform": "none"}, {"grid_spacing": 1, "vertical_spacing": .5, "max_distance": 100})
    trace = json.loads(volume_figure(result).to_json())["data"][0]
    assert trace["type"] == "isosurface"
    assert isinstance(trace["x"], list)
    assert len(trace["x"]) == result.value.size
    assert trace["isomin"] == pytest.approx(float(np.nanmin(result.value)))
