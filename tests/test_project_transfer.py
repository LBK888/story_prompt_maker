import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.characters import CharacterCatalog
from app.blank_storyboard import create_blank_storyboard
from app.project_archive import export_project, import_project, safe_relative
from app.storage import ProjectStore


def test_round_trip_preserves_media_text_snapshots_and_new_identity(tmp_path):
    store = ProjectStore(tmp_path / 'projects')
    original = store.create('測試角色故事', '中文故事')
    root = store.project_dir(original['id'])
    source = tmp_path / 'portrait.png'
    Image.new('RGB', (12, 12), 'red').save(source)
    asset = store.import_asset(original['id'], source, '角色.png')
    project = store.load(original['id'])
    project['assets'][0].update(character_name='小如', notes='保留介紹', roles=['character'])
    project['custom_text'] = 'all editable metadata'
    (root / 'template_snapshots' / 'workflow.json').write_text('{"1":{}}')
    (root / 'final' / 'video.mp4').write_bytes(b'test-video')
    (root / 'notes.txt').write_text('原始文字', encoding='utf-8')
    project['final_outputs'] = [{'path': 'final/video.mp4'}]
    store.save(project)
    archive = tmp_path / 'project.zip'
    export_project(store, original['id'], archive)
    imported = import_project(store, archive)
    imported_root = store.project_dir(imported['id'])
    assert imported['id'] != original['id']
    assert imported['settings'] == project['settings']
    assert imported['assets'] == project['assets']
    assert imported['custom_text'] == project['custom_text']
    for path in ('notes.txt', 'final/video.mp4', 'template_snapshots/workflow.json', 'ref_img/' + asset['filename']):
        assert (imported_root / path).read_bytes() == (root / path).read_bytes()
    assert len(store.list()) == 2
    assert store.load(original['id']) == project


@pytest.mark.parametrize('path', ['../escape', '/absolute', 'C:/file', 'a\\b', 'a/../b', 'ref_img/NUL.png', 'a:stream', 'a./b', 'a//b'])
def test_unsafe_paths_rejected(path):
    with pytest.raises(ValueError):
        safe_relative(path)


@pytest.mark.parametrize('kind', ['traversal', 'duplicate', 'schema', 'missing', 'bad-json', 'bad-zip', 'symlink'])
def test_invalid_import_never_publishes_project(tmp_path, kind):
    store = ProjectStore(tmp_path / 'projects')
    project = store.create('existing')
    output = tmp_path / 'bad.zip'
    export_project(store, project['id'], output)
    with zipfile.ZipFile(output) as archive:
        entries = {i.filename: archive.read(i) for i in archive.infolist()}
    if kind == 'schema':
        project['schema_version'] = 999
        entries['project/project.json'] = json.dumps(project).encode()
    elif kind == 'missing':
        project['assets'] = [{'filename': 'missing.png'}]
        entries['project/project.json'] = json.dumps(project).encode()
    elif kind == 'bad-json':
        entries['project/project.json'] = b'{'
    elif kind == 'traversal':
        entries['project/../../escape.txt'] = b'bad'
    elif kind == 'duplicate':
        entries['project/PROJECT.JSON'] = b'{}'
    with zipfile.ZipFile(output, 'w') as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
        if kind == 'symlink':
            info = zipfile.ZipInfo('project/link')
            info.external_attr = 0o120777 << 16
            archive.writestr(info, '../outside')
    if kind == 'bad-zip':
        output.write_bytes(b'no zip')
    with pytest.raises(ValueError):
        import_project(store, output)
    assert len(store.list()) == 1
    assert not (tmp_path / 'escape.txt').exists()
    assert not list(store.base_dir.glob('.import-*'))


def test_character_api_and_transfer(tmp_path, monkeypatch):
    import app.main as main
    store = ProjectStore(tmp_path / 'projects')
    catalog = CharacterCatalog(tmp_path / 'characters')
    folder = catalog.root / '女_小如'
    folder.mkdir(parents=True)
    Image.new('RGB', (800, 800), 'blue').save(folder / 'headshot.png')
    Image.new('RGB', (800, 800), 'green').save(folder / 'turnaround.png')
    (folder / 'voice.wav').write_bytes(b'voice-data')
    (folder / 'profile.txt').write_text('姓名：小如\n介紹：歌手', encoding='utf-8')
    (folder / 'line.txt').write_text('你好', encoding='utf-8')
    (catalog.root / '_ori').mkdir()
    monkeypatch.setattr(main, 'store', store)
    monkeypatch.setattr(main, 'character_catalog', catalog)
    monkeypatch.setattr(main, 'ensure_storyboard_structure_mutable', lambda _: None)
    client = TestClient(main.app)
    project = store.create('API test')
    items = client.get('/api/characters').json()
    assert len(items) == 1 and items[0]['name'] == '小如'
    key = items[0]['id']
    preview = client.get(f'/api/characters/{key}/thumbnail')
    assert preview.status_code == 200
    assert Image.open(io.BytesIO(preview.content)).size == (360, 360)
    assert client.get(f'/api/characters/{key}/files/voice.wav').content == b'voice-data'
    partial = client.get(f'/api/characters/{key}/files/voice.wav', headers={'Range': 'bytes=0-3'})
    assert partial.status_code == 206 and partial.content == b'voic'
    assert client.get(f'/api/characters/{key}/files/secret.txt').status_code == 404
    route = f"/api/projects/{project['id']}/characters"
    assert client.post(route, json={'ids': [key, 'unknown']}).status_code == 400
    assert store.load(project['id'])['assets'] == []
    added = client.post(route, json={'ids': [key]}).json()
    assert len(added['assets']) == 1
    assert added['assets'][0]['media_type'] == 'character'
    assert added['assets'][0]['filename'].endswith('turnaround.png')
    assert added['assets'][0]['audio_filename'].endswith('voice.wav')
    assert all(asset['character_name'] == '小如' for asset in added['assets'])
    assert len(client.post(route, json={'ids': [key]}).json()['assets']) == 1
    archive = client.get(f"/api/projects/{project['id']}/export")
    assert archive.status_code == 200
    imported = client.post('/api/project-import', files={'archive': ('project.zip', archive.content, 'application/zip')})
    assert imported.status_code == 200
    new_project = imported.json()
    assert new_project['id'] != project['id']
    root = store.project_dir(new_project['id'])
    assert (root / 'character_templates' / key / 'line.txt').read_text(encoding='utf-8') == '你好'
    for asset in new_project['assets']:
        assert (root / 'ref_img' / asset['filename']).exists()
        assert (root / 'ref_img' / asset['audio_filename']).read_bytes() == b'voice-data'
        assert (root / 'ref_img' / asset['headshot_filename']).is_file()
    assert client.post('/api/project-import', files={'archive': ('bad.zip', b'bad')}).status_code == 400


def test_storyboard_bindings_and_takes_survive_import(tmp_path):
    store = ProjectStore(tmp_path / 'projects')
    project = store.create('分鏡與手動提示詞')
    create_blank_storyboard(project)
    root = store.project_dir(project['id'])
    snapshot = 'template_snapshots/custom.json'
    (root / snapshot).write_text('{"1":{}}')
    video = 'segments/output.mp4'
    (root / video).write_bytes(b'video')
    segment = project['segments'][0]
    segment.update(prompt='手動內容保持原樣', prompt_source='manual', prompt_status='ready', status='completed', active_take_id='run_test',
                   template={'name':'custom', 'source_sha256':'abc', 'snapshot':snapshot, 'tokens':[], 'image_order':[]},
                   takes=[{'id':'run_test', 'status':'completed', 'outputs':[video], 'local_path':video}])
    project['segments'][1].update(status='running', takes=[{'id':'run_pending', 'status':'running', 'prompt_id':'old-server', 'outputs':[]}])
    store.save(project)
    output = tmp_path / 'transfer.zip'
    export_project(store, project['id'], output)
    source = store.load(project['id'])
    imported = import_project(store, output)
    assert imported['segments'][0] == source['segments'][0]
    pending = imported['segments'][1]
    assert pending['status'] == 'failed'
    assert pending['takes'][0]['status'] == 'failed'
    assert pending['takes'][0]['prompt_id'] is None
    assert store.load(imported['id'])['segments'][0]['prompt'] == '手動內容保持原樣'
