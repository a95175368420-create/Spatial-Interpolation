"""Real Chromium smoke test. Run against `spatial-monitor serve`.

Install the optional browser dependency, then run:
  python tests/browser_smoke.py --browser /usr/bin/chromium
Screenshots, a downloaded report and results.json go to outputs/browser-qa.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import zipfile

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]


def run(url, executable, output):
    output.mkdir(parents=True, exist_ok=True)
    checks, errors = [], []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=executable, headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--enable-unsafe-swiftshader"])
        context = browser.new_context(viewport={"width": 1440, "height": 1050}, accept_downloads=True)
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.set_default_timeout(30000)

        def settle():
            page.wait_for_timeout(600)
            page.get_by_test_id("stStatusWidget").wait_for(state="hidden", timeout=90000)
            expect(page.get_by_test_id("stException")).to_have_count(0)

        def passed(name):
            checks.append(name)
            print("PASS", name, flush=True)

        def rendered(predicate):
            # Plotly renders asynchronously after Streamlit has finished its rerun.
            # Wait for the new chart's semantics, not merely an existing DOM node.
            page.wait_for_function("() => [...document.querySelectorAll('.js-plotly-plot')].some(p => "
                "p.offsetParent !== null && p.data && (" + predicate + "))", timeout=30000)

        def tab(name):
            page.get_by_role("tab", name=name, exact=True).click()
            settle()

        def toggle(name):
            # Streamlit's accessible checkbox input is covered by its styled label.
            # Click the visible label, just as a mouse user would.
            page.get_by_text(name, exact=True).click()
            settle()

        def select(name, value):
            page.get_by_role("combobox", name=name, exact=True).click()
            page.get_by_role("option", name=value, exact=True).click()
            settle()

        page.goto(url, wait_until="domcontentloaded")
        page.get_by_role("tab", name="資料檢核", exact=True).wait_for(timeout=60000)
        settle()
        expect(page.get_by_test_id("stMetricValue").nth(0)).to_have_text("288")
        page.screenshot(path=str(output / "01-import.png"))
        passed("Default demo loads 288 observations without an application exception")

        select("參數", "ORP")
        tab("分布與趨勢")
        plot = page.locator(".js-plotly-plot:visible").first
        expect(plot).to_be_visible()
        page.wait_for_function("() => [...document.querySelectorAll('.js-plotly-plot')].some(p => p.data?.some(t => t.name?.includes('Measured'))) ")
        toggle("顯示2D內插估值")
        rendered("p.data.some(t=>t.type==='heatmap')")
        toggle("套疊對應深度的地質與當日地下水觀測")
        rendered("p.data.some(t=>t.name?.includes('Geology'))")
        page.screenshot(path=str(output / "02-map.png"))
        passed("Parameter filtering, all-point IDW and geological/water overlays render")

        tab("地質與剖面")
        visible = page.locator(".js-plotly-plot:visible")
        expect(visible).to_have_count(2)
        assert visible.first.evaluate("p => p.data.some(t=>t.type==='bar')")
        page.screenshot(path=str(output / "03-geology.png"))
        passed("Borehole logs and projected geological section render")

        tab("3D實測展示")
        scene = page.locator(".js-plotly-plot:visible").first
        expect(scene.locator("canvas").first).to_be_visible()
        before = scene.evaluate("p=>JSON.stringify(p._fullLayout.scene.camera)")
        canvas = scene.locator("canvas").first
        box = canvas.bounding_box()
        page.mouse.move(box["x"]+box["width"]*.5, box["y"]+box["height"]*.5)
        page.mouse.down()
        page.mouse.move(box["x"]+box["width"]*.7, box["y"]+box["height"]*.6, steps=12)
        page.mouse.up()
        page.wait_for_timeout(700)
        after = scene.evaluate("p=>JSON.stringify(p._fullLayout.scene.camera)")
        assert before != after, "Dragging did not rotate the actual WebGL camera"
        page.screenshot(path=str(output / "04-3d-rotated.png"))
        initial = scene.evaluate("p=>p.data.map(t=>t.visible||true)")
        scene.locator(".legendtoggle").first.click()
        page.wait_for_function("p=>p.data.some(t=>t.visible==='legendonly')", arg=scene.element_handle(), timeout=15000)
        assert scene.evaluate("p=>p.data.map(t=>t.visible||true)") != initial
        toggle("堆疊2D推估圖層（非三維內插）")
        # Unequal measured elevations must either show an explicit omission reason or stack surfaces.
        page.wait_for_function("""() => [...document.querySelectorAll('.js-plotly-plot')].some(p =>
            p.offsetParent !== null && (p.data?.some(t=>t.type==='surface') ||
            (p.layout?.annotations||[]).some(a=>a.text.includes('terrain reference'))))""", timeout=20000)
        page.screenshot(path=str(output / "04-3d-stacks.png"))
        passed("Real WebGL scene rotates, toggles layers and labels 2D-stack limitations")

        tab("成果匯出")
        page.get_by_role("button", name="產生成果壓縮檔", exact=True).click()
        download_button = page.get_by_role("button", name="下載完整成果ZIP", exact=True)
        download_button.wait_for(timeout=120000)
        settle()
        with page.expect_download(timeout=30000) as event:
            download_button.click()
        target = output / "downloaded-results.zip"
        event.value.save_as(str(target))
        with zipfile.ZipFile(target) as archive:
            assert archive.testzip() is None
            manifest = json.loads(archive.read("manifest.json"))
            assert manifest["observations"] == 288 and manifest["maps"] == 48
            assert manifest["sections"] == 16
        settle()
        expect(download_button).to_be_visible()
        passed("Complete ZIP downloads with valid CRC, 48 maps and 16 sections; remains downloadable")

        # Change an unrelated view control: the expensive export must remain available.
        select("參數", "pH")
        tab("成果匯出")
        expect(download_button).to_be_visible()
        passed("Unrelated filters preserve the completed export")

        page.get_by_text("上傳Excel或CSV", exact=True).click()
        settle()
        uploader = page.get_by_test_id("stFileUploader").filter(has_text="Excel工作簿或多個CSV資料表")
        uploader.locator('input[type="file"]').set_input_files([str(p) for p in sorted((ROOT / "examples/demo").glob("*.csv"))])
        settle()
        expect(page.get_by_test_id("stMetricValue").nth(0)).to_have_text("288")
        expect(page.get_by_test_id("stException")).to_have_count(0)
        passed("Actual multi-file CSV upload loads the complete dataset")

        # Excel import is a fresh browser session, preventing inherited selection state.
        from openpyxl import Workbook
        workbook = Workbook()
        workbook.remove(workbook.active)
        for source in sorted((ROOT / "examples/demo").glob("*.csv")):
            sheet = workbook.create_sheet(source.stem)
            with source.open(encoding="utf-8-sig") as handle:
                for row in csv.reader(handle):
                    sheet.append(row)
        xlsx = output / "demo-upload.xlsx"
        workbook.save(xlsx)
        context.close()
        context = browser.new_context(viewport={"width":1440,"height":1050})
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(url, wait_until="domcontentloaded")
        page.get_by_role("tab", name="資料檢核", exact=True).wait_for(timeout=60000)
        page.get_by_text("上傳Excel或CSV", exact=True).click()
        settle()
        uploader = page.get_by_test_id("stFileUploader").filter(has_text="Excel工作簿或多個CSV資料表")
        uploader.locator('input[type="file"]').set_input_files(str(xlsx))
        settle()
        expect(page.get_by_test_id("stMetricValue").nth(0)).to_have_text("288")
        page.screenshot(path=str(output / "05-excel-upload.png"))
        passed("Actual Excel workbook upload loads all five worksheets")
        assert not errors, errors
        browser.close()
    report = {"checks":checks,"passed":len(checks),"javascript_errors":errors,"browser":"Chromium", "mode":"headless with actual DOM and WebGL interaction"}
    (output / "results.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    return report


if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8501")
    parser.add_argument("--browser", default="/usr/bin/chromium")
    parser.add_argument("--output", type=Path, default=ROOT / "outputs/browser-qa")
    args=parser.parse_args()
    print(json.dumps(run(args.url,args.browser,args.output),ensure_ascii=False,indent=2))
