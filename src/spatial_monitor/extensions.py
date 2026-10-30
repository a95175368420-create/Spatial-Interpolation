"""Opt-in analysis with explicit support, pairing and interpretation boundaries.

These functions never change the imported data. Numeric models represent sample
intervals by their midpoints; they are not measured continuous fields. Geological
models are an explicit, revisioned interpretation of complete ordered boreholes.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from scipy.spatial import Delaunay, QhullError, cKDTree
from scipy.stats import pearsonr, spearmanr


@dataclass
class VolumeResult:
    x: np.ndarray
    y: np.ndarray
    z: np.ndarray
    value: np.ndarray
    mask: np.ndarray
    validation: dict[str, Any]
    metadata: dict[str, Any]


@dataclass
class GeologyModel:
    x: np.ndarray
    y: np.ndarray
    interfaces: np.ndarray  # boundary, x index, y index; elevations decrease downward
    strata: list[str]
    mask: np.ndarray
    metadata: dict[str, Any]


def _series(frame: pd.DataFrame, name: str, default: Any = "") -> pd.Series:
    return frame[name] if name in frame else pd.Series(default, index=frame.index)


def _valid(frame: pd.DataFrame) -> pd.Series:
    return _series(frame, "valid", True).fillna(False).astype(bool)


def _correlation(frame: pd.DataFrame) -> dict[str, Any]:
    n = len(frame)
    out = {"n": n, "pearson": None, "spearman": None}
    if n >= 3 and frame.left_value.nunique() > 1 and frame.right_value.nunique() > 1:
        out.update(pearson=float(pearsonr(frame.left_value, frame.right_value).statistic),
                   spearman=float(spearmanr(frame.left_value, frame.right_value).statistic))
    return out


def pair_parameters(observations: pd.DataFrame, left: str, right: str,
                    time_tolerance_days: float = 0, depth_tolerance: float = 0,
                    group_by: str = "medium") -> dict[str, Any]:
    """Mutual unique matches only; no nearest-match tie breaking or averaging.

    Sites, points, media and mass bases must match. Date and both depth bounds
    must satisfy the supplied tolerances. Statistics are separated by both
    methods/units and the requested grouping column. A global coefficient is
    withheld when multiple incomparable groups are present.
    """
    if left == right:
        raise ValueError("Select two different parameters.")
    if not np.isfinite([time_tolerance_days, depth_tolerance]).all() or min(time_tolerance_days, depth_tolerance) < 0:
        raise ValueError("Pairing tolerances must be finite and non-negative.")
    required = {"parameter", "value", "point_id", "date", "depth_top", "depth_bottom"}
    if not required.issubset(observations.columns):
        raise ValueError(f"Missing pairing columns: {sorted(required - set(observations.columns))}")
    if group_by not in observations.columns:
        raise ValueError(f"Grouping column {group_by!r} is unavailable.")
    frame = observations.copy().reset_index(drop=True)
    frame["_row"] = np.arange(len(frame))
    frame["_date"] = pd.to_datetime(frame.date, errors="coerce", utc=True)
    for name in ("value", "depth_top", "depth_bottom"):
        frame[name] = pd.to_numeric(frame[name], errors="coerce")
    usable = _valid(frame) & np.isfinite(frame[["value", "depth_top", "depth_bottom"]]).all(axis=1)
    usable &= frame._date.notna() & frame.point_id.notna() & frame.point_id.astype(str).str.strip().ne("")
    usable &= (frame.depth_top >= 0) & (frame.depth_bottom >= frame.depth_top)
    selected = frame.parameter.isin([left, right])
    excluded = [{"row": int(r._row), "parameter": r.parameter, "reason": "invalid or incomplete observation"}
                for _, r in frame.loc[selected & ~usable].iterrows()]
    a = frame.loc[usable & (frame.parameter == left)].copy()
    b = frame.loc[usable & (frame.parameter == right)].copy()
    keys = ["site", "point_id", "medium", "basis"]
    for f in (a, b):
        for k in keys:
            f[k] = _series(f, k).fillna("").astype(str)
    candidates: dict[int, list[int]] = {}
    reverse: dict[int, list[int]] = {int(i): [] for i in b.index}
    for i, row in a.iterrows():
        match = np.ones(len(b), dtype=bool)
        for key in keys:
            match &= (b[key] == row[key]).to_numpy()
        match &= ((b._date - row._date).abs().dt.total_seconds() <= time_tolerance_days * 86400).to_numpy()
        match &= ((b.depth_top - row.depth_top).abs() <= depth_tolerance).to_numpy()
        match &= ((b.depth_bottom - row.depth_bottom).abs() <= depth_tolerance).to_numpy()
        candidates[int(i)] = [int(j) for j in b.index[match]]
        for j in candidates[int(i)]:
            reverse[j].append(int(i))
    pairs = []
    used_b = set()
    for i, possible in candidates.items():
        if len(possible) == 1 and len(reverse[possible[0]]) == 1:
            j = possible[0]
            used_b.add(j)
            ar, br = a.loc[i], b.loc[j]
            pair = {k: ar[k] for k in keys}
            pair[group_by] = ar.get(group_by, "")
            # A grouping value must agree between the two observations too.
            if str(ar.get(group_by, "")) != str(br.get(group_by, "")):
                used_b.remove(j)
                excluded.append({"row": i, "parameter": left, "reason": "group mismatch"})
                continue
            for prefix, row in (("left", ar), ("right", br)):
                for k in ("sample_id", "value", "date", "depth_top", "depth_bottom", "method", "unit", "batch",
                          "reference_electrode", "temperature_compensated", "comparison_temperature"):
                    pair[f"{prefix}_{k}"] = row.get(k, "")
                pair[f"{prefix}_row"] = int(row._row)
            pairs.append(pair)
        else:
            reason = "no compatible match" if not possible else "ambiguous match"
            excluded.append({"row": i, "parameter": left, "reason": reason})
    for j in b.index:
        if j not in used_b:
            excluded.append({"row": int(j), "parameter": right,
                             "reason": "no compatible match" if not reverse[int(j)] else "ambiguous or incompatible match"})
    paired = pd.DataFrame(pairs)
    group_columns = list(dict.fromkeys(["site", "medium", "basis", "left_unit", "right_unit", "left_method", "right_method",
                                       "left_reference_electrode", "right_reference_electrode",
                                       "left_temperature_compensated", "right_temperature_compensated",
                                       "left_comparison_temperature", "right_comparison_temperature", group_by]))
    groups = []
    if len(paired):
        for key, subset in paired.groupby(group_columns, dropna=False, sort=True):
            groups.append(dict(zip(group_columns, key)) | _correlation(subset))
    totals = _correlation(paired) if len(groups) == 1 else {"n": len(paired), "pearson": None, "spearman": None}
    return {"pairs": paired, "excluded": pd.DataFrame(excluded, columns=["row", "parameter", "reason"]),
            "groups": pd.DataFrame(groups), **totals, "left": left, "right": right,
            "notes": "Mutual unique matches only. Correlation does not establish causation. Coefficients require at least three nonconstant pairs per comparable group.",
            "rules": {"time_tolerance_days": time_tolerance_days, "depth_tolerance": depth_tolerance, "group_by": group_by,
                      "comparison_groups": group_columns}}


def _positive(settings: dict, key: str, default: float) -> float:
    value = float(settings.get(key, default))
    if not np.isfinite(value) or value <= 0:
        raise ValueError(f"{key} must be finite and positive.")
    return value


def _hull(points: np.ndarray) -> Delaunay:
    dim = points.shape[1]
    if len(points) < dim + 1 or np.linalg.matrix_rank(points - points[0]) < dim:
        raise ValueError(f"At least {dim + 1} non-coplanar/non-collinear unique locations are required.")
    try:
        return Delaunay(points)
    except QhullError as exc:
        raise ValueError("Locations do not define a numerically stable interpolation domain.") from exc


def _idw(points: np.ndarray, values: np.ndarray, query: np.ndarray, power: float) -> np.ndarray:
    """All observations participate; chunks bound the temporary distance matrix."""
    values = np.asarray(values, dtype=float)
    output = np.empty((len(query),) + values.shape[1:])
    chunk_size = max(1, min(2048, 1_000_000 // max(len(points), 1)))
    for start in range(0, len(query), chunk_size):
        q = query[start:start + chunk_size]
        distance = np.linalg.norm(q[:, None, :] - points[None, :, :], axis=2)
        exact = distance == 0
        # Scaling each row by its minimum positive distance avoids overflow.
        safe = np.where(exact, np.inf, distance)
        minimum = safe.min(axis=1, keepdims=True)
        minimum[~np.isfinite(minimum)] = 1
        weights = np.power(np.divide(minimum, safe), power)
        has_exact = exact.any(axis=1)
        weights[has_exact] = exact[has_exact].astype(float)
        weights /= weights.sum(axis=1, keepdims=True)
        output[start:start + len(q)] = weights @ values
    return output


def _axes(points: np.ndarray, spacings: list[float], max_cells: int,
          multiplicity: int = 1) -> list[np.ndarray]:
    """Check the full grid size before allocating any coordinate arrays."""
    lows, highs = points.min(axis=0), points.max(axis=0)
    counts = []
    total = multiplicity
    for low, high, spacing in zip(lows, highs, spacings):
        ratio = (high - low) / spacing
        if not np.isfinite(ratio) or ratio > max_cells:
            raise ValueError("Requested grid exceeds max_cells; increase grid spacing.")
        count = int(np.floor(ratio + 1e-10))
        if count < 1:
            raise ValueError("Grid spacing exceeds the data extent; reduce the spacing.")
        counts.append(count)
        total *= count
        if total > max_cells:
            raise ValueError(f"Requested grid exceeds max_cells={max_cells}; increase grid spacing.")
    return [low + (np.arange(count) + 0.5) * spacing
            for low, count, spacing in zip(lows, counts, spacings)]


def _homogeneous(frame: pd.DataFrame, columns: list[str]) -> None:
    for name in columns:
        if name in frame and frame[name].fillna("").astype(str).nunique() > 1:
            raise ValueError(f"Select a single {name} before modelling; values are not averaged across groups.")


def _crs_check(frame: pd.DataFrame) -> None:
    from pyproj import CRS

    _homogeneous(frame, ["crs"])
    for crs in _series(frame, "crs").fillna("").astype(str).unique():
        if crs.upper() == "LOCAL_METRIC":
            continue
        if not crs.strip():
            raise ValueError("Declare a projected metre CRS or explicitly declare LOCAL_METRIC before modelling.")
        try:
            definition = CRS.from_user_input(crs)
        except Exception as exc:
            raise ValueError(f"Unrecognized CRS {crs!r}; use a projected metre CRS or LOCAL_METRIC.") from exc
        if not definition.is_projected or len(definition.axis_info) < 2 or any(
                not np.isclose(axis.unit_conversion_factor, 1) for axis in definition.axis_info[:2]):
            raise ValueError("Project coordinates to a metre-based coordinate system before modelling.")


def _same_crs(left: str, right: str) -> bool:
    from pyproj import CRS

    if left.upper() == "LOCAL_METRIC" or right.upper() == "LOCAL_METRIC":
        return left.upper() == right.upper()
    return CRS.from_user_input(left) == CRS.from_user_input(right)


def _vertical(frame: pd.DataFrame) -> tuple[np.ndarray, str]:
    elevation = pd.to_numeric(_series(frame, "surface_elevation", np.nan), errors="coerce").to_numpy()
    if np.isfinite(elevation).all():
        return elevation, "elevation"
    return np.zeros(len(frame)), "relative depth (all ground surfaces = 0 m)"


def _geological_bounds(model: GeologyModel, query_xy: np.ndarray, stratum: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    if stratum not in model.strata:
        raise ValueError(f"Unknown stratum {stratum!r}.")
    points = np.asarray(model.metadata["source_xy"], dtype=float)
    levels = np.asarray(model.metadata["source_interfaces"], dtype=float)
    support = _hull(points).find_simplex(query_xy, tol=1e-10) >= 0
    limit = model.metadata.get("max_distance")
    if limit is not None:
        support &= cKDTree(points).query(query_xy)[0] <= limit
    horizons = _idw(points, levels, query_xy, model.metadata["power"])
    index = model.strata.index(stratum)
    return horizons[:, index], horizons[:, index + 1], support


def interpolate_volume(observations: pd.DataFrame, parameter_config: dict,
                       settings: dict, geology: GeologyModel | None = None) -> VolumeResult:
    """Anisotropic 3D IDW inside the measured midpoint convex hull.

    Grid values live at cell centers. Full cells near the hull may cross the
    support boundary; the resulting volume is a resolution-dependent estimate.
    A stratum restriction requires a confirmed, compatible GeologyModel.
    """
    if parameter_config.get("data_type", "continuous") not in {"continuous", "numeric", "number"}:
        raise ValueError("Categorical parameters must not use numeric 3D interpolation.")
    required = {"x", "y", "depth_top", "depth_bottom", "value"}
    if not required.issubset(observations):
        raise ValueError(f"Missing volume columns: {sorted(required - set(observations.columns))}")
    frame = observations.loc[_valid(observations)].copy()
    for name in required:
        frame[name] = pd.to_numeric(frame[name], errors="coerce")
    frame = frame.loc[np.isfinite(frame[list(required)]).all(axis=1)]
    if frame.empty:
        raise ValueError("No valid numeric observations.")
    if (frame.depth_bottom < frame.depth_top).any() or (frame.depth_top < 0).any():
        raise ValueError("Sampling depths must be nonnegative and ordered.")
    _homogeneous(frame, ["parameter", "unit", "medium", "basis", "method", "site", "batch",
                         "reference_electrode", "temperature_compensated", "comparison_temperature"])
    # An explicit batch may span several survey days. Without it, dates must agree.
    if not _series(frame, "batch").fillna("").astype(str).str.strip().any():
        _homogeneous(frame, ["date"])
    _crs_check(frame)
    horizontal = _positive(settings, "horizontal_scale", 1)
    vertical = _positive(settings, "vertical_scale", 1)
    dx = _positive(settings, "grid_spacing", 1)
    dz = _positive(settings, "vertical_spacing", 0.5)
    power = _positive(settings, "power", 2)
    max_distance = _positive(settings, "max_distance", 10)
    max_cells = int(_positive(settings, "max_cells", 100000))
    surface, reference = _vertical(frame)
    mid = (frame.depth_top.to_numpy() + frame.depth_bottom.to_numpy()) / 2
    raw_points = np.column_stack([frame.x, frame.y, surface - mid])
    stratum = settings.get("stratum")
    excluded_by_stratum = 0
    if stratum:
        if not isinstance(geology, GeologyModel) or not geology.metadata.get("confirmed"):
            raise ValueError("Stratum restriction requires a confirmed ordered GeologyModel.")
        if reference != geology.metadata["vertical_reference"]:
            raise ValueError("Geology and observations must use the same vertical reference.")
        if not _same_crs(str(frame.crs.iloc[0]), geology.metadata["crs"]):
            raise ValueError("Geology and observations must use the same coordinate reference system.")
        if str(_series(frame, "site").fillna("").iloc[0]) != geology.metadata["site"]:
            raise ValueError("Geology and observations must belong to the same site.")
        top, bottom, supported = _geological_bounds(geology, raw_points[:, :2], stratum)
        selected = supported & (surface - frame.depth_top.to_numpy() <= top + 1e-8) & (surface - frame.depth_bottom.to_numpy() >= bottom - 1e-8)
        excluded_by_stratum = int((~selected).sum())
        frame, raw_points = frame.loc[selected], raw_points[selected]
    if len(np.unique(raw_points, axis=0)) != len(raw_points):
        raise ValueError("Repeated 3D locations require explicit duplicate resolution before modelling.")
    scales = np.array([horizontal, horizontal, vertical])
    points = raw_points / scales
    hull = _hull(points)
    values = frame.value.to_numpy(dtype=float)
    transform = parameter_config.get("transform", "none")
    parameter = str(frame.parameter.iloc[0]) if "parameter" in frame else parameter_config.get("name", "value")
    if transform == "log10":
        if parameter.lower() in {"ph", "orp"}:
            raise ValueError("pH and ORP must not use a concentration log transform.")
        if (values <= 0).any():
            raise ValueError("Log interpolation requires strictly positive measurements.")
        transformed = np.log10(values)
    elif transform in {"none", "linear", "identity", None}:
        transformed = values
    else:
        raise ValueError(f"Unsupported transform {transform!r}.")
    axes = _axes(raw_points, [dx, dx, dz], max_cells)
    size = int(np.prod([len(axis) for axis in axes]))
    if size > max_cells:
        raise ValueError(f"Requested {size} cells exceeds max_cells={max_cells}; increase grid spacing.")
    x, y, z = np.meshgrid(*axes, indexing="ij")
    query_raw = np.column_stack([x.ravel(), y.ravel(), z.ravel()])
    query = query_raw / scales
    mask_flat = (hull.find_simplex(query, tol=1e-10) >= 0) & (cKDTree(points).query(query)[0] <= max_distance)
    if stratum:
        top, bottom, supported = _geological_bounds(geology, query_raw[:, :2], stratum)
        mask_flat &= supported & (query_raw[:, 2] <= top) & (query_raw[:, 2] >= bottom)
    if not mask_flat.any():
        raise ValueError("No grid-cell centers lie within the supported model domain.")
    output = np.full(size, np.nan)
    predicted = _idw(points, transformed, query[mask_flat], power)
    output[mask_flat] = 10 ** predicted if transform == "log10" else predicted
    # Leave-one-out checks enforce the same hull and nearest-distance support.
    errors = []
    validation_rows = []
    for i in range(len(points)):
        others = np.arange(len(points)) != i
        try:
            holdout_hull = _hull(points[others])
        except ValueError:
            continue
        if holdout_hull.find_simplex(points[i], tol=1e-10) < 0:
            continue
        if cKDTree(points[others]).query(points[i])[0] > max_distance:
            continue
        pred = float(_idw(points[others], transformed[others], points[i:i + 1], power)[0])
        if transform == "log10":
            pred = 10 ** pred
        error = pred - values[i]
        errors.append(error)
        validation_rows.append({"row": int(i), "observed": float(values[i]), "predicted": pred, "error": float(error)})
    validation = {"method": "leave-one-out; same convex-hull and nearest-distance support",
                  "n": len(errors), "total": len(points), "unpredicted": len(points) - len(errors),
                  "coverage": len(errors) / len(points),
                  "rmse": float(np.sqrt(np.mean(np.square(errors)))) if errors else None,
                  "mae": float(np.mean(np.abs(errors))) if errors else None,
                  "predictions": validation_rows,
                  "usable": len(errors) >= 3,
                  "note": "Errors are in original measurement units. Acceptance requires project-specific judgement; convex-hull vertices cannot be validated without extrapolation."}
    metadata = {"parameter": parameter, "unit": str(frame.unit.iloc[0]) if "unit" in frame else "",
                "crs": str(frame.crs.iloc[0]), "site": str(_series(frame, "site").fillna("").iloc[0]),
                "vertical_reference": reference, "representation": "sample interval midpoints",
                "spacing": [dx, dx, dz], "scales": scales.tolist(), "power": power,
                "max_scaled_distance": max_distance, "transform": transform,
                "stratum": stratum, "excluded_by_stratum": excluded_by_stratum,
                "source_count": len(points), "supported_cells": int(mask_flat.sum()),
                "support": "3D convex hull of measured interval midpoints and nearest scaled distance; no extrapolation",
                "volume_boundary": "Cell-center inclusion; full cells can cross the support boundary. Resolution-dependent estimate, not a surveyed pollution boundary."}
    return VolumeResult(x, y, z, output.reshape(x.shape), mask_flat.reshape(x.shape), validation, metadata)


def estimate_volume(result: VolumeResult, threshold: float | tuple[float, float],
                    direction: str = "above", accepted_validation: bool = False) -> dict[str, Any]:
    """Count cell-center exceedances only after explicit review of usable validation."""
    if not accepted_validation:
        raise ValueError("Review model validation and explicitly accept it before estimating volume.")
    if not result.validation.get("usable") or result.validation.get("n", 0) < 3:
        raise ValueError("Volume estimates require at least three supported leave-one-out predictions.")
    if not np.isfinite([result.validation.get("rmse", np.nan), result.validation.get("mae", np.nan)]).all():
        raise ValueError("Validation errors must be finite.")
    values = result.value
    if direction in {"above", "below"}:
        cutoff = float(threshold)
        if not np.isfinite(cutoff):
            raise ValueError("Threshold must be finite.")
        selected = values >= cutoff if direction == "above" else values <= cutoff
    elif direction == "outside":
        if not isinstance(threshold, (tuple, list)) or len(threshold) != 2:
            raise ValueError("Outside-range thresholds need a lower and upper value.")
        low, high = map(float, threshold)
        if not np.isfinite([low, high]).all() or low >= high:
            raise ValueError("Threshold range must be finite and ordered.")
        selected = (values < low) | (values > high)
    else:
        raise ValueError("Direction must be above, below, or outside.")
    count = int((selected & result.mask & np.isfinite(values)).sum())
    cell_volume = float(np.prod(result.metadata["spacing"]))
    return {"volume_m3": count * cell_volume, "selected_cells": count,
            "supported_cells": int(result.mask.sum()), "cell_volume_m3": cell_volume,
            "threshold": threshold, "direction": direction,
            "validation": result.validation, "assumptions": result.metadata["volume_boundary"],
            "stratum": result.metadata.get("stratum")}


def build_geology_model(geology: pd.DataFrame, points: pd.DataFrame, strata: list[str],
                        settings: dict, confirmed: bool = False, revision: str = "") -> GeologyModel:
    """Interpret complete, contiguous, ordered boreholes as IDW horizons.

    Does not infer missing strata, faults, pinching layers or geological contacts.
    Incomplete/discordant boreholes are excluded and explicitly reported.
    """
    if not confirmed or not str(revision).strip():
        raise ValueError("Confirm the stratigraphic interpretation and supply a revision identifier.")
    if not strata or len(set(strata)) != len(strata) or any(not str(s).strip() for s in strata):
        raise ValueError("Supply a nonempty unique ordered stratum sequence.")
    required = {"point_id", "stratum", "depth_top", "depth_bottom"}
    if not required.issubset(geology) or not {"point_id", "x", "y"}.issubset(points):
        raise ValueError("Geology requires point IDs, strata and depths; points require coordinates.")
    _crs_check(points)
    _homogeneous(points, ["site"])
    if points.point_id.duplicated().any():
        raise ValueError("Point identifiers must be unique.")
    power = _positive(settings, "power", 2)
    spacing = _positive(settings, "grid_spacing", 1)
    max_cells = int(_positive(settings, "max_cells", 100000))
    max_distance = _positive(settings, "max_distance", 10)
    source = []
    depth_interfaces = []
    excluded = []
    for point_id, intervals in geology.groupby("point_id", sort=True):
        g = intervals.copy()
        for name in ("depth_top", "depth_bottom"):
            g[name] = pd.to_numeric(g[name], errors="coerce")
        g = g.sort_values("depth_top")
        point = points.loc[points.point_id == point_id]
        reason = None
        if not _valid(g).all() or not np.isfinite(g[["depth_top", "depth_bottom"]]).all().all():
            reason = "invalid geological interval"
        elif list(g.stratum.astype(str)) != list(map(str, strata)):
            reason = "incomplete or discordant stratum sequence"
        elif (g.depth_top < 0).any() or (g.depth_bottom <= g.depth_top).any():
            reason = "invalid interval thickness"
        elif not np.allclose(g.depth_bottom.to_numpy()[:-1], g.depth_top.to_numpy()[1:], atol=1e-8, rtol=0):
            reason = "gap or overlap between strata"
        elif len(point) != 1 or not np.isfinite(pd.to_numeric(point.iloc[0][["x", "y"]], errors="coerce")).all():
            reason = "missing or invalid point coordinates"
        if reason:
            excluded.append({"point_id": str(point_id), "reason": reason})
            continue
        source.append(point.iloc[0])
        depth_interfaces.append(np.r_[g.depth_top.iloc[0], g.depth_bottom.to_numpy()])
    source_frame = pd.DataFrame(source)
    if len(source_frame) < 3:
        raise ValueError("At least three complete, contiguous boreholes are required for the chosen sequence.")
    xy = source_frame[["x", "y"]].to_numpy(dtype=float)
    if len(np.unique(xy, axis=0)) != len(xy):
        raise ValueError("Repeated borehole coordinates require explicit resolution.")
    hull = _hull(xy)
    surface, reference = _vertical(source_frame)
    levels = surface[:, None] - np.asarray(depth_interfaces)
    x_axis, y_axis = _axes(xy, [spacing, spacing], max_cells, len(strata) + 1)
    x, y = np.meshgrid(x_axis, y_axis, indexing="ij")
    query = np.c_[x.ravel(), y.ravel()]
    mask = (hull.find_simplex(query, tol=1e-10) >= 0) & (cKDTree(xy).query(query)[0] <= max_distance)
    if not mask.any():
        raise ValueError("No supported geological grid cells; adjust spacing or distance.")
    interpolated = _idw(xy, levels, query, power)
    if (np.diff(interpolated[mask], axis=1) >= 0).any():
        raise ValueError("Interpreted horizons cross or collapse; revise the geological constraints.")
    interpolated[~mask] = np.nan
    interfaces = interpolated.T.reshape((len(strata) + 1,) + x.shape)
    # Hold out entire boreholes, not individual boundaries of a known borehole.
    # Report unsupported hull-edge holdouts explicitly instead of extrapolating.
    errors = []
    predictions = []
    for index in range(len(xy)):
        others = np.arange(len(xy)) != index
        try:
            holdout_hull = _hull(xy[others])
        except ValueError:
            continue
        if holdout_hull.find_simplex(xy[index], tol=1e-10) < 0 or cKDTree(xy[others]).query(xy[index])[0] > max_distance:
            continue
        estimated = _idw(xy[others], levels[others], xy[index:index + 1], power)[0]
        errors.append(estimated - levels[index])
        predictions.append({"point_id": str(source_frame.point_id.iloc[index]),
                            "observed_interfaces": levels[index].tolist(),
                            "predicted_interfaces": estimated.tolist()})
    validation = {"method": "leave-one-borehole-out; same convex-hull and nearest-distance support",
                  "n": len(errors), "total": len(xy), "unpredicted": len(xy) - len(errors),
                  "coverage": len(errors) / len(xy),
                  "rmse_by_interface_m": np.sqrt(np.mean(np.square(errors), axis=0)).tolist() if errors else None,
                  "mae_by_interface_m": np.mean(np.abs(errors), axis=0).tolist() if errors else None,
                  "predictions": predictions,
                  "note": "A geometric error check does not validate geological continuity or inferred contacts."}
    return GeologyModel(x, y, interfaces, list(strata), mask.reshape(x.shape),
                        {"confirmed": True, "revision": str(revision), "vertical_reference": reference,
                         "crs": str(source_frame.crs.iloc[0]), "site": str(_series(source_frame, "site").fillna("").iloc[0]),
                         "method": "IDW ordered geological interfaces; expert-confirmed interpretation",
                         "power": power, "spacing": spacing, "max_distance": max_distance,
                         "source_points": source_frame.point_id.astype(str).tolist(),
                         "source_xy": xy.tolist(), "source_interfaces": levels.tolist(),
                         "excluded_boreholes": excluded,
                         "validation": validation,
                         "limitations": "No inferred faults, absent layers or pinch-outs. Valid only inside borehole convex hull and nearest-distance support."})


def correlation_figure(result: dict):
    import plotly.graph_objects as go
    figure = go.Figure()
    pairs = result["pairs"]
    if len(pairs):
        grouping = result.get("rules", {}).get("comparison_groups", ["site", "medium", "basis", "left_unit", "right_unit", "left_method", "right_method"])
        for key, group in pairs.groupby(grouping, dropna=False):
            figure.add_trace(go.Scatter(x=group.left_value, y=group.right_value, mode="markers",
                                       name=" / ".join(str(x) for x in key if str(x)),
                                       text=group.point_id.astype(str), hovertemplate="%{text}<br>x=%{x}<br>y=%{y}<extra></extra>"))
    figure.update_layout(title=f"Paired observations · n={result['n']} · association is not causation",
                         xaxis_title=result["left"], yaxis_title=result["right"])
    return figure


def _supported_isosurface(result: VolumeResult, level: float):
    """March tetrahedra only within cubes whose eight grid nodes are supported."""
    offsets = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0),
               (0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)]
    slices = [tuple(slice(o, size - 1 + o) for o, size in zip(offset, result.value.shape))
              for offset in offsets]
    valid = np.stack([(result.mask & np.isfinite(result.value))[part] for part in slices], axis=-1).all(axis=-1)
    if not valid.any():
        return np.empty((0, 3))
    positions = np.stack([np.stack([axis[part] for axis in (result.x, result.y, result.z)], axis=-1)
                          for part in slices], axis=-2)[valid]
    values = np.stack([result.value[part] for part in slices], axis=-1)[valid]
    tetrahedra = [(0, 1, 2, 6), (0, 2, 3, 6), (0, 3, 7, 6),
                  (0, 7, 4, 6), (0, 4, 5, 6), (0, 5, 1, 6)]
    triangles = []
    for tetrahedron in tetrahedra:
        tv, tp = values[:, tetrahedron], positions[:, tetrahedron]
        crossing = (tv.min(axis=1) < level) & (tv.max(axis=1) >= level)
        for scalar, xyz in zip(tv[crossing], tp[crossing]):
            inside, outside = np.flatnonzero(scalar >= level), np.flatnonzero(scalar < level)
            def edge(a, b):
                return xyz[a] + (level - scalar[a]) / (scalar[b] - scalar[a]) * (xyz[b] - xyz[a])
            if len(inside) == 1:
                triangles.append([edge(inside[0], j) for j in outside])
            elif len(outside) == 1:
                triangles.append([edge(outside[0], j) for j in inside])
            else:
                a, b = inside
                c, d = outside
                polygon = [edge(a, c), edge(a, d), edge(b, d), edge(b, c)]
                triangles.extend([[polygon[0], polygon[1], polygon[2]], [polygon[0], polygon[2], polygon[3]]])
    return np.asarray(triangles, dtype=float).reshape(-1, 3)


def volume_figure(result: VolumeResult):
    """Render estimates without filling unsupported cells with fabricated zeros.

    Isosurface's Plotly.js renderer currently requires ordinary arrays, rather
    than Plotly 6 binary typed arrays. Partial domains use explicit supported
    meshes; very large or degenerate domains fall back to labeled grid centers.
    """
    import plotly.graph_objects as go
    supported = result.mask & np.isfinite(result.value)
    figure = go.Figure()
    if not supported.any():
        figure.add_annotation(text="No supported 3D estimates", showarrow=False)
        return figure
    low, high = float(result.value[supported].min()), float(result.value[supported].max())
    if supported.all() and high > low and min(result.value.shape) >= 2:
        figure.add_trace(go.Isosurface(x=result.x.ravel().tolist(), y=result.y.ravel().tolist(),
            z=result.z.ravel().tolist(), value=result.value.ravel().tolist(),
            isomin=low, isomax=high, cmin=low, cmax=high, opacity=.35, surface_count=4,
            caps=dict(x_show=False, y_show=False, z_show=False), colorscale="Viridis",
            colorbar=dict(title=result.metadata["unit"]), name="3D IDW estimate"))
    elif result.value.size <= 20000 and min(result.value.shape) >= 2 and high > low:
        for level in np.linspace(low, high, 6)[1:-1]:
            vertices = _supported_isosurface(result, float(level))
            if len(vertices):
                faces = np.arange(len(vertices)).reshape(-1, 3)
                figure.add_trace(go.Mesh3d(x=vertices[:, 0].tolist(), y=vertices[:, 1].tolist(),
                    z=vertices[:, 2].tolist(), i=faces[:, 0].tolist(), j=faces[:, 1].tolist(),
                    k=faces[:, 2].tolist(), intensity=[float(level)] * len(vertices),
                    cmin=low, cmax=high, colorscale="Viridis", opacity=.35,
                    showscale=len(figure.data) == 0, colorbar=dict(title=result.metadata["unit"]),
                    name=f"Supported isosurface · {level:.4g}", showlegend=True,
                    hovertemplate=f"3D estimate: {level:.4g} {result.metadata['unit']}<extra></extra>"))
        figure.add_annotation(text="Surfaces use only cubes with eight supported grid nodes; gaps remain unknown.",
                              showarrow=False, x=0, y=0, xref="paper", yref="paper")
    if not figure.data:
        indices = np.flatnonzero(supported.ravel())
        indices = indices[::max(1, int(np.ceil(len(indices) / 10000)))]
        figure.add_trace(go.Scatter3d(x=result.x.ravel()[indices].tolist(), y=result.y.ravel()[indices].tolist(),
            z=result.z.ravel()[indices].tolist(), mode="markers", name="Supported grid centers (not an isosurface)",
            marker=dict(size=3, color=result.value.ravel()[indices].tolist(), cmin=low, cmax=high,
                        colorscale="Viridis", showscale=True, colorbar=dict(title=result.metadata["unit"]))))
        figure.add_annotation(text="Supported grid centers shown; large grids are subsampled to at most 10,000 display points.",
                              showarrow=False, x=0, y=0, xref="paper", yref="paper")
    figure.update_layout(title=dict(text=f"{result.metadata['parameter']} · 3D estimate · midpoint representation", y=.98),
                         legend=dict(orientation="h", x=0, y=1.02, yanchor="bottom", maxheight=65),
                         margin=dict(t=130, r=65, b=65), height=620,
                         scene=dict(xaxis_title="X (m)", yaxis_title="Y (m)",
                                    zaxis_title=result.metadata["vertical_reference"], aspectmode="data"))
    return figure


def geology_model_figure(model: GeologyModel):
    import plotly.graph_objects as go
    figure = go.Figure()
    names = ["Top of " + model.strata[0]] + ["Base of " + s for s in model.strata]
    for i, name in enumerate(names):
        figure.add_trace(go.Surface(x=model.x, y=model.y, z=model.interfaces[i],
                                   name=name, showscale=False, opacity=0.65, showlegend=True,
                                   hovertemplate=name + "<br>X=%{x}<br>Y=%{y}<br>Z=%{z}<extra></extra>"))
    figure.update_layout(title=f"Interpreted geological interfaces · revision {model.metadata['revision']}",
                         scene=dict(xaxis_title="X (m)", yaxis_title="Y (m)",
                                    zaxis_title=model.metadata["vertical_reference"], aspectmode="data"))
    return figure


def geology_slice_figure(model: GeologyModel, y: float | None = None):
    import plotly.graph_objects as go
    index = model.y.shape[1] // 2 if y is None else int(np.argmin(abs(model.y[0] - float(y))))
    figure = go.Figure()
    names = ["Top of " + model.strata[0]] + ["Base of " + s for s in model.strata]
    for k, name in enumerate(names):
        figure.add_trace(go.Scatter(x=model.x[:, index], y=model.interfaces[k, :, index],
                                   mode="lines", fill=None if k == 0 else "tonexty", name=name,
                                   connectgaps=False))
    figure.update_layout(title=f"Interpreted geological slice · Y={model.y[0, index]:g} m · revision {model.metadata['revision']}",
                         xaxis_title="X (m)", yaxis_title=model.metadata["vertical_reference"])
    return figure
