"""Inspectable 2D/3D views of measurements and explicitly labeled estimates."""
from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.colors import sample_colorscale
from plotly.subplots import make_subplots

from .interpolation import GridResult, valid_observations


PALETTE = ["#477998", "#ae8f67", "#78a876", "#ad756b", "#9b87b5", "#c7ac55", "#5e9e9a", "#c6819d", "#8a959e", "#b46b43"]


def _base(figure: go.Figure, title: str) -> go.Figure:
    figure.update_layout(
        template="plotly_white", title=dict(text=title, y=.98, yanchor="top"),
        legend=dict(groupclick="togglegroup", orientation="h", x=0, xanchor="left",
                    y=1.02, yanchor="bottom", maxheight=65),
        margin=dict(l=60, r=65, t=135, b=90), height=620,
    )
    return figure


def _empty(title: str, message: str = "No valid observations for this selection.") -> go.Figure:
    fig = _base(go.Figure(), title)
    fig.add_annotation(text=message, x=.5, y=.5, xref="paper", yref="paper", showarrow=False)
    return fig


def _title(config: dict, data: pd.DataFrame) -> str:
    label = config.get("label") or (str(data.parameter.iloc[0]) if "parameter" in data and len(data) else "Value")
    unit = str(data.unit.iloc[0]) if "unit" in data and len(data) else str(config.get("unit", ""))
    return f"{label} ({unit})" if unit and unit not in {"1", "-", "dimensionless", "nan"} else str(label)


def _range(data: pd.DataFrame, color_range=None) -> tuple[float, float]:
    if color_range is not None:
        low, high = map(float, color_range)
    else:
        low, high = float(data.value.min()), float(data.value.max())
    if high == low:
        delta = max(abs(low) * .01, .1)
        low, high = low - delta, high + delta
    return low, high


def _selection_caption(data: pd.DataFrame) -> str:
    parts = []
    for column in ("medium", "date", "batch"):
        if column in data and data[column].nunique() == 1:
            value = data[column].dropna().iloc[0]
            parts.append(str(value.date()) if isinstance(value, pd.Timestamp) else str(value))
    if len(data) and {"depth_top", "depth_bottom"}.issubset(data) and len(data[["depth_top", "depth_bottom"]].drop_duplicates()) == 1:
        parts.append(f"Depth {data.depth_top.iloc[0]:g}–{data.depth_bottom.iloc[0]:g} m")
    return " | ".join(parts)


def _padded_bounds(bounds):
    xmin, xmax, ymin, ymax = bounds
    dx, dy = max((xmax-xmin)*.06, .1), max((ymax-ymin)*.06, .1)
    return (xmin-dx, xmax+dx, ymin-dy, ymax+dy)


def _category_color(label: str) -> str:
    return PALETTE[int(hashlib.sha256(str(label).encode()).hexdigest()[:8], 16) % len(PALETTE)]


def _custom(data: pd.DataFrame) -> np.ndarray:
    columns = ["point_id", "sample_id", "date", "batch", "depth_top", "depth_bottom", "medium", "unit", "source_file", "source_row"]
    return np.column_stack([data.get(c, pd.Series("", index=data.index)).fillna("").astype(str) for c in columns])


HOVER = "Point: %{customdata[0]}<br>Sample: %{customdata[1]}<br>Date: %{customdata[2]}<br>Batch: %{customdata[3]}<br>Depth: %{customdata[4]}–%{customdata[5]} m<br>Medium: %{customdata[6]}<br>Source: %{customdata[8]} row %{customdata[9]}"


def _threshold_overlays(fig: go.Figure, data: pd.DataFrame, config: dict) -> None:
    """Thresholds are shown as configured references, never statutory decisions."""
    for threshold in config.get("thresholds", []):
        kind, value = threshold.get("kind"), threshold.get("value")
        if kind == "upper":
            selected = data.value >= float(value)
        elif kind == "lower":
            selected = data.value <= float(value)
        elif kind == "range":
            selected = (data.value < float(value[0])) | (data.value > float(value[1]))
        else:
            continue
        subset = data.loc[selected]
        if subset.empty:
            continue
        description = f"Configured reference: {threshold.get('label', kind)}; source: {threshold.get('source', '')}; conditions: {threshold.get('conditions', '')}"
        fig.add_trace(go.Scatter(x=subset.x, y=subset.y, mode="markers", marker=dict(size=17, symbol="circle-open", color="#d1493f", line=dict(width=2)), name=description, text=[description] * len(subset), hovertemplate="%{text}<extra></extra>"))


def map_figure(observations: pd.DataFrame, parameter_config: dict, grid: GridResult | None = None,
               bounds=None, color_range=None, geology=None, water_levels=None) -> go.Figure:
    data = valid_observations(observations)
    if data.empty:
        return _empty("Plan distribution")
    title = _title(parameter_config, data)
    low, high = _range(data, color_range)
    scale = parameter_config.get("colorscale", "Viridis")
    fig = _base(go.Figure(), title + " — measured points and supported 2D estimates<br><sup>" + _selection_caption(data) + "</sup>")
    if grid is not None and grid.available:
        fig.add_trace(go.Heatmap(x=grid.x[0], y=grid.y[:, 0], z=grid.z, colorscale=scale, zmin=low, zmax=high, showscale=False, name="2D estimate", hovertemplate="2D estimate<br>X: %{x}<br>Y: %{y}<br>Value: %{z}<extra></extra>", hoverongaps=False, connectgaps=False))
    batches = list(data.get("batch", pd.Series("Measured", index=data.index)).fillna("Unknown").unique())
    symbols = ["circle", "triangle-up", "square", "diamond", "cross"]
    for i, batch in enumerate(batches):
        subset = data.loc[data.batch.fillna("Unknown") == batch] if "batch" in data else data
        fig.add_trace(go.Scatter(x=subset.x, y=subset.y, mode="markers", name=f"Measured · {batch}", customdata=_custom(subset), text=subset.value,
            marker=dict(size=11, symbol=symbols[i % len(symbols)], color=subset.value, colorscale=scale, cmin=low, cmax=high, showscale=i == 0, colorbar=dict(title=title), line=dict(color="#25344a", width=1)),
            hovertemplate=HOVER + "<br>Value: %{text} %{customdata[7]}<extra></extra>"))
    _threshold_overlays(fig, data, parameter_config)
    if geology is not None and len(geology) and {"x", "y", "lithology"}.issubset(geology):
        for lithology, group in geology.groupby("lithology", dropna=False):
            fig.add_trace(go.Scatter(x=group.x, y=group.y, mode="markers", name=f"Geology · {lithology}", legendgroup=f"geology-{lithology}", visible="legendonly", marker=dict(size=20, symbol="square-open", color=_category_color(str(lithology))), text=group.get("description", pd.Series("", index=group.index)), hovertemplate="Geology: " + str(lithology) + "<br>%{text}<extra></extra>"))
    if water_levels is not None and len(water_levels) and {"x", "y", "depth"}.issubset(water_levels):
        labels = [f"Point: {r.get('point_id', '')}<br>Date: {r.get('date', '')}<br>Water depth: {r.depth} m" for _, r in water_levels.iterrows()]
        fig.add_trace(go.Scatter(x=water_levels.x, y=water_levels.y, mode="markers", name="Groundwater observations", marker=dict(symbol="diamond-open", size=15, color="#2685b6"), text=labels, hovertemplate="%{text}<extra></extra>"))
    if grid is not None and grid.reason:
        fig.add_annotation(text=grid.reason, x=0, y=1.24, xref="paper", yref="paper", xanchor="left", showarrow=False, font=dict(size=11))
    fig.add_annotation(text="Blank areas have no estimate; they do not indicate zero or absence of contamination.", x=0, y=-.17, xref="paper", yref="paper", xanchor="left", showarrow=False, font=dict(size=10))
    fig.update_xaxes(title="X (projected coordinate)")
    fig.update_yaxes(title="Y (projected coordinate)", scaleanchor="x", scaleratio=1)
    if bounds:
        padded = _padded_bounds(bounds)
        fig.update_xaxes(range=padded[:2])
        fig.update_yaxes(range=padded[2:])
    return fig


def trend_figure(observations: pd.DataFrame, parameter_config: dict) -> go.Figure:
    data = valid_observations(observations)
    if data.empty or "date" not in data:
        return _empty("Time series")
    fig = _base(go.Figure(), "Time series — " + _title(parameter_config, data))
    keys = [c for c in ("point_id", "depth_top", "depth_bottom", "batch") if c in data]
    for key, group in data.groupby(keys, dropna=False):
        group = group.sort_values("date", kind="stable")
        fig.add_trace(go.Scatter(x=group.date, y=group.value, mode="lines+markers", name=" · ".join(map(str, key if isinstance(key, tuple) else (key,))), customdata=_custom(group), hovertemplate=HOVER + "<br>Value: %{y} %{customdata[7]}<extra></extra>"))
    fig.update_xaxes(title="Sampling date")
    fig.update_yaxes(title=_title(parameter_config, data))
    return fig


def profile_figure(observations: pd.DataFrame, parameter_config: dict) -> go.Figure:
    data = valid_observations(observations)
    if data.empty:
        return _empty("Vertical measured profile")
    fig = _base(go.Figure(), "Measured sample intervals — " + _title(parameter_config, data))
    _add_profiles(fig, data)
    fig.update_xaxes(title=_title(parameter_config, data))
    fig.update_yaxes(title="Depth below ground (m)", autorange="reversed")
    return fig


def _add_profiles(fig, data, row=None, col=None, absolute=False):
    seen = set()
    for _, r in data.iterrows():
        name = f"{r.get('point_id', '')} · {r.get('date', '')} · {r.get('batch', '')}"
        origin = float(r.get("surface_elevation", 0)) if absolute else 0
        ys = [origin - float(r.depth_top), origin - float(r.depth_bottom)] if absolute else [r.depth_top, r.depth_bottom]
        kwargs = dict(row=row, col=col) if row is not None else {}
        fig.add_trace(go.Scatter(x=[r.value, r.value], y=ys, mode="lines+markers", line=dict(width=5, color=_category_color(name)), name=name, legendgroup=name, showlegend=name not in seen, customdata=np.repeat(_custom(pd.DataFrame([r])), 2, axis=0), hovertemplate=HOVER + "<br>Value: %{x} %{customdata[7]}<extra></extra>"), **kwargs)
        seen.add(name)


def _located(frame: pd.DataFrame | None, points: pd.DataFrame, include_invalid=False) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()
    result = frame.copy()
    if "valid" in result and not include_invalid:
        result = result.loc[result.valid.fillna(False).astype(bool)]
    if "point_id" not in result:
        return result.iloc[:0]
    if "valid" in points:
        points = points.loc[points.valid.fillna(False).astype(bool)]
    unique_points = points.loc[~points.duplicated("point_id", keep=False)] if "point_id" in points else pd.DataFrame()
    if unique_points.empty:
        return result.iloc[:0]
    if not unique_points.empty:
        columns = [c for c in ("x", "y", "surface_elevation", "crs") if c in unique_points]
        result = result.merge(unique_points[["point_id", *columns]], on="point_id", how="inner", suffixes=("", "_point"), validate="many_to_one")
        for c in columns:
            if c + "_point" in result:
                result[c] = result[c].fillna(result[c + "_point"])
                result = result.drop(columns=c + "_point")
    return result


def _absolute(*frames) -> bool:
    used = [f for f in frames if f is not None and not f.empty]
    return bool(used) and all("surface_elevation" in f and np.isfinite(pd.to_numeric(f.surface_elevation, errors="coerce")).all() for f in used)


def _geology_segments(geology: pd.DataFrame) -> pd.DataFrame:
    """Preserve valid logs; show unlogged/conflicting intervals as unknown.

    Finite invalid intervals supply only the extent of missing information, never
    a lithology. Invalid coordinates or unpositionable depth intervals remain QA
    findings rather than invented geometry. Nothing is inferred below log extent.
    """
    if geology.empty:
        return geology
    geology = geology.copy()
    for column in ("stratum", "description"):
        if column not in geology:
            geology[column] = ""
    rows = []
    for _, group in geology.groupby("point_id", sort=False):
        group = group.copy()
        group["depth_top"] = pd.to_numeric(group.depth_top, errors="coerce")
        group["depth_bottom"] = pd.to_numeric(group.depth_bottom, errors="coerce")
        group = group.loc[np.isfinite(group[["depth_top", "depth_bottom"]]).all(axis=1) & group.depth_top.ge(0) & group.depth_bottom.gt(group.depth_top)]
        if group.empty:
            continue
        edges = sorted({0., *group.depth_top, *group.depth_bottom})
        for top, bottom in zip(edges[:-1], edges[1:]):
            covering = group.loc[group.depth_top.le(top) & group.depth_bottom.ge(bottom)]
            valid = covering.get("valid", pd.Series(True, index=covering.index)).fillna(False).astype(bool)
            known = len(covering) == 1 and bool(valid.iloc[0])
            segment = (covering.iloc[0] if len(covering) else group.iloc[0]).to_dict()
            segment.update(depth_top=top, depth_bottom=bottom)
            if not known:
                reason = "Invalid or conflicting source intervals" if len(covering) else "Unlogged interval"
                segment.update(lithology="Unknown", stratum="", description=reason + "; no inferred lithology")
            rows.append(segment)
    return pd.DataFrame(rows, columns=geology.columns)


def borehole_figure(geology: pd.DataFrame, points: pd.DataFrame) -> go.Figure:
    data = _geology_segments(_located(geology, points, include_invalid=True))
    if data.empty:
        return _empty("Geological borehole logs", "No valid geological intervals.")
    absolute = _absolute(data)
    fig = _base(go.Figure(), "Measured geology — " + ("elevation model" if absolute else "relative depth model"))
    for lithology, group in data.groupby("lithology", sort=True):
        origin = group.surface_elevation.astype(float) if absolute else np.zeros(len(group))
        fig.add_trace(go.Bar(x=group.point_id, y=group.depth_bottom - group.depth_top, base=origin - group.depth_bottom, name=str(lithology), marker=dict(color="#e3e5e8" if lithology == "Unknown" else _category_color(str(lithology)), pattern=dict(shape="/" if lithology == "Unknown" else "")), customdata=group[["depth_top", "depth_bottom", "stratum", "description"]].fillna("").astype(str).to_numpy(), hovertemplate="%{x}<br>Depth: %{customdata[0]}–%{customdata[1]} m<br>Stratum: %{customdata[2]}<br>%{customdata[3]}<extra></extra>"))
    fig.update_layout(barmode="overlay")
    fig.update_xaxes(title="Borehole")
    fig.update_yaxes(title="Elevation (m)" if absolute else "Relative elevation (m; surface = 0)")
    return fig


def project_section(points: pd.DataFrame, start, end, tolerance: float) -> pd.DataFrame:
    """Return points inside a finite section corridor with chainage and offset."""
    start, end = np.asarray(start, float), np.asarray(end, float)
    if start.shape != (2,) or end.shape != (2,) or not np.isfinite([*start, *end, tolerance]).all() or tolerance < 0:
        raise ValueError("Section endpoints must be finite XY pairs and tolerance nonnegative.")
    delta = end - start
    length = np.linalg.norm(delta)
    if length == 0:
        raise ValueError("Section start and end must differ.")
    data = points.copy()
    if not {"x", "y"}.issubset(data):
        return data.iloc[:0]
    xy = data[["x", "y"]].to_numpy(float) - start
    data["chainage"] = xy @ (delta / length)
    data["offset"] = np.abs(xy[:, 0] * delta[1] - xy[:, 1] * delta[0]) / length
    return data.loc[(data.chainage >= 0) & (data.chainage <= length) & (data.offset <= tolerance)].sort_values("chainage")


def section_figure(geology: pd.DataFrame, points: pd.DataFrame, start, end, tolerance: float,
                   observations=None, confirmed_contacts=None, parameter_config=None) -> go.Figure:
    projected = project_section(points, start, end, tolerance)
    data = _geology_segments(_located(geology, points, include_invalid=True))
    if data.empty or projected.empty:
        return _empty("Geological section", "No valid logged boreholes within the section corridor.")
    data = data.merge(projected[["point_id", "chainage", "offset"]], on="point_id", how="inner", validate="many_to_one")
    if data.empty:
        return _empty("Geological section", "No valid logged boreholes within the section corridor.")
    obs = valid_observations(_located(observations, points)) if observations is not None else pd.DataFrame()
    if not obs.empty:
        obs = obs.loc[obs.point_id.isin(projected.point_id)]
    absolute = _absolute(data, obs)
    fig = make_subplots(rows=1, cols=2 if len(obs) else 1, shared_yaxes=True, subplot_titles=["Projected geological intervals", "Measured parameter intervals"] if len(obs) else ["Projected geological intervals"])
    width = max(np.linalg.norm(np.asarray(end) - np.asarray(start)) * .025, .1)
    for lithology, group in data.groupby("lithology", sort=True):
        origin = group.surface_elevation.astype(float) if absolute else np.zeros(len(group))
        custom = group[["point_id", "offset", "depth_top", "depth_bottom", "stratum", "description"]].fillna("").astype(str).to_numpy()
        fig.add_trace(go.Bar(x=group.chainage, y=group.depth_bottom - group.depth_top, base=origin - group.depth_bottom, width=width, name=str(lithology), marker_color="#e3e5e8" if lithology == "Unknown" else _category_color(str(lithology)), customdata=custom, hovertemplate="Borehole: %{customdata[0]}<br>Chainage: %{x} m<br>Offset: %{customdata[1]} m<br>Depth: %{customdata[2]}–%{customdata[3]} m<br>Stratum: %{customdata[4]}<br>%{customdata[5]}<extra></extra>"), row=1, col=1)
    # Contacts are supplied as {confirmed: true, name, point_ids: [...], depths: [...]}.
    for contact in confirmed_contacts or []:
        if contact.get("confirmed") is not True:
            continue
        ids, depths = contact.get("point_ids", []), contact.get("depths", [])
        if len(ids) != len(depths) or len(ids) < 2:
            raise ValueError("Confirmed contacts need at least two paired point_ids and depths.")
        locations = projected.set_index("point_id")
        if not set(ids).issubset(locations.index):
            continue
        selected = locations.loc[ids]
        depth_values = np.asarray(depths, float)
        if not np.isfinite(depth_values).all() or (depth_values < 0).any():
            raise ValueError("Interpreted contact depths must be finite and nonnegative.")
        z = selected.surface_elevation.to_numpy(float) - depth_values if absolute else -depth_values
        fig.add_trace(go.Scatter(x=selected.chainage, y=z, mode="lines+markers", line=dict(dash="dash"), name=f"Confirmed interpretation · {contact.get('name', 'contact')}", hovertemplate="User-confirmed interpreted contact<extra></extra>"), row=1, col=1)
    if len(obs):
        if absolute:
            _add_profiles(fig, obs, row=1, col=2, absolute=True)
        else:
            relative = obs.copy()
            relative["surface_elevation"] = 0.0
            _add_profiles(fig, relative, row=1, col=2, absolute=True)
        fig.update_xaxes(title=_title(parameter_config or {}, obs), row=1, col=2)
    fig.update_layout(barmode="overlay")
    fig.update_xaxes(title="Chainage along section (m)", row=1, col=1)
    fig.update_yaxes(title="Elevation (m)" if absolute else "Relative elevation (m; surface = 0)", row=1, col=1)
    return _base(fig, "Geological section — " + ("elevation model" if absolute else "relative depth model"))


def scene_figure(observations: pd.DataFrame, geology: pd.DataFrame, points: pd.DataFrame,
                 parameter_config: dict, grids=None, water_levels=None) -> go.Figure:
    data = valid_observations(_located(observations, points))
    rock = _geology_segments(_located(geology, points, include_invalid=True))
    water = _located(water_levels, points)
    for name, frame in (("rock", rock), ("water", water)):
        if not frame.empty and {"x", "y"}.issubset(frame):
            cleaned = frame.loc[np.isfinite(frame[["x", "y"]].astype(float)).all(axis=1)]
        else:
            cleaned = pd.DataFrame()
        if name == "rock":
            rock = cleaned
        else:
            water = cleaned
    if data.empty and rock.empty and water.empty:
        return _empty("3D measured intervals")
    absolute = _absolute(data, rock, water)
    fig = _base(go.Figure(), "3D measured intervals — " + ("elevation model" if absolute else "relative depth model; all surfaces = 0"))
    seen = set()
    for _, r in rock.iterrows():
        label = str(r.lithology)
        origin = float(r.surface_elevation) if absolute else 0
        fig.add_trace(go.Scatter3d(x=[r.x, r.x], y=[r.y, r.y], z=[origin-r.depth_top, origin-r.depth_bottom], mode="lines", line=dict(color="#aaaaaa" if label == "Unknown" else _category_color(label), width=18, dash="dash" if label == "Unknown" else "solid"), opacity=.45, name="Geology · " + label, legendgroup="geology-" + label, showlegend=label not in seen, text=[f"{r.point_id}<br>{label}<br>{r.depth_top}–{r.depth_bottom} m<br>{r.get('description', '')}"] * 2, hovertemplate="%{text}<extra></extra>"))
        seen.add(label)
    if not data.empty:
        low, high = _range(data)
        title, scale = _title(parameter_config, data), parameter_config.get("colorscale", "Viridis")
        for i, (_, r) in enumerate(data.iterrows()):
            origin = float(r.surface_elevation) if absolute else 0
            fig.add_trace(go.Scatter3d(x=[r.x, r.x], y=[r.y, r.y], z=[origin-r.depth_top, origin-r.depth_bottom], mode="lines+markers", line=dict(color=[r.value, r.value], colorscale=scale, cmin=low, cmax=high, width=8), marker=dict(color=[r.value, r.value], colorscale=scale, cmin=low, cmax=high, size=3, showscale=i == 0, colorbar=dict(title=title)), name="Measured · " + title, legendgroup="measurements", showlegend=i == 0, customdata=np.repeat(_custom(pd.DataFrame([r])), 2, axis=0), text=[r.value] * 2, hovertemplate=HOVER + "<br>Value: %{text} %{customdata[7]}<extra></extra>"))
    for i, (_, r) in enumerate(water.iterrows()):
        if not np.isfinite(float(r.get("depth", np.nan))):
            continue
        origin = float(r.surface_elevation) if absolute else 0
        fig.add_trace(go.Scatter3d(x=[r.x], y=[r.y], z=[origin-float(r.depth)], mode="markers", marker=dict(color="#1689c6", size=7, symbol="diamond"), name="Groundwater observations", legendgroup="water", showlegend=i == 0, text=[f"{r.point_id}<br>{r.get('date', '')}<br>Water depth: {r.depth} m"], hovertemplate="%{text}<extra></extra>"))
    # Relative-depth grids can only be mapped to absolute elevation when every
    # measured surface shares one elevation; no undocumented terrain is inferred.
    elevation_parts = [f.surface_elevation for f in (data, rock, water) if not f.empty and "surface_elevation" in f]
    elevations = pd.concat(elevation_parts, ignore_index=True) if elevation_parts else pd.Series(dtype=float)
    stack_origin = float(elevations.iloc[0]) if absolute and elevations.nunique() == 1 else (0 if not absolute else None)
    for entry in grids or []:
        depth, grid = (entry["depth"], entry["grid"]) if isinstance(entry, dict) else entry
        if not grid.available:
            continue
        if stack_origin is None:
            fig.add_annotation(text="2D stacks omitted: variable surface elevations require an explicit terrain reference.", showarrow=False, x=0, y=1.04, xref="paper", yref="paper")
            break
        z = np.where(grid.mask, stack_origin-float(depth), np.nan)
        fig.add_trace(go.Surface(x=grid.x, y=grid.y, z=z, surfacecolor=grid.z, colorscale=parameter_config.get("colorscale", "Viridis"), cmin=low if not data.empty else None, cmax=high if not data.empty else None, showscale=False, opacity=.65, showlegend=True, name=f"2D layer stack · depth {depth} m (not 3D interpolation)", hovertemplate="Stacked 2D estimate<br>X: %{x}<br>Y: %{y}<br>Z: %{z}<br>Value: %{surfacecolor}<extra></extra>"))
    fig.update_layout(scene=dict(xaxis_title="X", yaxis_title="Y", zaxis_title="Elevation (m)" if absolute else "Relative elevation (m)", aspectmode="data"))
    return fig


def export_map_png(observations: pd.DataFrame, parameter_config: dict, path: str | Path,
                   grid: GridResult | None = None, bounds=None, color_range=None) -> Path:
    """Export a standalone static map without a browser or Plotly image server."""
    import os
    import tempfile
    cache = Path(tempfile.gettempdir()) / "spatial-monitor-cache"
    cache.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("MPLCONFIGDIR", str(cache / "matplotlib"))
    os.environ.setdefault("XDG_CACHE_HOME", str(cache))
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    available_fonts = {font.name for font in font_manager.fontManager.ttflist}
    candidates = ["Noto Sans CJK TC", "Noto Sans CJK JP", "Microsoft JhengHei", "PingFang TC"]
    matplotlib.rcParams["font.sans-serif"] = [font for font in candidates if font in available_fonts] + ["DejaVu Sans"]
    data = valid_observations(observations)
    fig, ax = plt.subplots(figsize=(8, 7), constrained_layout=True)
    if not data.empty:
        low, high = _range(data, color_range)
        colors = sample_colorscale(parameter_config.get("colorscale", "Viridis"), np.linspace(0, 1, 256))
        from matplotlib.colors import ListedColormap
        from plotly.colors import unlabel_rgb
        rgb = [tuple(v / 255 for v in unlabel_rgb(color)) for color in colors]
        cmap = ListedColormap(rgb)
        if grid is not None and grid.available:
            ax.pcolormesh(grid.x, grid.y, grid.z, cmap=cmap, vmin=low, vmax=high, shading="auto")
        batches = data.get("batch", pd.Series("Measured", index=data.index)).fillna("Unknown")
        for i, batch in enumerate(batches.unique()):
            subset = data.loc[batches == batch]
            scatter = ax.scatter(subset.x, subset.y, c=subset.value, cmap=cmap, vmin=low, vmax=high, edgecolor="#25344a", s=42, marker=["o", "^", "s", "D", "+"][i % 5], label=f"Measured · {batch}")
        fig.colorbar(scatter, ax=ax, label=_title(parameter_config, data))
        for threshold in parameter_config.get("thresholds", []):
            kind, value = threshold.get("kind"), threshold.get("value")
            if kind == "upper":
                selected = data.value >= float(value)
            elif kind == "lower":
                selected = data.value <= float(value)
            elif kind == "range":
                selected = (data.value < float(value[0])) | (data.value > float(value[1]))
            else:
                continue
            if selected.any():
                subset = data.loc[selected]
                label = f"Reference · {threshold.get('label', kind)} ({threshold.get('source', '')})"
                ax.scatter(subset.x, subset.y, s=100, facecolors="none", edgecolors="#d1493f", linewidths=1.5, label=label)
        for _, r in data.iterrows():
            right = r.x > data.x.mean()
            ax.annotate(str(r.get("point_id", "")), (r.x, r.y), xytext=(-4 if right else 4, 4), textcoords="offset points", fontsize=7, ha="right" if right else "left")
        if len(ax.get_legend_handles_labels()[0]) > 1:
            ax.legend(loc="best", fontsize=8)
    else:
        ax.text(.5, .5, "No valid measurements", ha="center", transform=ax.transAxes)
    if bounds:
        padded = _padded_bounds(bounds)
        ax.set_xlim(padded[:2]); ax.set_ylim(padded[2:])
    ax.set(xlabel="X (projected coordinate)", ylabel="Y (projected coordinate)")
    ax.set_title(_title(parameter_config, data) + "\n" + _selection_caption(data), fontsize=11)
    ax.ticklabel_format(useOffset=False, style="plain")
    ax.tick_params(labelsize=8)
    from matplotlib.ticker import MaxNLocator
    ax.xaxis.set_major_locator(MaxNLocator(nbins=5))
    ax.yaxis.set_major_locator(MaxNLocator(nbins=6))
    ax.set_aspect("equal")
    fig.supxlabel("Blank areas have no estimate. 2D estimates are not 3D interpolation.", fontsize=8)
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(destination, dpi=160)
    plt.close(fig)
    return destination
