"""Real-browser login, upload, plotting and logout for the personal test site."""
import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--url', default='http://127.0.0.1:8501')
    parser.add_argument('--browser', default='/usr/bin/chromium')
    parser.add_argument('--password-file', type=Path, default=ROOT/'.personal-site-password')
    parser.add_argument('--output', type=Path, default=ROOT/'outputs/personal-site')
    args=parser.parse_args()
    password=args.password_file.read_text().strip()
    args.output.mkdir(parents=True, exist_ok=True)
    checks, errors=[], []
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch(executable_path=args.browser,headless=True,
            args=['--no-sandbox','--disable-dev-shm-usage','--enable-unsafe-swiftshader'])
        page=browser.new_page(viewport={'width':1440,'height':1050})
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.goto(args.url,wait_until='domcontentloaded')
        expect(page.get_by_role('button',name='進入工作台',exact=True)).to_be_visible(timeout=60000)
        expect(page.get_by_role('tab')).to_have_count(0)
        expect(page.get_by_test_id('stMetricValue')).to_have_count(0)
        page.screenshot(path=str(args.output/'login.png'))
        checks.append('Anonymous browser sees login only, no observations or workbench')
        page.get_by_role('textbox',name='測試密碼',exact=True).fill('incorrect')
        page.get_by_role('button',name='進入工作台',exact=True).click()
        expect(page.get_by_text('密碼不正確，請重新輸入。',exact=True)).to_be_visible()
        expect(page.get_by_role('tab')).to_have_count(0)
        checks.append('Incorrect password is rejected')
        page.get_by_role('textbox',name='測試密碼',exact=True).fill(password)
        page.get_by_role('button',name='進入工作台',exact=True).click()
        expect(page.get_by_role('tab')).to_have_count(6,timeout=60000)
        expect(page.get_by_test_id('stMetricValue').first).to_have_text('288')
        expect(page.get_by_role('textbox',name='資料夾或Excel路徑',exact=True)).to_have_count(0)
        checks.append('Correct password opens all six workbench tabs with bundled demo')
        page.get_by_role('tab',name='分布與趨勢',exact=True).click()
        page.get_by_text('顯示2D內插估值',exact=True).click()
        page.wait_for_function("() => [...document.querySelectorAll('.js-plotly-plot')].some(p => p.offsetParent!==null && p.data?.some(t=>t.type==='heatmap'))",timeout=30000)
        chart=page.locator('.js-plotly-plot:visible').first
        chart.screenshot(path=str(args.output/'workbench-map.png'))
        checks.append('Personal workbench renders an actual 2D interpolated map')
        page.get_by_text('上傳Excel或CSV',exact=True).click()
        uploader=page.get_by_test_id('stFileUploader').filter(has_text='Excel工作簿或多個CSV資料表')
        uploader.locator('input[type=file]').set_input_files([str(path) for path in sorted((ROOT/'examples/demo').glob('*.csv'))])
        expect(page.get_by_test_id('stMetricValue').first).to_have_text('288',timeout=30000)
        checks.append('Authenticated multi-file CSV upload loads 288 observations')
        page.get_by_role('button',name='登出測試入口',exact=True).click()
        expect(page.get_by_role('button',name='進入工作台',exact=True)).to_be_visible()
        expect(page.get_by_role('tab')).to_have_count(0)
        expect(page.get_by_test_id('stMetricValue')).to_have_count(0)
        checks.append('Logout returns to login and removes the loaded dataset from the visible UI')
        assert not errors,errors
        browser.close()
    report={'checks':checks,'passed':len(checks),'page_errors':errors,'browser':'Chromium'}
    (args.output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
