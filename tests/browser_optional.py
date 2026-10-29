"""Real Chromium checks for optional models, downloads and interpretation lifecycle.

Requires Playwright and a running app. Example:
python tests/browser_optional.py --url http://127.0.0.1:8501 --browser /usr/bin/chromium
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright, expect


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8501")
    parser.add_argument("--browser", default="/usr/bin/chromium")
    parser.add_argument("--output", default="outputs/browser-qa/optional")
    args = parser.parse_args()
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    report = {"checks": [], "page_errors": [], "console_errors": []}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=args.browser, headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--enable-unsafe-swiftshader"])
        page = browser.new_page(viewport={"width": 1500, "height": 1000}, accept_downloads=True)
        page.set_default_timeout(30000)
        page.on("pageerror", lambda error: report["page_errors"].append(str(error)))
        page.on("console", lambda message: report["console_errors"].append(message.text)
                if message.type == "error" else None)

        def download(label, filename):
            button = page.get_by_role("button", name=label, exact=True)
            expect(button).to_be_visible()
            with page.expect_download() as pending:
                button.click()
            pending.value.save_as(out / filename)
            assert (out / filename).stat().st_size > 0

        def chart(title):
            element = page.locator('[data-testid="stPlotlyChart"]:visible').filter(has_text=title).last
            expect(element).to_be_visible()
            return element

        page.goto(args.url, wait_until="networkidle")
        page.get_by_role("tab", name="選用分析", exact=True).click()
        page.get_by_text("啟用多參數關聯分析", exact=True).click()
        page.get_by_role("button", name="計算配對關聯", exact=True).click()
        expect(page.get_by_role("button", name="下載配對資料", exact=True)).to_be_visible()
        page.get_by_text("啟用三維數值內插", exact=True).click()
        download("下載配對資料", "pairs.csv")
        report["checks"].append("Correlation persists across unrelated controls and downloads")
        page.get_by_role("button", name="建立三維推估", exact=True).click()
        expect(page.get_by_role("button", name="下載三維推估網格", exact=True)).to_be_visible()
        volume = chart("3D estimate · midpoint representation")
        plot = volume.locator(".js-plotly-plot")
        expect(plot).to_be_visible()
        page.wait_for_function("""() => [...document.querySelectorAll('.js-plotly-plot')].some(e =>
            e._fullLayout?.title?.text?.includes('3D estimate · midpoint representation') &&
            e._fullLayout.scene.xaxis.range[0] > 200000)""")
        volume.screenshot(path=str(out / "volume-chart.png"))
        download("下載三維推估網格", "volume.csv")
        download("下載驗證與模型設定", "volume-metadata.json")
        metadata = json.loads((out / "volume-metadata.json").read_text())
        assert metadata["validation"]["n"] >= 3
        page.get_by_text("我已檢視本組驗證誤差與模型假設，啟用體積統計", exact=True).click()
        download("下載體積統計與假設", "volume-statistics.json")
        assert json.loads((out / "volume-statistics.json").read_text())["volume_m3"] > 0
        report["checks"].append("Rendered 3D estimates, original-unit validation and accepted volume statistics")

        page.get_by_text("啟用連續3D地質模型", exact=True).click()
        page.get_by_role("textbox", name="由淺至深的地層名稱（逗號分隔）", exact=True).fill("Fill,Sand,Clay")
        page.get_by_role("textbox", name="解釋版本／修訂紀錄", exact=True).fill("browser-verified-1")
        page.get_by_text("已確認地層對應與接觸關係", exact=True).click()
        page.get_by_role("button", name="建立地層界面", exact=True).click()
        expect(page.get_by_role("button", name="下載地層界面", exact=True)).to_be_visible()
        chart("Interpreted geological interfaces").screenshot(path=str(out / "geology-model-chart.png"))
        chart("Interpreted geological slice").screenshot(path=str(out / "geology-slice-chart.png"))
        page.get_by_role("slider", name="地質剖切Y座標").press("ArrowRight")
        download("下載地層界面", "geology-interfaces.csv")
        download("下載地質解釋紀錄", "geology-metadata.json")
        report["checks"].append("Confirmed geology interfaces, interactive slice and downloadable interpretation")

        page.get_by_role("button", name="建立三維推估", exact=True).click()
        download("下載驗證與模型設定", "confirmed-context-volume-metadata.json")
        page.get_by_text("已確認地層對應與接觸關係", exact=True).click()
        expect(page.get_by_role("button", name="下載三維推估網格", exact=True)).to_have_count(0)
        expect(page.get_by_role("button", name="下載地層界面", exact=True)).to_have_count(0)
        page.get_by_role("textbox", name="限制於指定地層（選填）", exact=True).fill("Sand")
        page.get_by_role("textbox", name="限制於指定地層（選填）", exact=True).press("Enter")
        page.get_by_role("button", name="建立三維推估", exact=True).click()
        expect(page.get_by_text("Stratum restriction requires a confirmed ordered GeologyModel.", exact=True)).to_be_visible()
        page.screenshot(path=str(out / "revoked-model.png"))
        report["checks"].append("Revoking geological confirmation invalidates dependent estimates")
        assert not report["page_errors"], report["page_errors"]
        assert not report["console_errors"], report["console_errors"]
        browser.close()
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
