"""Streamlit workbench. Run with spatial-monitor serve."""
from __future__ import annotations

import json
import hashlib
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

from spatial_monitor.config import load_config
from spatial_monitor.io import load_dataset
from spatial_monitor.personal import personal_mode, require_login, demo_path
from spatial_monitor.selection import (compatible_groups, describe_group, filter_observations,
                                       layer_groups, require_metric_crs, attach_strata)
from spatial_monitor.interpolation import interpolate_layer
from spatial_monitor.plotting import (map_figure, trend_figure, profile_figure,
                                     borehole_figure, section_figure, scene_figure)


def plot(fig, key):
    st.plotly_chart(fig, width="stretch", key=key)


def fingerprint(*items):
    digest = hashlib.sha256()
    for item in items:
        digest.update((item.to_csv(index=False) if isinstance(item, pd.DataFrame)
                       else json.dumps(item, sort_keys=True, default=str)).encode())
    return digest.hexdigest()


def invalidate_geology():
    """Widget callbacks run before the script, so downstream models cannot use revoked inputs."""
    for key in ("confirmed-geology", "geology-result", "volume-result"):
        st.session_state.pop(key, None)


def subset_points(dataset, observations):
    ids = observations["point_id"].dropna().unique()
    return dataset.points[dataset.points.point_id.isin(ids) & dataset.points.valid].copy()


def choose_group(frame, key):
    choices = list(compatible_groups(frame))
    if not choices:
        st.info("目前條件下沒有有效的數值資料。未檢出或異常資料可在資料檢核頁查看。")
        return None
    idx = st.selectbox("可比較的資料組別", range(len(choices)),
                       format_func=lambda i: describe_group(choices[i][0]), key=key)
    return choices[idx][1]


def main():
    st.set_page_config(page_title="環境空間工作台", page_icon="🌐", layout="wide")
    st.title("環境空間工作台")
    require_login()
    st.caption("多參數監測 · 地質柱狀與剖面 · 2D / 3D空間展示")
    default = demo_path()
    with st.sidebar:
        st.header("資料來源")
        source_mode = st.radio("選擇來源", ["內建範例" if personal_mode() else "範例／本機資料夾", "上傳Excel或CSV"])
        source_path = (default if personal_mode() else st.text_input("資料夾或Excel路徑", str(default))) if source_mode != "上傳Excel或CSV" else None
        uploads = st.file_uploader("Excel工作簿或多個CSV資料表", type=["xlsx", "csv"], accept_multiple_files=True) if source_path is None else []
        config_upload = st.file_uploader("欄位與參數設定（YAML，選填）", type=["yaml", "yml"])
        st.caption("基本資料表：samples、analyses、points；選填：geology、water_levels。可用YAML對應中文欄名。")
    try:
        with tempfile.TemporaryDirectory(prefix="spatial-import-") as directory:
            folder = Path(directory)
            cfg_path = None
            if config_upload:
                cfg_path = folder / "config.yaml"
                cfg_path.write_bytes(config_upload.getvalue())
            config = load_config(cfg_path)
            if source_path is None:
                if not uploads:
                    st.info("請選擇Excel或CSV資料；也可切換至內建範例體驗。")
                    return
                xlsx = [u for u in uploads if u.name.lower().endswith(".xlsx")]
                if xlsx and len(uploads) != 1:
                    raise ValueError("請上傳一個Excel工作簿，或一組CSV，避免混用來源。")
                for uploaded in uploads:
                    (folder / Path(uploaded.name).name).write_bytes(uploaded.getvalue())
                source_path = folder / Path(xlsx[0].name).name if xlsx else folder
            dataset = load_dataset(source_path, config)
    except (ValueError, OSError, KeyError) as exc:
        st.error(f"資料載入失敗：{exc}")
        return
    observations = dataset.observations
    if observations.empty:
        st.warning("沒有分析紀錄；仍可檢視資料品質與地質資料。")
    counts = st.columns(4)
    counts[0].metric("分析紀錄", len(observations))
    counts[1].metric("有效數值", int(observations.valid.sum()))
    counts[2].metric("點位", len(dataset.points))
    counts[3].metric("檢核訊息", len(dataset.issues))
    with st.sidebar:
        st.header("篩選")
        filtered = observations.copy()
        for col, title in [("site", "場址"), ("medium", "介質"), ("parameter", "參數"), ("batch", "批次")]:
            values = sorted(filtered[col].dropna().astype(str).unique())
            selected = st.selectbox(title, ["全部", *values], key=f"filter-{col}")
            if selected != "全部":
                filtered = filter_observations(filtered, **{col: selected})
        ids = sorted(filtered.point_id.dropna().unique())
        selected_ids = st.multiselect("點位（留空為全部）", ids)
        if selected_ids:
            filtered = filter_observations(filtered, point_ids=selected_ids)
        dates = filtered.date.dropna()
        if not dates.empty:
            date_range = st.date_input("日期範圍", (dates.min().date(), dates.max().date()))
            if len(date_range) == 2:
                filtered = filter_observations(filtered, date_start=date_range[0], date_end=date_range[1])
        depth_top = st.number_input("深度上限（m）", min_value=0.0, value=0.0)
        max_depth = float(filtered.depth_bottom.max()) if filtered.depth_bottom.notna().any() else 10.0
        depth_bottom = st.number_input("深度下限（m）", min_value=0.0, value=max(max_depth, depth_top))
        filtered = filter_observations(filtered, depth_top=depth_top, depth_bottom=depth_bottom)
        st.caption("深度篩選只納入完整位於範圍內的樣品區間，不切割或複製樣品。")
    tabs = st.tabs(["資料檢核", "分布與趨勢", "地質與剖面", "3D實測展示", "選用分析", "成果匯出"])
    with tabs[0]:
        st.subheader("原始來源與標準化資料")
        st.dataframe(filtered, width="stretch")
        st.subheader("異常與處理紀錄")
        st.dataframe(dataset.issues, width="stretch")
        st.download_button("下載異常清單", dataset.issues.to_csv(index=False).encode("utf-8-sig"), "issues.csv", on_click="ignore")
        with st.expander("目前參數設定與門檻來源"):
            st.json(config)
    with tabs[1]:
        group = choose_group(filtered, "map-group")
        if group is not None:
            parameter = str(group.parameter.iloc[0])
            definition = {**config["parameters"][parameter], "parameter": parameter}
            layers = list(layer_groups(group))
            selected = st.multiselect("比較批次／深度層", range(len(layers)), default=[0] if layers else [],
                                      format_func=lambda i: describe_group(layers[i][0]), max_selections=4)
            do_idw = st.checkbox("顯示2D內插估值", value=False)
            overlays = st.checkbox("套疊對應深度的地質與當日地下水觀測", value=False)
            alternatives = list(compatible_groups(filtered))
            secondary = st.selectbox("並排另一資料組（各自色階）", [-1, *range(len(alternatives))],
                                     format_func=lambda i: "不比較" if i == -1 else describe_group(alternatives[i][0]))
            settings = dict(config["interpolation"])
            with st.expander("內插與顯示範圍"):
                settings["grid_spacing"] = st.number_input("網格間距（m）", min_value=0.1, value=float(settings["grid_spacing"]))
                settings["max_distance"] = st.number_input("距最近實測點的顯示上限（m）", min_value=0.1, value=float(settings.get("max_distance") or 10))
                st.caption("IDW使用該層全部有效點；顯示距離不等於搜尋半徑或已驗證的預測準確距離。空白表示無估值。")
            color_range = (float(group.value.min()), float(group.value.max()))
            bounds = (float(group.x.min()), float(group.x.max()), float(group.y.min()), float(group.y.max()))
            columns = st.columns(min(len(selected), 2) or 1)
            for j, idx in enumerate(selected):
                meta, rows = layers[idx]
                grid = None
                with columns[j % len(columns)]:
                    st.caption(describe_group(meta))
                    if do_idw:
                        try:
                            require_metric_crs(rows)
                            grid = interpolate_layer(rows, definition, settings, bounds=bounds)
                            if grid.reason:
                                st.info(grid.reason)
                        except ValueError as exc:
                            st.warning(str(exc))
                    rock = dataset.geology.loc[dataset.geology.valid & dataset.geology.point_id.isin(rows.point_id)
                        & dataset.geology.depth_top.le(meta["depth_top"]) & dataset.geology.depth_bottom.ge(meta["depth_bottom"])] if overlays else None
                    water = dataset.water_levels.loc[dataset.water_levels.valid & dataset.water_levels.point_id.isin(rows.point_id)
                        & dataset.water_levels.date.eq(meta["date"])] if overlays else None
                    plot(map_figure(rows, definition, grid=grid, bounds=bounds, color_range=color_range,
                                    geology=rock, water_levels=water), f"map-{idx}")
                    if secondary != -1:
                        comparison = alternatives[secondary][1]
                        comparison = comparison.loc[comparison.date.eq(meta["date"]) & comparison.batch.eq(meta["batch"])
                            & comparison.depth_top.eq(meta["depth_top"]) & comparison.depth_bottom.eq(meta["depth_bottom"])]
                        if comparison.empty:
                            st.info("第二組沒有相同日期、批次與深度區間的資料。")
                        else:
                            second_def = config["parameters"][comparison.parameter.iloc[0]]
                            plot(map_figure(comparison, second_def, bounds=bounds), f"compare-{idx}")
            plot(trend_figure(group, definition), "trend")
            plot(profile_figure(group, definition), "profile")
    with tabs[2]:
        points = dataset.points.loc[dataset.points.valid].copy()
        geology = dataset.geology.copy()
        sites = sorted(points.site.dropna().unique()) if "site" in points else []
        if sites:
            selected_site = st.selectbox("地質場址", sites)
            points = points.loc[points.site.eq(selected_site)]
            geology = geology.loc[geology.point_id.isin(points.point_id)]
        if geology.empty:
            st.info("沒有可用地質區間，請匯入geology資料表。")
        else:
            plot(borehole_figure(geology, points), "boreholes")
            if len(points) >= 2:
                ids = points.point_id.tolist()
                start_id = st.selectbox("剖面起點", ids, index=0)
                end_id = st.selectbox("剖面終點", ids, index=len(ids) - 1)
                tolerance = st.number_input("剖面兩側納入距離（m）", min_value=0.1, value=25.0)
                by_id = points.set_index("point_id")
                section_obs = choose_group(filtered[filtered.point_id.isin(ids)], "section-group")
                contacts = []
                st.caption("地層不自動連線。若有專業解釋，可指定各孔接觸深度後確認套用。")
                with st.expander("加入已確認的地層連接"):
                    contact_text = st.text_area("地層接觸資料JSON", '[]', help='格式：[{"name":"界面A", "point_ids":["BH1","BH2"], "depths":[2,3]}]')
                    confirmed = st.checkbox("已確認地層對應，將連接標示為解釋成果")
                    if confirmed:
                        try:
                            contacts = json.loads(contact_text)
                            if not isinstance(contacts, list) or any(not isinstance(c, dict) for c in contacts):
                                raise ValueError("Contacts must be a list of mappings")
                            contacts = [{**c, "confirmed": True} for c in contacts]
                        except (ValueError, TypeError):
                            st.error("地層連接JSON格式錯誤。")
                            contacts = []
                try:
                    require_metric_crs(points)
                    fig = section_figure(geology, points, by_id.loc[start_id, ["x", "y"]].to_numpy(float),
                                         by_id.loc[end_id, ["x", "y"]].to_numpy(float), tolerance,
                                         observations=section_obs, confirmed_contacts=contacts,
                                         parameter_config=config["parameters"][section_obs.parameter.iloc[0]] if section_obs is not None else None)
                    plot(fig, "section")
                except (ValueError, KeyError, TypeError) as exc:
                    st.warning(str(exc))
    with tabs[3]:
        group = choose_group(filtered, "scene-group")
        if group is not None:
            definition = {**config["parameters"][str(group.parameter.iloc[0])], "parameter": str(group.parameter.iloc[0])}
            dates = sorted(group.date.dropna().unique())
            if len(dates) > 1:
                selected_date = st.selectbox("3D採樣日期", dates, format_func=lambda d: str(pd.Timestamp(d).date()))
                group = group.loc[group.date.eq(selected_date)]
            batches = sorted(group.batch.fillna("").unique())
            if len(batches) > 1:
                batch = st.selectbox("3D採樣批次", batches)
                group = group.loc[group.batch.fillna("").eq(batch)]
            points = subset_points(dataset, group)
            geology = dataset.geology[dataset.geology.point_id.isin(points.point_id)]
            grids = []
            stack = st.checkbox("堆疊2D推估圖層（非三維內插）", value=False)
            try:
                require_metric_crs(points)
                if stack:
                    for meta, rows in layer_groups(group):
                        grid = interpolate_layer(rows, definition, config["interpolation"])
                        if grid.available:
                            grids.append({"grid": grid, "depth": (meta["depth_top"] + meta["depth_bottom"]) / 2})
                water = dataset.water_levels.loc[dataset.water_levels.valid & dataset.water_levels.point_id.isin(points.point_id)
                    & dataset.water_levels.date.isin(group.date)]
                plot(scene_figure(group, geology, points, definition, grids=grids,
                                  water_levels=water), "scene")
            except ValueError as exc:
                st.warning(str(exc))
    with tabs[4]:
        render_extensions(filtered, dataset, config)
    with tabs[5]:
        st.write("匯出完整來源資料的標準化表、異常清單、統計表、圖面、設定與來源指紋。畫面篩選不會刪除匯出資料。")
        st.caption("HTML可離線互動；2D圖另提供PNG。選用模型在各模組個別下載。")
        export_key = fingerprint(dataset.observations, dataset.points, dataset.geology,
                                 dataset.water_levels, dataset.issues, config)
        if st.button("產生成果壓縮檔", type="primary"):
            from spatial_monitor.export import export_dataset, zip_outputs
            with st.spinner("產生可重現成果…"), tempfile.TemporaryDirectory(prefix="spatial-export-") as folder:
                output = Path(folder) / "report"
                try:
                    export_dataset(dataset, config, output)
                    st.session_state["export-result"] = (export_key, zip_outputs(output))
                except (ValueError, OSError) as exc:
                    st.error(str(exc))
        saved_export = st.session_state.get("export-result")
        if saved_export and saved_export[0] == export_key:
            st.download_button("下載完整成果ZIP", saved_export[1], "spatial-monitor-results.zip",
                               mime="application/zip", on_click="ignore")


def render_extensions(filtered, dataset, config):
    from spatial_monitor.extensions import (pair_parameters, correlation_figure,
        interpolate_volume, volume_figure, estimate_volume, build_geology_model,
        geology_model_figure, geology_slice_figure)
    st.caption("選用模組各自啟用。模型結果為推估，相關性不表示因果。")
    if st.checkbox("啟用多參數關聯分析", value=config["extensions"]["correlation"]):
        names = sorted(filtered.parameter.unique())
        if len(names) >= 2:
            left = st.selectbox("參數A", names)
            right = st.selectbox("參數B", [n for n in names if n != left])
            days = st.number_input("配對日期容許差（天）", min_value=0.0, value=0.0)
            depth = st.number_input("配對深度端點容許差（m）", min_value=0.0, value=0.0)
            grouping = st.selectbox("關聯分組", ["medium", "stratum"])
            data = attach_strata(filtered, dataset.geology) if grouping == "stratum" else filtered
            if grouping == "stratum":
                unknown = int(data.stratum.isna().sum())
                st.caption(f"{unknown}筆無唯一完整地層歸屬，未納入地層分組分析。")
                data = data.dropna(subset=["stratum"])
            correlation_key = fingerprint(data, left, right, days, depth, grouping)
            if st.button("計算配對關聯"):
                try:
                    result = pair_parameters(data, left, right, days, depth, grouping)
                    st.session_state["correlation-result"] = (correlation_key, result)
                except ValueError as exc:
                    st.warning(str(exc))
            saved_correlation = st.session_state.get("correlation-result")
            if saved_correlation and saved_correlation[0] == correlation_key:
                result = saved_correlation[1]
                st.write({key: result[key] for key in ["n", "pearson", "spearman", "notes"]})
                plot(correlation_figure(result), "correlation")
                st.dataframe(result["pairs"])
                st.dataframe(result["groups"])
                st.dataframe(result["excluded"])
                st.download_button("下載配對資料", result["pairs"].to_csv(index=False), "pairs.csv", on_click="ignore")
        else:
            st.info("關聯分析需要至少兩種參數，請調整左側參數篩選。")
    if st.checkbox("啟用三維數值內插", value=config["extensions"]["volume_interpolation"]):
        group = choose_group(filtered, "volume-group")
        if group is not None:
            events = list(group.groupby(["date", "batch"], dropna=False))
            index = st.selectbox("三維推估採樣事件", range(len(events)), format_func=lambda i: str(events[i][0]))
            group = events[index][1]
            cols = st.columns(3)
            hscale = cols[0].number_input("水平距離尺度（m）", min_value=0.1, value=10.0)
            vscale = cols[1].number_input("垂直距離尺度（m）", min_value=0.1, value=1.0)
            spacing = cols[2].number_input("三維水平網格（m）", min_value=0.1, value=2.0)
            vspacing = st.number_input("三維垂直網格（m）", min_value=0.1, value=0.5)
            limit = st.number_input("最大標準化最近點距離", min_value=0.1, value=2.0)
            stratum = st.text_input("限制於指定地層（選填）", "")
            st.caption("以採樣區間中點近似三維位置；模型會計算留一交叉驗證。")
            settings = {"horizontal_scale": hscale, "vertical_scale": vscale,
                        "grid_spacing": spacing, "vertical_spacing": vspacing,
                        "max_distance": limit, "max_cells": 100000, "power": 2}
            model_record = st.session_state.get("confirmed-geology")
            source_key = fingerprint(dataset.geology, dataset.points)
            model = model_record[1] if model_record and model_record[0] == source_key else None
            if stratum:
                settings["stratum"] = stratum
                if model is None:
                    st.info("請先在下方建立並確認同一份資料的地質模型，才能指定地層限制。")
            parameter_config = config["parameters"][group.parameter.iloc[0]]
            run_key = fingerprint(group, parameter_config, settings, model.metadata if model else None)
            if st.button("建立三維推估"):
                try:
                    require_metric_crs(group)
                    result = interpolate_volume(group, parameter_config, settings, model)
                    st.session_state["volume-result"] = (run_key, result)
                except ValueError as exc:
                    st.warning(str(exc))
            saved = st.session_state.get("volume-result")
            if saved and saved[0] == run_key:
                result = saved[1]
                st.json({"validation": result.validation, "metadata": result.metadata})
                plot(volume_figure(result), "volume")
                from spatial_monitor.export import volume_csv
                st.download_button("下載三維推估網格", volume_csv(result), "volume.csv", on_click="ignore")
                st.download_button("下載驗證與模型設定", json.dumps({"validation": result.validation,
                    "metadata": result.metadata}, ensure_ascii=False, indent=2), "volume-metadata.json", on_click="ignore")
                accepted = st.checkbox("我已檢視本組驗證誤差與模型假設，啟用體積統計", key="accept-"+run_key)
                if accepted:
                    threshold = st.number_input("體積判讀門檻", value=0.0)
                    direction = st.selectbox("門檻方向", ["above", "below", "outside"])
                    if direction == "outside":
                        upper = st.number_input("區間上限", value=max(1.0, threshold))
                        threshold = (threshold, upper)
                    try:
                        estimate = estimate_volume(result, threshold, direction, accepted_validation=True)
                        st.json(estimate)
                        st.download_button("下載體積統計與假設", json.dumps(estimate, ensure_ascii=False, indent=2), "volume-statistics.json", on_click="ignore")
                    except ValueError as exc:
                        st.warning(str(exc))
    if st.checkbox("啟用連續3D地質模型", value=config["extensions"]["geological_model"], on_change=invalidate_geology):
        st.caption("限定可對應的完整地層序列；缺層、尖滅、斷層或不完整鑽孔須另行專業建模。")
        strata = st.text_input("由淺至深的地層名稱（逗號分隔）", on_change=invalidate_geology)
        revision = st.text_input("解釋版本／修訂紀錄", on_change=invalidate_geology)
        spacing = st.number_input("地質模型網格（m）", min_value=0.1, value=2.0, on_change=invalidate_geology)
        confirmed = st.checkbox("已確認地層對應與接觸關係", on_change=invalidate_geology)
        points = dataset.points[dataset.points.valid]
        sites = sorted(points.site.dropna().unique())
        site = st.selectbox("連續地質模型場址", sites, on_change=invalidate_geology) if sites else None
        if site is not None:
            points = points.loc[points.site.eq(site)]
        geology = dataset.geology.loc[dataset.geology.point_id.isin(points.point_id)]
        model_key = fingerprint(geology, points, strata, revision, spacing, confirmed)
        if st.button("建立地層界面"):
            try:
                require_metric_crs(points)
                model = build_geology_model(geology, points, [s.strip() for s in strata.split(",") if s.strip()],
                                            {"grid_spacing": spacing, "max_cells": 100000}, confirmed, revision)
                st.session_state["geology-result"] = (model_key, model)
                st.session_state["confirmed-geology"] = (fingerprint(dataset.geology, dataset.points), model)
            except ValueError as exc:
                st.warning(str(exc))
        saved_model = st.session_state.get("geology-result")
        if saved_model and saved_model[0] == model_key:
            model = saved_model[1]
            st.json(model.metadata)
            plot(geology_model_figure(model), "geology-model")
            y = st.slider("地質剖切Y座標", min_value=float(model.y.min()), max_value=float(model.y.max()),
                          value=float(np.median(model.y)))
            plot(geology_slice_figure(model, y=y), "geology-slice")
            from spatial_monitor.export import geology_model_csv
            st.download_button("下載地層界面", geology_model_csv(model), "geology-interfaces.csv", on_click="ignore")
            st.download_button("下載地質解釋紀錄", json.dumps(model.metadata, ensure_ascii=False, indent=2), "geology-metadata.json", on_click="ignore")



if __name__ == "__main__":
    main()
