"""Explicit compatible groups; never average distinct sampling events."""
from __future__ import annotations

import pandas as pd

COMPATIBILITY = [
    "parameter", "medium", "unit", "basis", "method", "reference_electrode",
    "temperature_compensated", "comparison_temperature",
]
EVENT = ["site", "date", "batch", "depth_top", "depth_bottom"]


def valid_observations(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    return frame.loc[frame["valid"].fillna(False) & frame["value"].notna()].copy()


def groups(frame: pd.DataFrame, columns: list[str]):
    """Yield stable (metadata, rows) groups, including explicitly missing metadata."""
    keys = [key for key in columns if key in frame.columns]
    if frame.empty:
        return
    if not keys:
        yield {}, frame.copy()
        return
    for values, rows in frame.groupby(keys, dropna=False, sort=True):
        values = values if isinstance(values, tuple) else (values,)
        yield dict(zip(keys, values)), rows.copy()


def compatible_groups(frame: pd.DataFrame):
    return groups(valid_observations(frame), ["site", *COMPATIBILITY])


def layer_groups(frame: pd.DataFrame):
    return groups(valid_observations(frame), [*COMPATIBILITY, *EVENT])


def describe_group(metadata: dict) -> str:
    return " · ".join(f"{key}={value}" for key, value in metadata.items() if not pd.isna(value) and str(value))


def filter_observations(frame: pd.DataFrame, *, site=None, point_ids=None,
                        medium=None, parameter=None, batch=None,
                        date_start=None, date_end=None,
                        depth_top=None, depth_bottom=None) -> pd.DataFrame:
    """Depth selection includes only wholly contained intervals, without splitting."""
    result = frame.copy()
    for name, value in [("site", site), ("medium", medium), ("parameter", parameter), ("batch", batch)]:
        if value is not None:
            result = result.loc[result[name].eq(value)]
    if point_ids is not None:
        result = result.loc[result["point_id"].isin(point_ids)]
    if date_start is not None:
        result = result.loc[result["date"] >= pd.Timestamp(date_start)]
    if date_end is not None:
        result = result.loc[result["date"] < pd.Timestamp(date_end).normalize() + pd.Timedelta(days=1)]
    if depth_top is not None:
        result = result.loc[result["depth_top"] >= depth_top]
    if depth_bottom is not None:
        result = result.loc[result["depth_bottom"] <= depth_bottom]
    return result.copy()


def require_metric_crs(frame: pd.DataFrame):
    """Reject mixed/unknown/angular CRS for metre-based distances; no silent reprojection."""
    from pyproj import CRS
    values = frame.get("crs", pd.Series(dtype=str)).dropna().astype(str).unique()
    if len(values) != 1 or frame.get("crs", pd.Series(dtype=str)).isna().any():
        raise ValueError("空間運算需要單一、已知的公尺座標系統；請補齊CRS並分開不同座標系統。")
    if values[0] == "LOCAL_METRIC":
        return
    try:
        crs = CRS.from_user_input(values[0])
    except Exception as exc:
        raise ValueError("無法識別座標系統；工程公尺座標請明確設定 LOCAL_METRIC。") from exc
    if not crs.is_projected or any(abs(a.unit_conversion_factor - 1) > 1e-9 for a in crs.axis_info[:2]):
        raise ValueError("請先將座標轉為以公尺為單位的投影座標系統，再執行距離與內插運算。")


def attach_strata(observations: pd.DataFrame, geology: pd.DataFrame) -> pd.DataFrame:
    """Assign only a unique fully enclosing logged stratum; spanning samples stay unknown."""
    result = observations.copy()
    result["stratum"] = pd.NA
    for index, row in result.iterrows():
        choices = geology.loc[geology.valid & geology.point_id.eq(row.point_id)
                              & geology.depth_top.le(row.depth_top)
                              & geology.depth_bottom.ge(row.depth_bottom)]
        if len(choices) == 1:
            result.at[index, "stratum"] = choices.stratum.iloc[0]
    return result
