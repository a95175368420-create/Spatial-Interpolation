"""Fill a COPY of a supplied v3.4 template with the reproducible synthetic demo.

Usage: .venv/bin/python scripts/build_v34_examples.py template.xlsx --output examples/workbooks
Never edits the supplied template. Only source columns are written; formula
columns, table metadata, menus, standards and all eight sheets are preserved.
"""
import argparse
from copy import copy
from datetime import datetime
from pathlib import Path

import pandas as pd
from openpyxl import load_workbook
from openpyxl.formula.translate import Translator
from openpyxl.utils.cell import range_boundaries, get_column_letter

ROOT = Path(__file__).resolve().parents[1]


def fill_table(ws, name, rows, inputs):
    table = ws.tables[name]
    left, top, right, old_bottom = range_boundaries(table.ref)
    seed = [copy(ws.cell(top + 1, c)) for c in range(left, right + 1)]
    bottom = top + max(len(rows), 1)
    for r in range(top + 1, max(bottom, old_bottom) + 1):
        for c in range(left, right + 1):
            dest, source = ws.cell(r, c), seed[c - left]
            dest.value = None
            dest.comment = None
            if r > bottom:
                continue
            dest._style = copy(source._style)
            if c in inputs:
                value = rows[r - top - 1].get(c) if r - top - 1 < len(rows) else None
                dest.value = value
                if isinstance(value, str):
                    dest.data_type = "s"  # Never interpret source strings as formulas.
            elif source.data_type == "f":
                dest.value = Translator(source.value, origin=source.coordinate).translate_formula(dest.coordinate)
    table.ref = f"{get_column_letter(left)}{top}:{get_column_letter(right)}{bottom}"
    if table.autoFilter:
        table.autoFilter.ref = table.ref
    for validation in ws.data_validations.dataValidation:
        for span in list(validation.sqref.ranges):
            if span.min_row <= top + 1 and span.max_row >= old_bottom and span.max_row < bottom:
                span.max_row = bottom


def append_master(ws, name, entries, key_columns, inputs):
    left, top, right, bottom = range_boundaries(ws.tables[name].ref)
    rows = [{c: ws.cell(r, c).value for c in inputs} for r in range(top + 1, bottom + 1)]
    existing = {tuple(row[c] for c in key_columns) for row in rows}
    for row in entries:
        key = tuple(row.get(c) for c in key_columns)
        if key not in existing:
            rows.append(row)
            existing.add(key)
    fill_table(ws, name, rows, inputs)


def build(template, destination, *, blank=False, site="合成示範場址A（非實測）", x_offset=0):
    if Path(template).resolve() == Path(destination).resolve():
        raise ValueError("Output must be a copy, not the supplied template")
    book = load_workbook(template)
    original_sheets = book.sheetnames[:]
    points = pd.read_csv(ROOT / "examples/demo/points.csv")
    samples = pd.read_csv(ROOT / "examples/demo/samples.csv")
    analyses = pd.read_csv(ROOT / "examples/demo/analyses.csv", dtype=str, keep_default_na=False)
    point_rows, sample_rows, result_rows = [], [], []
    sample_ids = {}
    for p in points.itertuples():
        for medium, suffix in [("土壤", "S"), ("地下水", "W")]:
            point_rows.append({1: f"{p.point_id}-{suffix}", 2: "土壤鑽孔" if suffix == "S" else "監測井",
                3: medium, 4: 10 if suffix == "W" else 3, 5: p.x + x_offset, 6: p.y,
                7: p.surface_elevation, 8: 0.3 if suffix == "W" else None, 9: 8 if suffix == "W" else None,
                10: .05 if suffix == "W" else None, 11: "是",
                12: "合成資料，非實測；crs=EPSG:3826; elevation_datum=ground; wellhead_offset=0"})
    for s in samples.itertuples():
        suffix = "S" if s.medium == "soil" else "W"
        date = datetime.strptime(s.date, "%Y-%m-%d")
        sid = f"{s.point_id}-{suffix}_{date:%Y%m%d}_{int(s.depth_top) + 1:02d}"
        sample_ids[s.sample_id] = sid
        sample_rows.append({1: sid, 2: f"{s.point_id}-{suffix}", 3: date, 4: s.batch,
            5: (s.depth_top + s.depth_bottom) / 2,
            6: s.depth_top if suffix == "S" else (.2 if s.depth_top == 0 else None),
            7: s.depth_bottom if suffix == "S" else None, 8: "其他" if suffix == "S" else "微洗井",
            9: "合成範例", 10: "合成範例", 11: f"合成資料；來源 examples/demo/samples.csv；{s.sample_id}"})
    master_entries, units = [], set()
    by_sample = samples.set_index("sample_id")
    names = {"Zn": "鋅", "TOC": "總有機碳", "temperature": "水溫"}
    for r in analyses.to_dict("records"):
        name = "溶氧飽和度" if r["parameter"] == "DO" and r["unit"] == "%" else names.get(r["parameter"], r["parameter"])
        medium = "土壤" if by_sample.at[r["sample_id"], "medium"] == "soil" else "地下水"
        unit = {"1": "無因次", "degC": "℃"}.get(r["unit"], r["unit"])
        metadata = "; ".join(f"{key}={r[key]}" for key in ("method", "basis", "temperature",
            "temperature_compensated", "reference_temperature", "reference_electrode", "calibration") if r.get(key))
        result_rows.append({1: name, 2: r["value"], 3: unit, 4: f"合成資料，非實測；{metadata}", 8: sample_ids[r["sample_id"]]})
        standard = "mg/kg" if name == "總有機碳" and medium == "土壤" else ("μS/cm" if name == "EC" else unit)
        master_entries.append({1: medium, 2: name, 3: r["parameter"], 4: "合成範例新增測項",
                               5: r.get("method"), 6: standard, 7: "是"})
        units.update([unit, standard])
    fill_table(book["03_點位主檔"], "DB_03", [] if blank else point_rows, set(range(1, 13)))
    fill_table(book["01_樣品登錄"], "Samples", [] if blank else sample_rows, set(range(1, 12)))
    fill_table(book["02_分析資料庫"], "Results", [] if blank else result_rows, {1, 2, 3, 4, 8})
    for cell in book["01_樣品登錄"]["C"][1:len(sample_rows) + 1]:
        cell.number_format = "yyyy-mm-dd"
    ws = book["04_測項與清單"]
    append_master(ws, "DB_04", master_entries, [1, 2], set(range(1, 8)))
    append_master(ws, "DB_06", [{11: "%", 12: "mg/kg", 13: 10000},
        {11: "mS/cm", 12: "μmho/cm", 13: 1000}], [11, 12], {11, 12, 13})
    append_master(ws, "UnitCodes", [{5: u} for u in sorted(units)], [5], {5})
    book["00_使用說明"]["C5"] = "請填寫場址名稱" if blank else site
    book["07_QAQC"]["C3"] = None  # No legal classification is implied for a synthetic/new site.
    book["07_QAQC"]["C6"] = 1
    book.calculation.calcMode = "auto"
    book.calculation.fullCalcOnLoad = True
    book.calculation.forceFullCalc = True
    assert book.sheetnames == original_sheets
    destination.parent.mkdir(parents=True, exist_ok=True)
    book.save(destination)
    book.close()
    return {"points": 0 if blank else len(point_rows), "samples": 0 if blank else len(sample_rows),
            "observations": 0 if blank else len(result_rows)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("template", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "examples/workbooks")
    args = parser.parse_args()
    print(build(args.template, args.output / "monitoring_v34_demo.xlsx"))
    print(build(args.template, args.output / "monitoring_v34_blank.xlsx", blank=True))
