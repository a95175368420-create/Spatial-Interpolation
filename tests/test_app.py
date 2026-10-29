"""Exercise real Streamlit widget flows rather than only server liveness."""
from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = Path(__file__).parents[1] / "src/spatial_monitor/app.py"


def widget(items, label):
    return next(item for item in items if item.label == label)


def clean(app):
    assert not app.exception, [item.message for item in app.exception]
    assert not app.error, [item.value for item in app.error]


def test_default_and_layer_controls():
    app = AppTest.from_file(str(APP), default_timeout=60).run()
    clean(app)
    assert len(app.tabs) == 6
    assert app.metric[0].value == "288"
    widget(app.checkbox, "顯示2D內插估值").check()
    widget(app.checkbox, "套疊對應深度的地質與當日地下水觀測").check()
    widget(app.checkbox, "堆疊2D推估圖層（非三維內插）").check()
    app.run()
    clean(app)
    widget(app.checkbox, "已確認地層對應，將連接標示為解釋成果").check()
    widget(app.text_area, "地層接觸資料JSON").set_value('42')
    app.run()
    assert not app.exception
    assert any("JSON" in item.value for item in app.error)


def test_optional_models_and_validation_acceptance():
    app = AppTest.from_file(str(APP), default_timeout=60).run()
    widget(app.checkbox, "啟用多參數關聯分析").check()
    widget(app.checkbox, "啟用三維數值內插").check()
    widget(app.checkbox, "啟用連續3D地質模型").check()
    app.run()
    clean(app)
    widget(app.button, "計算配對關聯").click().run()
    clean(app)
    widget(app.button, "建立三維推估").click().run()
    clean(app)
    assert "volume-result" in app.session_state
    widget(app.checkbox, "我已檢視本組驗證誤差與模型假設，啟用體積統計").check().run()
    clean(app)
    widget(app.text_input, "由淺至深的地層名稱（逗號分隔）").set_value("Fill,Sand,Clay")
    widget(app.text_input, "解釋版本／修訂紀錄").set_value("test-revision-1")
    widget(app.checkbox, "已確認地層對應與接觸關係").check()
    widget(app.button, "建立地層界面").click().run()
    clean(app)
    assert "confirmed-geology" in app.session_state
    assert any(item.label == "地質剖切Y座標" for item in app.slider)
    widget(app.text_input, "限制於指定地層（選填）").set_value("Sand")
    widget(app.button, "建立三維推估").click().run()
    clean(app)


def download_labels(app):
    return [item.proto.label for item in app.get("download_button")]


def test_correlation_survives_unrelated_controls_and_invalidates_changed_pairing():
    app = AppTest.from_file(str(APP), default_timeout=60).run()
    widget(app.checkbox, "啟用多參數關聯分析").check().run()
    widget(app.button, "計算配對關聯").click().run()
    clean(app)
    assert "下載配對資料" in download_labels(app)
    widget(app.checkbox, "啟用三維數值內插").check().run()
    clean(app)
    assert "下載配對資料" in download_labels(app)
    widget(app.number_input, "配對日期容許差（天）").set_value(1.0).run()
    clean(app)
    assert "下載配對資料" not in download_labels(app)


def test_revoking_geology_confirmation_invalidates_dependent_volume():
    app = AppTest.from_file(str(APP), default_timeout=60).run()
    widget(app.checkbox, "啟用連續3D地質模型").check().run()
    widget(app.text_input, "由淺至深的地層名稱（逗號分隔）").set_value("Fill,Sand,Clay")
    widget(app.text_input, "解釋版本／修訂紀錄").set_value("confirmed-1")
    widget(app.checkbox, "已確認地層對應與接觸關係").check()
    widget(app.button, "建立地層界面").click().run()
    clean(app)
    assert "confirmed-geology" in app.session_state
    widget(app.checkbox, "啟用三維數值內插").check().run()
    widget(app.button, "建立三維推估").click().run()
    clean(app)
    assert "volume-result" in app.session_state
    widget(app.checkbox, "已確認地層對應與接觸關係").uncheck().run()
    clean(app)
    assert "confirmed-geology" not in app.session_state
    assert "volume-result" not in app.session_state
    assert "下載三維推估網格" not in download_labels(app)
    widget(app.text_input, "限制於指定地層（選填）").set_value("Sand")
    widget(app.button, "建立三維推估").click().run()
    clean(app)
    assert any("confirmed" in item.value for item in app.warning)


def test_export_persists_filter_rerun_but_not_source_change(tmp_path, monkeypatch):
    import shutil
    import spatial_monitor.export as export
    def small_export(dataset, config, output):
        output.mkdir()
        (output / "observations.csv").write_text(dataset.observations.head().to_csv(index=False))
    monkeypatch.setattr(export, "export_dataset", small_export)
    app = AppTest.from_file(str(APP), default_timeout=60).run()
    widget(app.button, "產生成果壓縮檔").click().run()
    clean(app)
    assert "下載完整成果ZIP" in download_labels(app)
    widget(app.selectbox, "參數").select("pH").run()
    clean(app)
    assert "下載完整成果ZIP" in download_labels(app)
    new_source = tmp_path / "other-source"
    shutil.copytree(APP.parents[2] / "examples" / "demo", new_source)
    import pandas as pd
    measurements = pd.read_csv(new_source / "analyses.csv")
    measurements.loc[measurements.parameter.eq("pH"), "value"] = 7.0
    measurements.to_csv(new_source / "analyses.csv", index=False)
    widget(app.text_input, "資料夾或Excel路徑").set_value(str(new_source)).run()
    clean(app)
    assert "下載完整成果ZIP" not in download_labels(app)
