from pathlib import Path

from streamlit.testing.v1 import AppTest

from spatial_monitor.cli import main

APP = Path(__file__).parents[1] / 'src/spatial_monitor/app.py'
PASSWORD = 'test-only-personal-password-32chars'


def button(app, label):
    return next(item for item in app.button if item.label == label)


def login(app):
    next(item for item in app.text_input if item.label == '測試密碼').set_value(PASSWORD)
    return button(app, '進入工作台').click().run()


def test_missing_hosted_password_fails_closed(monkeypatch):
    monkeypatch.setenv('SM_APP_MODE', 'personal')
    monkeypatch.delenv('SM_TEST_PASSWORD', raising=False)
    app = AppTest.from_file(str(APP), default_timeout=60).run()
    assert not app.exception
    assert not app.tabs and not app.metric
    assert any('尚未完成設定' in item.value for item in app.error)


def test_login_logout_and_rotation(monkeypatch):
    monkeypatch.setenv('SM_APP_MODE', 'personal')
    monkeypatch.setenv('SM_TEST_PASSWORD', PASSWORD)
    app = AppTest.from_file(str(APP), default_timeout=60).run()
    assert not app.metric
    next(item for item in app.text_input if item.label == '測試密碼').set_value('wrong')
    button(app, '進入工作台').click().run()
    assert any('密碼不正確' in item.value for item in app.error)
    assert not app.metric
    login(app)
    assert not app.exception
    assert len(app.tabs) == 6 and app.metric[0].value == '288'
    assert '資料夾或Excel路徑' not in [item.label for item in app.text_input]
    assert 'personal-password-input' not in app.session_state
    assert app.session_state['personal-auth'] != PASSWORD
    monkeypatch.setenv('SM_TEST_PASSWORD', 'new-test-only-password-of-32-chars')
    app.run()
    assert not app.metric and not app.tabs
    monkeypatch.setenv('SM_TEST_PASSWORD', PASSWORD)
    login(app)
    assert len(app.tabs) == 6
    button(app, '登出測試入口').click().run()
    assert not app.exception
    assert not app.metric and not app.tabs
    assert 'personal-auth' not in app.session_state


def test_personal_password_creation_private_and_no_overwrite(tmp_path, capsys):
    target=tmp_path/'password'
    assert main(['site-password','--output',str(target)]) == 0
    password=target.read_text()
    assert len(password)>=24 and password not in capsys.readouterr().out
    assert target.stat().st_mode & 0o777 == 0o600
    assert main(['site-password','--output',str(target)]) == 2
    assert target.read_text() == password
