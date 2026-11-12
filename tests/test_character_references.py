import asyncio
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from app.blank_storyboard import create_blank_storyboard
from app.characters import CharacterCatalog
from app.config import AppConfig
from app.generation import GenerationManager
from app.media import scan_ref_img
from app.reference_order import bind_references, picture_asset_ids, resolve_references
from app.storage import ProjectStore
from app.template_engine import load_template, render_template, validate_template


ASSETS = [
    {'id': 'c1', 'media_type': 'character', 'filename': 'body1.png', 'audio_filename': 'voice1.wav', 'headshot_filename': 'head1.png', 'sha256': 'a'*64},
    {'id': 'c2', 'media_type': 'character', 'filename': 'body2.png', 'audio_filename': 'voice2.wav', 'sha256': 'b'*64},
    {'id': 'a', 'media_type': 'audio', 'filename': 'a.wav', 'sha256': 'c'*64},
    {'id': 'v', 'media_type': 'video', 'filename': 'v.mp4', 'sha256': 'd'*64},
    {'id': 'i', 'media_type': 'image', 'filename': 'i.png', 'sha256': 'e'*64},
]
CASES = [
    (['REF_IMG1','REF_AUD1','Drive_AUD'], ['c1'], {'REF_IMG1':'c1','REF_AUD1':'c1','Drive_AUD':'c1'}),
    (['REF_IMG1'], ['c1'], {'REF_IMG1':'c1'}),
    (['REF_AUD2'], ['c1'], {'REF_AUD2':'c1'}),
    (['Drive_AUD'], ['c1'], {'Drive_AUD':'c1'}),
    (['REF_IMG0','REF_IMG1','REF_AUD0','REF_AUD1','Drive_AUD'], ['c1','c2'], {'REF_IMG0':'c1','REF_AUD0':'c1','REF_IMG1':'c2','REF_AUD1':'c2','Drive_AUD':'c1'}),
    (['REF_VID0','REF_IMG1','REF_AUD1','REF_AUD2','Drive_AUD'], ['a','v','c1'], {'REF_VID0':'v','REF_IMG1':'c1','REF_AUD1':'a','REF_AUD2':'c1','Drive_AUD':'a'}),
    (['REF_IMG0','REF_AUD0','REF_IMG1','REF_AUD1'], ['a','c1','i'], {'REF_IMG0':'i','REF_AUD0':'a','REF_IMG1':'c1','REF_AUD1':'c1'}),
    (['REF_IMG1','REF_IMG1','REF_AUD1'], ['c1'], {'REF_IMG1':'c1','REF_AUD1':'c1'}),
]


@pytest.mark.parametrize('tokens,order,expected', CASES)
def test_typed_reference_allocation(tokens, order, expected):
    binding = {'tokens': tokens, 'image_order': order}
    assert resolve_references(binding, ASSETS, require_complete=True) == expected


def test_browser_mapping_matches_generation_mapping():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is required for browser/backend parity test')
    code = "const {mapReferenceMedia}=require('./static/reference-mapping.js'); const x=JSON.parse(require('fs').readFileSync(0,'utf8')); process.stdout.write(JSON.stringify(x.cases.map(([tokens,image_order])=>mapReferenceMedia({tokens,image_order},x.assets))));"
    result = subprocess.run([node, '-e', code], input=json.dumps({'cases':CASES,'assets':ASSETS}), text=True, capture_output=True, check=True)
    for actual, (_, _, expected) in zip(json.loads(result.stdout), CASES):
        assert actual == {'mapping':expected, 'missing':[], 'errors':[]}


def test_wrong_media_or_missing_slots_cannot_generate():
    with pytest.raises(ValueError, match='相容插槽'):
        resolve_references({'tokens':['REF_IMG1'], 'image_order':['a']}, ASSETS, require_complete=True)
    with pytest.raises(ValueError, match='REF_AUD2'):
        resolve_references({'tokens':['REF_IMG1','REF_AUD1','REF_AUD2'], 'image_order':['c1']}, ASSETS, require_complete=True)


def test_upload_uses_turnaround_and_voice_and_reuses_drive(tmp_path):
    store = ProjectStore(tmp_path)
    project = store.create('Character inputs')
    project['assets'] = ASSETS
    root = store.project_dir(project['id']) / 'ref_img'
    for asset in ASSETS:
        for key in ('filename','audio_filename','headshot_filename'):
            if asset.get(key):
                (root / asset[key]).write_bytes(b'fixture')
    template = load_template(Path('workflow_templates/AAIIref2AV_1.5x_template.json'))
    segment = {'template':{'tokens':template.tokens,'image_order':['c1','c2']}}
    calls = []
    class Client:
        async def upload_input(self, path, name):
            calls.append(path.name)
            return {'name':name}
    manager = GenerationManager(store, None, None, AppConfig(), None)
    values = asyncio.run(manager._upload_inputs(Client(), project['id'], project, segment, template.tokens))
    assert sorted(calls) == ['body1.png','body2.png','voice1.wav','voice2.wav']
    assert values['Drive_AUD'] == values['REF_AUD0']
    rendered = render_template(template, {**values,'PROMPT':'same prompt','SEED':987,'TASK_TYPE':'Ref2VA','OUTPUT_PREFIX':'project/run','WIDTH':1024,'HEIGHT':576,'DURATION':8})
    assert rendered['7']['inputs']['prompt'] == rendered['14']['inputs']['prompt'] == 'same prompt'
    assert rendered['11']['inputs']['noise_seed'] == rendered['50']['inputs']['noise_seed'] == 987
    assert rendered['21']['inputs']['filename_prefix'] == rendered['51']['inputs']['filename_prefix']
    bind_references(segment['template'], ASSETS)
    assert picture_asset_ids(segment) == ['c1','c2']


def test_legacy_characters_migrate_without_changing_files(tmp_path):
    store = ProjectStore(tmp_path)
    project = store.create('legacy')
    create_blank_storyboard(project)
    assets = []
    root = store.project_dir(project['id']) / 'ref_img'
    for index, name in enumerate(('headshot.png','turnaround.png','voice.wav')):
        (root / name).write_bytes(b'original')
        assets.append({'id':str(index),'filename':name,'original_name':'測試_'+name,'character_template_id':'preset','character_name':'測試','roles':['character'],'sha256':str(index)*64})
    project['assets'] = assets
    project['segments'][0]['template'] = {'tokens':['REF_IMG1','REF_AUD1','Drive_AUD'], 'image_order':['0','2','1']}
    store.save(project)
    result = store.load(project['id'])
    assert len(result['assets']) == 1
    character = result['assets'][0]
    assert character['media_type'] == 'character'
    assert character['filename'] == 'turnaround.png' and character['audio_filename'] == 'voice.wav'
    assert result['segments'][0]['template']['reference_mapping'] == {'REF_IMG1':'1','REF_AUD1':'1','Drive_AUD':'1'}
    assert scan_ref_img(store, project['id'])[0] == []
    assert all((root / name).read_bytes() == b'original' for name in ('headshot.png','turnaround.png','voice.wav'))
    store.delete_asset(project['id'], character['id'])
    assert not [p for p in root.iterdir() if p.is_file()]
    assert store.load(project['id'])['segments'][0]['template']['reference_mapping'] == {}


def test_incomplete_character_selection_is_atomic(tmp_path):
    store = ProjectStore(tmp_path / 'projects')
    project = store.create('test')
    catalog = CharacterCatalog(tmp_path / 'characters')
    for name in ('女_完整','男_缺語音'):
        folder = catalog.root / name
        folder.mkdir(parents=True)
        Image.new('RGB',(10,10)).save(folder / 'turnaround.png')
        if name == '女_完整':
            (folder / 'voice.wav').write_bytes(b'voice')
    with pytest.raises(ValueError):
        catalog.add(store, project['id'], list(catalog.folders()))
    assert store.load(project['id'])['assets'] == []


def test_repeated_media_and_zero_index_tokens_render(tmp_path):
    doc={'a':'[[REF_AUD0]]','b':'[[REF_AUD0]]','c':'[[REF_VID0]]','d':'[[Drive_AUD]]','o':'[[OUTPUT_PREFIX]]'}
    validate_template(doc)
    file=tmp_path/'workflow.json'
    file.write_text(json.dumps(doc))
    result=render_template(load_template(file), {'REF_AUD0':'a.wav','REF_VID0':'v.mp4','Drive_AUD':'a.wav','OUTPUT_PREFIX':'out'})
    assert result == {'a':'a.wav','b':'a.wav','c':'v.mp4','d':'a.wav','o':'out'}


def test_editor_accepts_repeated_typed_tokens_and_rejects_unknown():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node required')
    html = Path('ComfyUI Workflow 關鍵字編輯器.html').read_text(encoding='utf-8')
    definitions = html[html.index('    const FIXED_TOKENS'):html.index('    const state =')]
    validator = html[html.index('    function validateTemplate('):html.index('    function renderTree(')]
    doc = {'1':{'class_type':'Test','inputs':{'a':'[[REF_AUD0]]','b':'[[REF_AUD0]]','v':'[[REF_VID2]]','d':'[[Drive_AUD]]','o':'[[OUTPUT_PREFIX]]'}}}
    script = definitions + validator + '\nconsole.log(JSON.stringify(validateTemplate('+json.dumps(doc)+')));'
    result = subprocess.run([node, '-'], input=script, capture_output=True, text=True, encoding='utf-8', check=True)
    assert json.loads(result.stdout) == []
    script = script.replace('[[REF_VID2]]','[[UNKNOWN]]')
    result = subprocess.run([node, '-'], input=script, capture_output=True, text=True, encoding='utf-8', check=True)
    assert any(i['level'] == 'error' for i in json.loads(result.stdout))


def test_api_binding_accepts_one_character_for_two_slots(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import app.main as main
    from app.template_catalog import TemplateCatalog
    store = ProjectStore(tmp_path)
    project = store.create('API mapping')
    create_blank_storyboard(project)
    project['assets'] = ASSETS
    segment = project['segments'][0]
    segment['template'] = {'name':'typed','tokens':['REF_IMG1','REF_AUD1','Drive_AUD'],'image_order':[]}
    store.save(project)
    monkeypatch.setattr(main, 'store', store)
    monkeypatch.setattr(main, 'template_catalog', TemplateCatalog(store))
    client = TestClient(main.app)
    url = f"/api/projects/{project['id']}/segments/{segment['id']}/template"
    response = client.patch(url, json={'image_order':['c1']})
    assert response.status_code == 200
    assert response.json()['template']['reference_mapping'] == {'REF_IMG1':'c1','REF_AUD1':'c1','Drive_AUD':'c1'}
    rejected = client.patch(url, json={'image_order':['v']})
    assert rejected.status_code == 400
    assert store.load(project['id'])['segments'][0]['template']['image_order'] == ['c1']
