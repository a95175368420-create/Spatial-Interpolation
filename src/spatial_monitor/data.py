"""Shared table contract for source data, normalized measurements and QA findings."""
from dataclasses import dataclass, field

import pandas as pd


@dataclass
class Dataset:
    observations: pd.DataFrame
    points: pd.DataFrame
    geology: pd.DataFrame
    water_levels: pd.DataFrame
    issues: pd.DataFrame
    config: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)


# Include these dimensions when comparing or interpolating measurements. An empty
# value means unspecified metadata; it does not establish equivalence to a known value.
COMPATIBILITY_COLUMNS = ["parameter", "medium", "unit", "basis", "method",
                         "reference_electrode", "temperature_compensated", "comparison_temperature"]


def compatible_groups(observations):
    """Yield physically comparable groups without dropping unspecified metadata."""
    cols = [c for c in COMPATIBILITY_COLUMNS if c in observations]
    return observations.groupby(cols, dropna=False, sort=True)
