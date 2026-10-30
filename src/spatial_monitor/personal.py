"""Small password gate for a single owner's hosted trial site."""
from __future__ import annotations

import hashlib
import hmac
import os
from pathlib import Path

import streamlit as st


def personal_mode():
    return os.environ.get('SM_APP_MODE', 'local') == 'personal'


def demo_path():
    configured = os.environ.get('SM_DEMO_PATH')
    if configured:
        return Path(configured)
    checkout = Path(__file__).resolve().parents[2] / 'examples' / 'demo'
    return checkout if checkout.is_dir() else Path.cwd() / 'examples' / 'demo'


def _password_signature(password):
    return hashlib.sha256(('spatial-monitor-personal-v1:' + password).encode()).hexdigest()


def _check_password():
    # Never store the raw password in authentication state or log it.
    candidate = st.session_state.pop('personal-password-input', '')
    expected = os.environ.get('SM_TEST_PASSWORD', '')
    if len(expected) >= 16 and hmac.compare_digest(candidate.encode(), expected.encode()):
        st.session_state['personal-auth'] = _password_signature(expected)
        st.session_state.pop('personal-login-failed', None)
    else:
        st.session_state['personal-login-failed'] = True


def require_login():
    """Local development is unchanged; a hosted trial fails closed if unconfigured."""
    if not personal_mode():
        return
    password = os.environ.get('SM_TEST_PASSWORD', '')
    if len(password) < 16:
        st.error('個人測試入口尚未完成設定。請在部署平台設定至少16字元的SM_TEST_PASSWORD。')
        st.stop()
    signature = _password_signature(password)
    authenticated = st.session_state.get('personal-auth', '')
    if authenticated and not hmac.compare_digest(authenticated, signature):
        # Rotation revokes existing sessions and clears their previously loaded data.
        st.session_state.clear()
    if not hmac.compare_digest(st.session_state.get('personal-auth', ''), signature):
        st.subheader('個人測試入口')
        st.write('輸入你的測試密碼，即可使用範例資料或上傳自己的Excel／CSV。')
        with st.form('personal-login', clear_on_submit=True):
            st.text_input('測試密碼', type='password', key='personal-password-input')
            st.form_submit_button('進入工作台', on_click=_check_password, type='primary')
        if st.session_state.get('personal-login-failed'):
            st.error('密碼不正確，請重新輸入。')
        st.stop()
    with st.sidebar:
        st.caption('個人測試模式 · 僅此瀏覽器工作階段登入')
        if st.button('登出測試入口'):
            st.session_state.clear()
            st.rerun()
