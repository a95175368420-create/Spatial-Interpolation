"""Read CSV bundles / Excel workbooks and retain findings without silent imputation."""
from pathlib import Path
import re

import numpy as np
import pandas as pd

from .config import load_config
from .data import Dataset


FIELDS = {
    "samples": ["sample_id", "point_id", "date", "medium", "depth_top", "depth_bottom", "batch", "site"],
    "analyses": ["sample_id", "parameter", "value", "unit", "method", "basis", "temperature",
                 "temperature_compensated", "reference_temperature", "reference_electrode", "calibration"],
    "points": ["point_id", "x", "y", "crs", "surface_elevation", "site"],
    "geology": ["point_id", "depth_top", "depth_bottom", "lithology", "stratum", "description"],
    "water_levels": ["point_id", "date", "depth"],
}
REQUIRED = {
    "samples": ["sample_id", "point_id", "date", "medium", "depth_top", "depth_bottom"],
    "analyses": ["sample_id", "parameter", "value", "unit"],
    "points": ["point_id", "x", "y"],
    "geology": ["point_id", "depth_top", "depth_bottom", "lithology"],
    "water_levels": ["point_id", "date", "depth"],
}
MEDIUM = {"土壤": "soil", "水": "water", "地下水": "groundwater", "地表水": "surface_water",
          "空氣": "air", "surface water": "surface_water"}


def _unit(value):
    value = str(value).strip().replace("µ", "u").replace("μ", "u")
    return {"": "1", "-": "1", "pH": "1", "dimensionless": "1", "°C": "degC",
            "℃": "degC", "percent": "%"}.get(value, value)


def conversion_factor(source, target, medium):
    """Convert compatible units only; aqueous ppm is not assumed to be mg/L."""
    source, target = _unit(source), _unit(target)
    if source == target:
        return 1.0
    factors = {("mS/cm", "uS/cm"): 1000., ("uS/cm", "mS/cm"): .001,
               ("S/m", "uS/cm"): 10000., ("uS/cm", "S/m"): .0001,
               ("ug/L", "mg/L"): .001, ("mg/L", "ug/L"): 1000.,
               ("ug/kg", "mg/kg"): .001, ("mg/kg", "ug/kg"): 1000.,
               ("%", "mg/kg"): 10000., ("mg/kg", "%"): .0001,
               ("V", "mV"): 1000., ("mV", "V"): .001}
    if source == "ppm" and target == "mg/kg" and medium == "soil":
        return 1.0
    if (source, target) not in factors:
        raise ValueError(f"Incompatible or unrecognized units: {source} → {target}")
    return factors[(source, target)]


def _read_tables(path, config, issues):
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)
    workbook = pd.ExcelFile(path) if path.is_file() and path.suffix.lower() == ".xlsx" else None
    if path.is_file() and workbook is None:
        raise ValueError("Use an .xlsx workbook or a directory containing CSV tables")
    tables = {}
    try:
        for name, fields in FIELDS.items():
            settings = config["tables"].get(name, {})
            filename, sheet = settings.get("filename", f"{name}.csv"), settings.get("sheet", name)
            source = path / filename if path.is_dir() else path
            if path.is_dir() and not source.resolve().is_relative_to(path.resolve()):
                raise ValueError(f"Table {name}: CSV filename must stay inside the selected data directory")
            present = source.is_file() if workbook is None else sheet in workbook.sheet_names
            if not present:
                if name in ("samples", "analyses", "points"):
                    raise ValueError(f"Missing required table: {name}")
                frame = pd.DataFrame(columns=fields)
            else:
                if workbook is None:
                    frame = pd.read_csv(source, dtype=str, keep_default_na=False, encoding="utf-8-sig")
                else:
                    frame = pd.read_excel(workbook, sheet_name=sheet, dtype=str, keep_default_na=False)
                frame = frame.rename(columns=settings.get("columns", {}))
                if frame.columns.duplicated().any():
                    raise ValueError(f"{name}: column mapping creates duplicate columns")
                missing_required = []
                for field in REQUIRED[name]:
                    if field not in frame:
                        missing_required.append(field)
                        issues.append(dict(severity="error", table=name, row=None,
                                           code="missing_column", message=f"Missing column: {field}"))
                for field in fields:
                    if field not in frame:
                        frame[field] = ""
                frame = frame.fillna("")
                frame.attrs["missing_required"] = missing_required
            # Workbook row numbers include the header, allowing direct source lookup.
            frame["source_file"] = f"{path.name}:{sheet}" if workbook else source.name
            frame["source_row"] = np.arange(len(frame)) + 2
            tables[name] = frame
    finally:
        if workbook is not None:
            workbook.close()
    return tables


def load_dataset(path, config=None):
    """Load a workbook or CSV bundle. Invalid source records remain inspectable.

    Valid means usable for numeric/spatial analysis, not regulatory compliance.
    Below-detection results are retained with their limit and excluded from numeric
    estimates. No substitution or replicate averaging is performed.
    """
    config = load_config(config)
    issues = []
    tables = _read_tables(path, config, issues)

    def issue(table, index, code, message, severity="error", invalidate=True):
        frame = tables[table]
        issues.append(dict(severity=severity, table=table,
                           row=int(frame.at[index, "source_row"]), code=code, message=message))
        if invalidate:
            frame.at[index, "valid"] = False

    for frame in tables.values():
        frame["valid"] = not bool(frame.attrs.get("missing_required"))
        for field in ("sample_id", "point_id", "parameter", "medium", "unit", "site", "basis",
                      "method", "reference_electrode", "temperature_compensated", "batch", "crs"):
            if field in frame:
                frame[field] = frame[field].astype(str).str.strip()

    def require_ids(table, field):
        frame = tables[table]
        bad = frame[field].eq("") | frame[field].duplicated(keep=False)
        for index in frame.index[bad]:
            issue(table, index, "invalid_identifier", f"Missing or duplicate {field}: {frame.at[index, field]}")

    def numeric(table, columns, optional=()):
        frame = tables[table]
        for col in columns:
            original = frame[col].copy()
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
            bad = ~np.isfinite(frame[col])
            if col in optional:
                bad &= original.astype(str).str.strip().ne("")
            for index in frame.index[bad]:
                issue(table, index, "invalid_number", f"{col}: {original.loc[index]}")

    def dates(table):
        frame = tables[table]
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce", format="mixed", utc=True).dt.tz_localize(None)
        for index in frame.index[frame["date"].isna()]:
            issue(table, index, "invalid_date", "Missing or unparseable date")

    def intervals(table):
        frame = tables[table]
        numeric(table, ["depth_top", "depth_bottom"])
        bad = frame.depth_top.lt(0) | frame.depth_bottom.lt(frame.depth_top)
        if table == "geology":
            bad |= frame.depth_bottom.eq(frame.depth_top)
        for index in frame.index[bad]:
            issue(table, index, "invalid_interval", "Depth must increase downward; interval is invalid")

    points = tables["points"]
    require_ids("points", "point_id")
    numeric("points", ["x", "y", "surface_elevation"], optional=["surface_elevation"])
    points["crs"] = points.crs.mask(points.crs.eq(""), config.get("crs") or "")
    for index in points.index[points.crs.eq("")]:
        issue("points", index, "missing_crs", "A coordinate reference system is required")
    crs_values = points.loc[points.valid, "crs"].unique()
    if len(crs_values) > 1:
        for index in points.index[points.valid]:
            issue("points", index, "mixed_crs", "Transform all coordinates to one CRS before combining")

    samples = tables["samples"]
    require_ids("samples", "sample_id")
    intervals("samples")
    dates("samples")
    samples["medium"] = samples.medium.str.lower().replace(MEDIUM)
    # Only unique IDs may participate in joins; a duplicate never chooses a winner.
    unique_points = points[~points.point_id.duplicated(keep=False) & points.point_id.ne("")].set_index("point_id")
    for index, row in samples.iterrows():
        if row.point_id not in unique_points.index:
            issue("samples", index, "unresolved_point", f"Missing or ambiguous point: {row.point_id}")
        elif not unique_points.at[row.point_id, "valid"]:
            issue("samples", index, "invalid_point", f"Point has validation errors: {row.point_id}")
        if not row.medium:
            issue("samples", index, "missing_medium", "A medium is required")
    unique_samples = samples[~samples.sample_id.duplicated(keep=False) & samples.sample_id.ne("")].set_index("sample_id")

    analyses = tables["analyses"]
    analyses["raw_value"] = analyses.value.copy()
    analyses["raw_unit"] = analyses.unit.copy()
    analyses["raw_parameter"] = analyses.parameter.copy()
    analyses["raw_temperature"] = analyses.temperature.copy()
    analyses["raw_reference_temperature"] = analyses.reference_temperature.copy()
    analyses["comparison_temperature"] = np.nan
    analyses["qualifier"] = ""
    analyses["detection_limit"] = np.nan
    analyses["value"] = np.nan
    # Keep original columns and append canonical sample/point metadata.
    for col in ("point_id", "date", "medium", "depth_top", "depth_bottom", "batch", "site",
                "x", "y", "crs", "surface_elevation"):
        if col in analyses:
            analyses[f"input_{col}"] = analyses[col].copy()
        analyses[col] = pd.Series(index=analyses.index, dtype="object")
    for index, row in analyses.iterrows():
        if row.sample_id not in unique_samples.index:
            issue("analyses", index, "unresolved_sample", f"Missing or ambiguous sample: {row.sample_id}")
            continue
        sample = unique_samples.loc[row.sample_id]
        if "input_point_id" in row and row.input_point_id and row.input_point_id != sample.point_id:
            issue("analyses", index, "point_mismatch", "Analysis point and sample point disagree")
        if "input_date" in row and str(row.input_date).strip():
            analysis_date = pd.to_datetime(row.input_date, errors="coerce", utc=True)
            if pd.isna(analysis_date) or analysis_date.tz_localize(None) != sample.date:
                issue("analyses", index, "date_mismatch", "Analysis date differs from sample date; original retained in input_date", "warning", False)
        for col in ("point_id", "date", "medium", "depth_top", "depth_bottom", "batch", "site"):
            analyses.at[index, col] = sample[col]
        if not sample.valid:
            issue("analyses", index, "invalid_sample", f"Sample has validation errors: {row.sample_id}")
        if sample.point_id in unique_points.index:
            point = unique_points.loc[sample.point_id]
            for col in ("x", "y", "crs", "surface_elevation"):
                analyses.at[index, col] = point[col]
            if sample.site and point.site and sample.site != point.site:
                issue("analyses", index, "site_mismatch", "Sample and point site identifiers disagree")
            if not sample.site:
                analyses.at[index, "site"] = point.site
        parameter = row.parameter
        if parameter == "DO" and _unit(row.unit) == "%":
            parameter = "DO_SAT"
            analyses.at[index, "parameter"] = parameter
        definition = config["parameters"].get(parameter)
        if definition is None:
            issue("analyses", index, "unknown_parameter", f"Parameter requires configuration: {parameter}")
            continue
        medium = sample.medium
        if definition.get("media") and medium not in definition["media"]:
            issue("analyses", index, "incompatible_medium", f"{parameter} is not configured for {medium}")
        target = definition.get("unit_by_medium", {}).get(medium, definition["unit"])
        try:
            factor = conversion_factor(row.unit, target, medium)
        except ValueError as error:
            issue("analyses", index, "incompatible_unit", str(error))
            continue
        analyses.at[index, "unit"] = target
        if factor != 1:
            issue("analyses", index, "unit_converted", f"{row.unit} → {target}; factor {factor:g}", "info", False)
        raw = str(row.raw_value).strip()
        match = re.fullmatch(r"([<>]=?|≤|≥)\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)", raw)
        if match:
            analyses.at[index, "qualifier"] = match[1]
            analyses.at[index, "detection_limit"] = float(match[2]) * factor
            issue("analyses", index, "censored_value", "Censored result retained without numeric substitution", "info")
            continue
        if raw.upper() in ("ND", "N.D.", "NON-DETECT", "未檢出"):
            analyses.at[index, "qualifier"] = "ND"
            issue("analyses", index, "censored_value", "Non-detect retained without numeric substitution", "info")
            continue
        try:
            value = float(raw) * factor
            if not np.isfinite(value):
                raise ValueError()
        except ValueError:
            issue("analyses", index, "invalid_value", f"Result is not a finite number: {raw}")
            continue
        analyses.at[index, "value"] = value
        limits = definition.get("valid_range")
        if limits and ((limits[0] is not None and value < limits[0]) or (limits[1] is not None and value > limits[1])):
            issue("analyses", index, "outside_valid_range", f"{value:g} lies outside configured plausible range {limits}")
        if definition.get("transform") == "log10" and value <= 0:
            issue("analyses", index, "nonpositive_log_value", "log10 requires a positive result")
        if parameter == "TOC" and medium == "soil" and not row.basis:
            issue("analyses", index, "missing_basis", "Soil TOC requires a dry/wet mass basis")
        for meta in ({"EC": ["temperature", "temperature_compensated"],
                      "ORP": ["reference_electrode", "calibration"]}.get(parameter, [])):
            if not str(row[meta]).strip():
                issue("analyses", index, "missing_measurement_metadata", f"{parameter}: {meta} is unspecified", "warning", False)
        if parameter == "EC":
            status = str(row.temperature_compensated).strip().lower()
            compensated = status in ("true", "yes", "1", "compensated", "corrected", "已補償", "已校正") or bool(re.fullmatch(r"[+-]?\d+(?:\.\d+)?\s*°?c", status))
            uncompensated = status in ("", "false", "no", "0", "none", "unknown", "unspecified", "na", "n/a", "未補償", "未校正")
            if not compensated and not uncompensated:
                issue("analyses", index, "unrecognized_compensation", "EC compensation status is unrecognized; group by measured temperature", "warning", False)
            temperature_field = "reference_temperature" if compensated else "temperature"
            compare = pd.to_numeric(row[temperature_field], errors="coerce")
            if pd.isna(compare) or not np.isfinite(compare):
                issue("analyses", index, "missing_comparison_temperature", f"EC comparison requires {temperature_field}; unknown temperatures remain a separate group", "warning", False)
            else:
                analyses.at[index, "comparison_temperature"] = float(compare)
    duplicate_keys = ["sample_id", "parameter", "method", "basis", "reference_electrode", "temperature_compensated"]
    for index in analyses.index[analyses.duplicated(duplicate_keys, keep=False)]:
        issue("analyses", index, "duplicate_measurement", "Repeated measurement identity; resolve replicates explicitly")
    analyses["date"] = pd.to_datetime(analyses["date"], errors="coerce")
    for col in ("x", "y", "depth_top", "depth_bottom", "surface_elevation", "temperature", "reference_temperature"):
        analyses[col] = pd.to_numeric(analyses[col], errors="coerce")
    for index in analyses.index[analyses.raw_temperature.astype(str).str.strip().ne("") & ~np.isfinite(analyses.temperature)]:
        issue("analyses", index, "invalid_measurement_metadata", "temperature is not finite; original retained in raw_temperature", "warning", False)
    for index in analyses.index[analyses.raw_reference_temperature.astype(str).str.strip().ne("") & ~np.isfinite(analyses.reference_temperature)]:
        issue("analyses", index, "invalid_measurement_metadata", "reference_temperature is not finite; original retained in raw_reference_temperature", "warning", False)

    geology = tables["geology"]
    intervals("geology")
    for index, row in geology.iterrows():
        if not str(row.lithology).strip():
            issue("geology", index, "missing_lithology", "Lithology is required; an unknown interval stays unknown")
    for point_id, group in geology.groupby("point_id", sort=False):
        previous = []
        end = None
        for index, row in group.sort_values("depth_top").iterrows():
            if not np.isfinite(row.depth_top) or not np.isfinite(row.depth_bottom):
                continue
            overlapping = [j for j in previous if geology.at[j, "depth_bottom"] > row.depth_top]
            if overlapping:
                issue("geology", index, "overlapping_interval", f"Overlapping lithology intervals at {point_id}")
                for other in overlapping:
                    if geology.at[other, "valid"]:
                        issue("geology", other, "overlapping_interval", f"Overlapping lithology intervals at {point_id}")
            elif row.depth_top > (end if end is not None else 0):
                issue("geology", index, "geology_gap", f"Unknown interval {end or 0:g}–{row.depth_top:g} m at {point_id}", "warning", False)
            end = max(end, row.depth_bottom) if end is not None else row.depth_bottom
            previous.append(index)

    water = tables["water_levels"]
    numeric("water_levels", ["depth"])
    dates("water_levels")
    for index in water.index[water.duplicated(["point_id", "date"], keep=False)]:
        issue("water_levels", index, "duplicate_water_level", "Multiple water levels at the same point and time")
    # Negative water depth is permitted: a measured head can stand above ground.
    for table in ("geology", "water_levels"):
        frame = tables[table]
        for col in ("x", "y", "surface_elevation", "crs", "site"):
            frame[col] = pd.Series(index=frame.index, dtype="object")
        for index, row in frame.iterrows():
            if row.point_id not in unique_points.index:
                issue(table, index, "unresolved_point", f"Missing or ambiguous point: {row.point_id}")
            else:
                point = unique_points.loc[row.point_id]
                if not point.valid:
                    issue(table, index, "invalid_point", f"Point has validation errors: {row.point_id}")
                for col in ("x", "y", "surface_elevation", "crs", "site"):
                    frame.at[index, col] = point[col]
        for col in ("x", "y", "surface_elevation"):
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
    findings = pd.DataFrame(issues, columns=["severity", "table", "row", "code", "message"])
    return Dataset(analyses, points, geology, water, findings)
