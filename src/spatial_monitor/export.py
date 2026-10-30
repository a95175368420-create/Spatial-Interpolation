"""Offline, traceable result bundles without changing source files."""
from __future__ import annotations

import hashlib
import importlib.metadata
import io
import json
from pathlib import Path
import zipfile

import numpy as np
import pandas as pd
import yaml

from . import __version__
from .selection import compatible_groups, layer_groups, describe_group, require_metric_crs
from .interpolation import interpolate_layer
from .plotting import map_figure, trend_figure, profile_figure, borehole_figure, section_figure, scene_figure, export_map_png


def _csv(frame):
    return frame.to_csv(index=False, lineterminator="\n").encode("utf-8-sig")


def _default_section(points):
    """Use the farthest measured point pair; encompass all points in its corridor.

    This is a reproducible display orientation, not an inferred geological strike.
    Candidate endpoints lie on the convex hull; a streaming search avoids an N²
    distance matrix. Collinear and two-point cases need no Qhull call.
    """
    from scipy.spatial import ConvexHull
    frame = points.sort_values("point_id").drop_duplicates(["x", "y"])
    if len(frame) < 2:
        return None
    xy = frame[["x", "y"]].to_numpy(float)
    if len(xy) > 2 and np.linalg.matrix_rank(xy - xy[0]) == 2:
        candidates = ConvexHull(xy).vertices
    else:
        candidates = np.arange(len(xy))
    best, pair = -1., (0, 1)
    for i in candidates:
        distances = np.square(xy[candidates] - xy[i]).sum(axis=1)
        j = int(candidates[int(np.argmax(distances))])
        if distances.max() > best:
            best, pair = float(distances.max()), (int(i), j)
    start, end = xy[pair[0]], xy[pair[1]]
    direction = (end - start) / np.sqrt(best)
    offsets = np.abs((xy[:, 0] - start[0]) * direction[1] - (xy[:, 1] - start[1]) * direction[0])
    tolerance = max(float(offsets.max()) + 1e-6, .1)
    return {"start": start.tolist(), "end": end.tolist(), "tolerance": tolerance,
            "start_point": str(frame.iloc[pair[0]].point_id),
            "end_point": str(frame.iloc[pair[1]].point_id),
            "selection": "Farthest measured point pair; corridor includes all event points; no interpreted contacts"}


def _serializable_metadata(metadata):
    return {key: None if pd.isna(value) else value.isoformat() if isinstance(value, pd.Timestamp)
            else value.item() if isinstance(value, np.generic) else value for key, value in metadata.items()}


def export_dataset(dataset, config, output, *, max_maps=200):
    """Generate all valid layer maps. Refuse overwrites and excessive unbounded exports."""
    output = Path(output)
    if output.exists() and any(output.iterdir()):
        raise ValueError("Output directory must be empty; use a new run directory to preserve existing results.")
    groups = list(compatible_groups(dataset.observations))
    count = sum(len(list(layer_groups(rows))) for _, rows in groups)
    if count > max_maps:
        raise ValueError(f"Export contains {count} maps, exceeding max_maps={max_maps}; explicitly increase the limit.")
    output.mkdir(parents=True, exist_ok=True)
    records = []
    sections = []
    hashes = {}
    for name in ["observations", "points", "geology", "water_levels", "issues"]:
        raw = _csv(getattr(dataset, name))
        (output / f"{name}.csv").write_bytes(raw)
        hashes[name] = hashlib.sha256(raw).hexdigest()
    (output / "config.yaml").write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=True), encoding="utf-8")
    (output / "import.json").write_text(json.dumps(dataset.metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    versions = {p: importlib.metadata.version(p) for p in ["numpy", "pandas", "scipy", "plotly", "openpyxl", "matplotlib", "pyproj"]}
    figures = output / "figures"
    figures.mkdir()

    def save(fig, filename):
        fig.write_html(figures / f"{filename}.html", include_plotlyjs="directory", full_html=True, div_id=filename)

    for gindex, (metadata, rows) in enumerate(groups):
        parameter = rows.parameter.iloc[0]
        definition = {**config["parameters"][parameter], "parameter": parameter}
        bounds = (float(rows.x.min()), float(rows.x.max()), float(rows.y.min()), float(rows.y.max()))
        scale = (float(rows.value.min()), float(rows.value.max()))
        save(trend_figure(rows, definition), f"group-{gindex:03d}-trend")
        save(profile_figure(rows, definition), f"group-{gindex:03d}-profile")
        for lindex, (layer, values) in enumerate(layer_groups(rows)):
            stem = f"group-{gindex:03d}-layer-{lindex:03d}"
            grid = None
            reason = None
            try:
                require_metric_crs(values)
                grid = interpolate_layer(values, definition, config["interpolation"], bounds=bounds)
                reason = grid.reason
            except ValueError as exc:
                reason = str(exc)
            save(map_figure(values, definition, grid=grid, bounds=bounds, color_range=scale), stem)
            export_map_png(values, definition, figures / f"{stem}.png", grid=grid, bounds=bounds, color_range=scale)
            stats = {**layer, "count": len(values), "minimum": values.value.min(), "maximum": values.value.max(),
                     "mean": values.value.mean(), "median": values.value.median(), "figure": f"figures/{stem}.html",
                     "interpolation": "estimated" if grid is not None and grid.available else "measured-only",
                     "reason": reason or ""}
            records.append(stats)
        # Preserve distinct dates/batches in separately named 3D measured views.
        for eindex, (event_key, event) in enumerate(rows.groupby(["date", "batch"], dropna=False, sort=True)):
            points = dataset.points[dataset.points.point_id.isin(event.point_id) & dataset.points.valid]
            geology = dataset.geology[dataset.geology.point_id.isin(points.point_id)]
            try:
                require_metric_crs(points)
            except ValueError:
                continue  # Metre-CRS issue is recorded for every corresponding layer above.
            water = dataset.water_levels.loc[
                dataset.water_levels.point_id.isin(points.point_id)
                & dataset.water_levels.date.eq(event_key[0])
                & dataset.water_levels.valid
            ]
            save(scene_figure(event, geology, points, definition, water_levels=water), f"group-{gindex:03d}-event-{eindex:03d}-3d")
            orientation = _default_section(points) if not geology.empty else None
            if orientation is not None:
                stem = f"group-{gindex:03d}-event-{eindex:03d}-section"
                save(section_figure(geology, points, orientation["start"], orientation["end"],
                                    orientation["tolerance"], observations=event,
                                    parameter_config=definition), stem)
                sections.append({**_serializable_metadata(metadata),
                                 "date": pd.Timestamp(event_key[0]).isoformat(), "batch": str(event_key[1]),
                                 **orientation, "figure": f"figures/{stem}.html"})
    geology = dataset.geology
    if not geology.empty:
        save(borehole_figure(geology, dataset.points.loc[dataset.points.valid]), "boreholes")
    summary = pd.DataFrame(records)
    (output / "layer_statistics.csv").write_bytes(_csv(summary))
    (output / "sections.json").write_text(json.dumps(sections, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest = {"software": "spatial-monitor", "version": __version__, "libraries": versions,
                "data_sha256": hashes, "maps": len(records), "observations": len(dataset.observations),
                "valid_observations": int(dataset.observations.valid.sum()),
                "sections": len(sections), "section_reference": "sections.json",
                "config_sha256": hashlib.sha256((output / "config.yaml").read_bytes()).hexdigest(),
                "assumptions": ["Depths and projected coordinates use metres.",
                                "Maps retain exact sampling intervals, dates, batches and compatible metadata.",
                                "IDW display mask is not a validated prediction radius.",
                                "Missing estimates are not zero concentrations.",
                                "3D figures show measured intervals, not continuous volume estimates.",
                                "Section orientations use the farthest event point pair, not interpreted geological strike.",
                                "Groundwater overlays match each measured scene's points and exact sampling date.",
                                "Optional models are exported separately after explicit activation."]}
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "README.txt").write_text(
        "環境監測成果\n\nobservations.csv保留原始值、標準化值及來源；issues.csv列出檢核結果。\n"
        "layer_statistics.csv對應每張圖與資料組別；figures內HTML可離線互動，PNG可供報告使用。\n"
        "sections.json記錄各事件預設剖面的端點與納入距離；方向取最遠點位對，未推論地層連接。\n"
        "請將HTML與同目錄plotly.min.js一併保留。實測與推估分開標示，空白不表示無污染。\n"
        "資料按介質、單位、方法、量測基準、採樣批次及區間分組，未自動平均跨層或跨批次資料。\n"
        "config.yaml保存參數與內插設定；manifest.json保存標準化輸入與設定指紋及套件版本。\n",
        encoding="utf-8")
    return manifest


def zip_outputs(directory):
    """Stable ZIP metadata and CRC verification, preserving all report assets."""
    folder = Path(directory)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for file in sorted(folder.rglob("*")):
            if file.is_file():
                info = zipfile.ZipInfo(str(file.relative_to(folder)), date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, file.read_bytes())
    with zipfile.ZipFile(buffer) as archive:
        if archive.testzip() is not None:
            raise ValueError("Result archive failed CRC validation")
    return buffer.getvalue()


def volume_csv(result):
    return _csv(pd.DataFrame({"x": np.asarray(result.x).ravel(), "y": np.asarray(result.y).ravel(),
                             "z": np.asarray(result.z).ravel(), "estimate": np.asarray(result.value).ravel(),
                             "supported": np.asarray(result.mask).ravel()}))


def geology_model_csv(model):
    values = {"x": np.asarray(model.x).ravel(), "y": np.asarray(model.y).ravel(), "supported": np.asarray(model.mask).ravel()}
    for index, interface in enumerate(model.interfaces):
        values[f"interface_{index}"] = np.asarray(interface).ravel()
    return _csv(pd.DataFrame(values))
