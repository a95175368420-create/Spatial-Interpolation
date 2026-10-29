"""Conservative 2D IDW interpolation with explicit spatial support limits.

The nearest-point cutoff masks displayed estimates; it never changes which
observations participate in IDW. No routine in this module interpolates geology.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math

import numpy as np
import pandas as pd
from scipy.spatial import ConvexHull, QhullError, cKDTree
from scipy.spatial.distance import cdist, pdist
from pyproj import CRS


@dataclass
class GridResult:
    x: np.ndarray = field(default_factory=lambda: np.empty((0, 0)))
    y: np.ndarray = field(default_factory=lambda: np.empty((0, 0)))
    z: np.ndarray = field(default_factory=lambda: np.empty((0, 0)))
    mask: np.ndarray = field(default_factory=lambda: np.empty((0, 0), dtype=bool))
    nearest_distance: np.ndarray = field(default_factory=lambda: np.empty((0, 0)))
    reason: str | None = None
    source_count: int = 0

    @property
    def available(self) -> bool:
        return self.reason is None and self.z.size > 0 and bool(np.isfinite(self.z).any())


def valid_observations(observations: pd.DataFrame) -> pd.DataFrame:
    """Return usable numerical measurements without modifying the source table."""
    result = observations.copy()
    if "valid" in result:
        result = result.loc[result["valid"].fillna(False).astype(bool)]
    for column in ("x", "y", "value"):
        if column not in result:
            return result.iloc[:0]
        result[column] = pd.to_numeric(result[column], errors="coerce")
    return result.loc[np.isfinite(result[["x", "y", "value"]]).all(axis=1)].copy()


def filter_layer(observations: pd.DataFrame, top: float, bottom: float) -> pd.DataFrame:
    """Select exact sample intervals; samples spanning layers are not split."""
    if not (math.isfinite(top) and math.isfinite(bottom) and 0 <= top <= bottom):
        raise ValueError("Layer depths must be finite and satisfy 0 <= top <= bottom.")
    if not {"depth_top", "depth_bottom"}.issubset(observations.columns):
        return observations.iloc[:0].copy()
    return observations.loc[
        np.isclose(pd.to_numeric(observations.depth_top, errors="coerce"), top, atol=1e-8, rtol=0)
        & np.isclose(pd.to_numeric(observations.depth_bottom, errors="coerce"), bottom, atol=1e-8, rtol=0)
    ].copy()


def idw_values(xy: np.ndarray, values: np.ndarray, queries: np.ndarray, power: float = 2) -> np.ndarray:
    """All-point IDW, including exact reproduction at sampled coordinates."""
    xy, values, queries = np.asarray(xy, float), np.asarray(values, float), np.asarray(queries, float)
    if not math.isfinite(power) or power <= 0:
        raise ValueError("IDW power must be positive and finite.")
    if xy.ndim != 2 or xy.shape[1] != 2 or len(values) != len(xy) or not len(xy):
        raise ValueError("IDW requires paired XY coordinates and numerical values.")
    result = np.empty(len(queries), dtype=float)
    for offset in range(0, len(queries), 2048):
        distance = cdist(queries[offset:offset + 2048], xy)
        exact = distance == 0
        # Rescale by nearest distance before exponentiation to avoid overflow.
        minimum = np.where(exact.any(axis=1), 1.0, distance.min(axis=1))
        with np.errstate(divide="ignore", over="ignore"):
            weights = (minimum[:, None] / distance) ** power
        weights[exact] = 0
        denominator = weights.sum(axis=1)
        chunk = np.divide(weights @ values, denominator, out=np.zeros(len(distance)), where=denominator > 0)
        hit = exact.any(axis=1)
        chunk[hit] = values[exact[hit].argmax(axis=1)]
        result[offset:offset + len(chunk)] = chunk
    return result


def interpolate_layer(
    observations: pd.DataFrame,
    parameter_config: dict,
    settings: dict,
    bounds: tuple[float, float, float, float] | None = None,
) -> GridResult:
    """Compute one homogeneous interval; bounds are xmin, xmax, ymin, ymax.

    Degenerate geometry and ambiguous selections return ``reason`` and no grid.
    Invalid numerical settings raise ValueError so callers can request correction.
    """
    transform = str(parameter_config.get("transform", "none")).lower()
    if transform not in {"none", "linear", "identity", "log10"}:
        raise ValueError(f"Unsupported value transform: {transform}")
    if parameter_config.get("data_type", parameter_config.get("type", "continuous")) in {"categorical", "category"}:
        raise ValueError("Categorical geology cannot be interpolated as a numerical field.")
    names = set(observations.get("parameter", pd.Series(dtype=str)).astype(str).str.upper())
    names.add(str(parameter_config.get("name", parameter_config.get("label", ""))).upper())
    if transform == "log10" and ({"PH", "ORP"} & names):
        raise ValueError("pH and ORP must not use a concentration log10 transform.")
    data = valid_observations(observations)
    count = len(data)
    def unavailable(reason: str) -> GridResult:
        return GridResult(reason=reason, source_count=count)
    for column in ("parameter", "unit", "medium", "basis", "method", "reference_electrode", "temperature_compensated", "comparison_temperature", "date", "batch", "site", "crs", "depth_top", "depth_bottom"):
        if column in data and data[column].fillna("unknown").nunique() > 1:
            return unavailable(f"Select a single {column}; heterogeneous observations are not merged.")
    if "crs" in data and len(data) and str(data.crs.iloc[0]).upper() != "LOCAL_METRIC":
        try:
            crs = CRS.from_user_input(data.crs.iloc[0])
        except (ValueError, TypeError):
            return unavailable("Metric interpolation requires a known projected CRS.")
        if not crs.is_projected or any(not np.isclose(axis.unit_conversion_factor, 1) for axis in crs.axis_info[:2]):
            return unavailable("Metric interpolation requires a projected CRS in metres.")
    if count < 3:
        return unavailable("Fewer than three valid points; measured points only.")
    xy, values = data[["x", "y"]].to_numpy(float), data.value.to_numpy(float)
    if data.duplicated(["x", "y"]).any():
        return unavailable("Repeated XY coordinates require explicit sample/batch selection; no automatic averaging.")
    if transform == "log10":
        if np.any(values <= 0):
            return unavailable("Log10 interpolation requires strictly positive measurements.")
        values = np.log10(values)
    try:
        hull = ConvexHull(xy)
    except QhullError:
        return unavailable("Points are collinear; measured points only.")
    min_width = float(settings.get("min_width", 2))
    spacing = float(settings.get("grid_spacing", 1))
    max_distance = float(settings.get("max_distance", 10))
    power = float(settings.get("power", 2))
    max_cells = int(settings.get("max_cells", 100000))
    if not all(map(math.isfinite, (spacing, min_width, max_distance, power))) or spacing <= 0 or min_width < 0 or max_distance <= 0 or power <= 0 or max_cells < 1:
        raise ValueError("Grid spacing, distance and power must be positive; min_width nonnegative; max_cells positive.")
    diameter = float(pdist(xy[hull.vertices]).max())
    if hull.volume / diameter < min_width:
        return unavailable("Convex-hull area / diameter is below the configured minimum width; measured points only.")
    xmin, xmax, ymin, ymax = bounds or (xy[:, 0].min(), xy[:, 0].max(), xy[:, 1].min(), xy[:, 1].max())
    if not all(map(math.isfinite, (xmin, xmax, ymin, ymax))) or xmin >= xmax or ymin >= ymax:
        raise ValueError("Bounds must be finite xmin < xmax, ymin < ymax.")
    nx, ny = int(math.floor((xmax - xmin) / spacing)) + 1, int(math.floor((ymax - ymin) / spacing)) + 1
    if nx * ny > max_cells:
        return unavailable(f"Requested grid has {nx * ny:,} cells, exceeding max_cells={max_cells:,}; increase grid spacing.")
    xx, yy = np.meshgrid(xmin + np.arange(nx) * spacing, ymin + np.arange(ny) * spacing)
    query = np.column_stack([xx.ravel(), yy.ravel()])
    inside = np.all(query @ hull.equations[:, :2].T + hull.equations[:, 2] <= 1e-8, axis=1)
    nearest = cKDTree(xy).query(query)[0]
    mask = inside & (nearest <= max_distance)
    estimates = np.full(len(query), np.nan)
    estimates[mask] = idw_values(xy, values, query[mask], power)
    if transform == "log10":
        estimates[mask] = 10 ** estimates[mask]
    reason = None if mask.any() else "No grid cells satisfy the configured spatial support limits."
    return GridResult(xx, yy, estimates.reshape(xx.shape), mask.reshape(xx.shape), nearest.reshape(xx.shape), reason, count)
