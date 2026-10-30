import io
import json
from pathlib import Path
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from spatial_monitor.api import Limits, create_app
from spatial_monitor.cli import main

KEY = 'test-only-key-which-is-at-least-32-characters'
HEADERS = {'X-API-Key': KEY}
ROOT = Path(__file__).parents[1]


@pytest.fixture
def payload():
    tables = {file.stem: pd.read_csv(file, dtype=str, keep_default_na=False).to_dict('records')
              for file in (ROOT/'examples/demo').glob('*.csv')}
    tables['analyses'] = [row for row in tables['analyses'] if row['parameter'] == 'pH' and row['sample_id'].endswith('-1')][:6]
    # Use one exact layer across the six measured points.
    ids = {row['sample_id'] for row in tables['analyses']}
    selected_samples = [row for row in tables['samples'] if row['sample_id'] in ids]
    top = selected_samples[0]['depth_top']
    date = selected_samples[0]['date']
    ids = {row['sample_id'] for row in selected_samples if row['depth_top']==top and row['date']==date}
    tables['analyses'] = [row for row in tables['analyses'] if row['sample_id'] in ids]
    return {'tables': tables}


@pytest.fixture
def client():
    with TestClient(create_app(api_keys={'test': KEY})) as client:
        yield client


def test_keys_required_at_startup_and_no_anonymous_data(monkeypatch, client):
    monkeypatch.delenv('SM_API_KEYS_JSON', raising=False)
    with pytest.raises(ValueError, match='requires'):
        create_app()
    assert client.get('/healthz').json()['status']=='ok'
    assert client.get('/v1/parameters').status_code == 401
    assert client.get('/v1/parameters', headers={'X-API-Key':'wrong'}).status_code==401
    assert client.get('/v1/parameters', headers=HEADERS).status_code==200
    assert 'securitySchemes' in client.get('/openapi.json').json()['components']


def test_validation_and_json_error_visibility(client, payload):
    response=client.post('/v1/validate',headers=HEADERS,json=payload)
    assert response.status_code==200, response.text
    assert response.json()['valid_observations']>0
    payload['tables']['analyses'][0]['value']='ND'
    result=client.post('/v1/validate',headers=HEADERS,json=payload).json()
    assert any(issue['code']=='censored_value' for issue in result['issues'])
    assert result['data'][0]['value'] is None
    assert result['data'][0]['raw_value']=='ND'


def test_layer_selection_and_grid(client,payload):
    groups=client.post('/v1/layers',headers=HEADERS,json=payload).json()['groups']
    assert len(groups)>=1
    result=client.post('/v1/interpolate',headers=HEADERS,json={**payload,'group_index':0})
    assert result.status_code==200,result.text
    assert result.json()['available'] is True
    assert any(row['supported'] for row in result.json()['grid'])
    assert client.post('/v1/interpolate',headers=HEADERS,json={**payload,'group_index':99999}).status_code==422


def test_cannot_read_server_paths_or_unknown_tables(client,payload):
    assert client.post('/v1/validate',headers=HEADERS,json={'path':'/etc/passwd'}).status_code==422
    payload['tables']['../../etc/passwd']=[]
    assert client.post('/v1/validate',headers=HEADERS,json=payload).status_code==422


def archive(tables):
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w',zipfile.ZIP_DEFLATED) as handle:
        for name,rows in tables.items():
            handle.writestr(name+'.csv',pd.DataFrame(rows).to_csv(index=False))
    return buffer.getvalue()


def test_real_file_upload_and_export(client,payload):
    raw=archive(payload['tables'])
    result=client.post('/v1/validate/file',headers=HEADERS,files={'file':('data.zip',raw,'application/zip')})
    assert result.status_code==200,result.text
    assert result.json()['valid_observations']>0
    result=client.post('/v1/export',headers=HEADERS,json=payload)
    assert result.status_code==200,result.text[:500]
    assert result.headers['content-type']=='application/zip'
    assert result.headers['cache-control']=='no-store'
    with zipfile.ZipFile(io.BytesIO(result.content)) as handle:
        assert handle.testzip() is None
        assert 'observations.csv' in handle.namelist()
        assert json.loads(handle.read('manifest.json'))['maps']>=1
    assert client.post('/v1/validate/file',headers=HEADERS,files={'file':('data.xlsx',b'bad zip')}).status_code==422


def test_excel_upload(client,payload):
    buffer=io.BytesIO()
    with pd.ExcelWriter(buffer,engine='openpyxl') as writer:
        for name,rows in payload['tables'].items():
            pd.DataFrame(rows).to_excel(writer,sheet_name=name,index=False)
    response=client.post('/v1/validate/file',headers=HEADERS,files={'file':('data.xlsx',buffer.getvalue())})
    assert response.status_code==200,response.text
    assert response.json()['valid_observations']>0


def test_body_limit_and_archive_paths(payload):
    with TestClient(create_app(api_keys={'test':KEY},limits=Limits(body_bytes=200))) as client:
        assert client.post('/v1/validate',headers=HEADERS,json=payload).status_code==413
        # Unauthorized traffic is rejected before parsing a large body.
        assert client.post('/v1/validate',json=payload).status_code==401
    with TestClient(create_app(api_keys={'test':KEY})) as client:
        buffer=io.BytesIO()
        with zipfile.ZipFile(buffer,'w') as handle:
            handle.writestr('../samples.csv','malicious')
        assert client.post('/v1/validate/file',headers=HEADERS,files={'file':('x.zip',buffer.getvalue())}).status_code==422


def test_expansion_limit_and_row_limit(payload):
    with TestClient(create_app(api_keys={'test':KEY},limits=Limits(expanded_bytes=10))) as client:
        assert client.post('/v1/validate/file',headers=HEADERS,files={'file':('x.zip',archive(payload['tables']))}).status_code==413
    with TestClient(create_app(api_keys={'test':KEY},limits=Limits(rows=2))) as client:
        assert client.post('/v1/validate',headers=HEADERS,json=payload).status_code==413


def test_concurrency_rejects_instead_of_queuing(client,payload,monkeypatch):
    import spatial_monitor.api as api
    started,release=threading.Event(),threading.Event()
    original=api.interpolate_layer
    def blocking(*args,**kwargs):
        started.set()
        assert release.wait(10)
        return original(*args,**kwargs)
    monkeypatch.setattr(api,'interpolate_layer',blocking)
    with ThreadPoolExecutor() as executor:
        future=executor.submit(client.post,'/v1/interpolate',headers=HEADERS,json=payload)
        assert started.wait(10)
        try:
            busy=client.post('/v1/validate',headers=HEADERS,json=payload)
            assert busy.status_code==429
            assert busy.headers['retry-after']=='5'
        finally:
            release.set()
        assert future.result().status_code==200


def test_key_creation_is_private_non_overwriting(tmp_path,capsys):
    target=tmp_path/'keys.json'
    assert main(['api-keys','--client','alice','--client','bob','--output',str(target)])==0
    values=json.loads(target.read_text())
    assert len(set(values.values()))==2
    assert all(len(key)>=32 for key in values.values())
    assert target.stat().st_mode & 0o777 == 0o600
    printed=capsys.readouterr().out
    assert all(key not in printed for key in values.values())
    assert main(['api-keys','--client','charlie','--output',str(target)])==2
    assert json.loads(target.read_text())==values


def test_cors_preflight_explicit_only(monkeypatch):
    monkeypatch.setenv('SM_API_ALLOWED_ORIGINS','https://frontend.example')
    with TestClient(create_app(api_keys={'test':KEY})) as client:
        result=client.options('/v1/validate',headers={'Origin':'https://frontend.example',
            'Access-Control-Request-Method':'POST','Access-Control-Request-Headers':'X-API-Key'})
        assert result.status_code==200
        assert result.headers['access-control-allow-origin']=='https://frontend.example'
        assert client.get('/v1/parameters',headers={'Origin':'https://evil.example',**HEADERS}).headers.get('access-control-allow-origin') is None
