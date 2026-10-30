"""Project configuration and parameter definitions; no regulatory defaults are implied."""
from copy import deepcopy
import math
from pathlib import Path

import yaml


def _parameter(label, unit, media, *, transform="none", colorscale="Viridis", valid_range=None, **extra):
    return dict(label=label, unit=unit, media=media, data_type="continuous", transform=transform,
                colorscale=colorscale, valid_range=valid_range, thresholds=[], **extra)


WATER = ["water", "groundwater", "surface_water"]
DEFAULT_CONFIG = {
    "crs": None,
    "tables": {name: {"filename": f"{name}.csv", "sheet": name, "columns": {}}
               for name in ("samples", "analyses", "points", "geology", "water_levels")},
    "parameters": {
        "pH": _parameter("pH", "1", WATER + ["soil"], colorscale="RdBu", valid_range=[0, 14]),
        "EC": _parameter("EC", "uS/cm", WATER + ["soil"], valid_range=[0, None]),
        "DO": _parameter("DO", "mg/L", WATER, valid_range=[0, None]),
        "DO_SAT": _parameter("DO saturation", "%", WATER, valid_range=[0, None]),
        "ORP": _parameter("ORP", "mV", WATER + ["soil"], colorscale="RdBu"),
        "TOC": _parameter("TOC", "mg/L", WATER + ["soil"], valid_range=[0, None],
                          unit_by_medium={**{m: "mg/L" for m in WATER}, "soil": "mg/kg"}),
        "Zn": _parameter("Zinc", "mg/kg", WATER + ["soil"], valid_range=[0, None],
                         unit_by_medium={**{m: "mg/L" for m in WATER}, "soil": "mg/kg"}),
        "temperature": _parameter("Temperature", "degC", WATER + ["soil", "air"]),
        "turbidity": _parameter("Turbidity", "NTU", WATER, valid_range=[0, None]),
    },
    "interpolation": {"power": 2, "grid_spacing": 1, "max_distance": 10,
                      "min_width": 2, "max_cells": 100000},
    "geology": {"colors": {}},
    "extensions": {"correlation": False, "geological_model": False,
                   "volume_interpolation": False, "volume_statistics": False},
}


def _merge(base, override):
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = deepcopy(value)
    return base


def validate_config(config):
    if not isinstance(config.get("parameters"), dict) or not isinstance(config.get("tables"), dict):
        raise ValueError("parameters and tables must be mappings")
    if not isinstance(config.get("interpolation"), dict):
        raise ValueError("interpolation must be a mapping")
    for name, settings in config["tables"].items():
        if not isinstance(settings, dict) or not isinstance(settings.get("columns", {}), dict):
            raise ValueError(f"Table {name}: settings and columns must be mappings")
    for name, definition in config["parameters"].items():
        if not isinstance(definition, dict) or not definition.get("unit"):
            raise ValueError(f"Parameter {name}: unit is required")
        if definition.get("data_type", definition.get("type", "continuous")) not in ("continuous", "numeric"):
            raise ValueError(f"Parameter {name}: categorical data must use the geology table, not numeric observations")
        transform = definition.get("transform", "none")
        if transform not in ("none", "log10"):
            raise ValueError(f"Parameter {name}: transform must be none or log10")
        if name in ("pH", "ORP") and transform != "none":
            raise ValueError(f"Parameter {name}: logarithmic transformation is not supported")
        valid_range = definition.get("valid_range")
        if valid_range is not None:
            if not isinstance(valid_range, list) or len(valid_range) != 2:
                raise ValueError(f"Parameter {name}: valid_range needs [minimum, maximum]")
            low, high = valid_range
            if low is not None and high is not None and low > high:
                raise ValueError(f"Parameter {name}: invalid valid_range")
        for threshold in definition.get("thresholds", []):
            if threshold.get("kind") not in ("upper", "lower", "range"):
                raise ValueError(f"Parameter {name}: threshold kind must be upper, lower or range")
            if not threshold.get("source") or not threshold.get("conditions"):
                raise ValueError(f"Parameter {name}: thresholds need source and conditions")
            value = threshold.get("value")
            if threshold["kind"] == "range":
                if not isinstance(value, list) or len(value) != 2 or value[0] > value[1]:
                    raise ValueError(f"Parameter {name}: range threshold requires [low, high]")
            elif not isinstance(value, (int, float)):
                raise ValueError(f"Parameter {name}: threshold value must be numeric")
    for name in ("power", "grid_spacing", "max_cells"):
        value = config["interpolation"][name]
        if not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"interpolation.{name} must be finite and positive")
    if int(config["interpolation"]["max_cells"]) != config["interpolation"]["max_cells"]:
        raise ValueError("interpolation.max_cells must be an integer")
    for name in ("max_distance", "min_width"):
        value = config["interpolation"].get(name)
        if value is not None and (not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0):
            raise ValueError(f"interpolation.{name} must be finite and nonnegative")
    return config


def load_config(path=None):
    """Return independent defaults recursively updated by a YAML file or dictionary."""
    config = deepcopy(DEFAULT_CONFIG)
    if path is not None:
        if isinstance(path, dict):
            supplied = path
        else:
            with Path(path).open(encoding="utf-8") as handle:
                try:
                    supplied = yaml.safe_load(handle) or {}
                except yaml.YAMLError as exc:
                    raise ValueError(f"Invalid YAML configuration: {exc}") from exc
        if not isinstance(supplied, dict):
            raise ValueError("Configuration root must be a mapping")
        _merge(config, supplied)
    return validate_config(config)
