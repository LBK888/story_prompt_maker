"""Versioned, bounded ZIP transfer with staging and project-relative files."""
import json
import shutil
import stat
import zipfile
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory

from .domain import PROJECT_SCHEMA_VERSION, new_id, utc_now
from .storage import SAFE_ID, ProjectStore

MAX_ARCHIVE_BYTES = 20 * 1024**3
MAX_FILES = 20000
FORMAT = 'vid-auto-prompt-project'


def safe_relative(value: str) -> str:
    if not isinstance(value, str) or not value or '\\' in value or ':' in value:
        raise ValueError('專案包含不安全的檔案路徑')
    path = PurePosixPath(value)
    if path.is_absolute() or any(p in ('', '.', '..') for p in value.split('/')):
        raise ValueError('專案包含不安全的檔案路徑')
    for part in path.parts:
        if part.endswith((' ', '.')) or any(c in part for c in '<>"|?*') or any(ord(c) < 32 for c in part):
            raise ValueError('專案包含不安全的檔名')
        if part.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}:
            raise ValueError('專案包含保留檔名')
    return value


def validate_project(project, root):
    if not isinstance(project, dict) or project.get('schema_version') != PROJECT_SCHEMA_VERSION:
        raise ValueError('不支援的專案版本')
    if not isinstance(project.get('settings'), dict) or not isinstance(project.get('name'), str):
        raise ValueError('專案設定格式錯誤')
    def require(path):
        if not (root / safe_relative(path)).is_file():
            raise ValueError(f'專案缺少素材或檔案：{path}')
    for key in ('assets', 'segments', 'final_outputs', 'events'):
        if not isinstance(project.get(key), list) or any(not isinstance(x, dict) for x in project[key]):
            raise ValueError(f'專案 {key} 格式錯誤')
    for asset in project['assets']:
        filename = safe_relative(asset['filename'])
        if '/' in filename:
            raise ValueError('素材檔名不能包含資料夾')
        require('ref_img/' + filename)
        for key in ('audio_filename', 'headshot_filename'):
            if asset.get('media_type') == 'character' and key == 'audio_filename' and not asset.get(key):
                raise ValueError('內建角色缺少語音')
            if asset.get(key):
                component = safe_relative(asset[key])
                if '/' in component:
                    raise ValueError('角色素材檔名不能包含資料夾')
                require('ref_img/' + component)
    for segment in project['segments']:
        if not SAFE_ID.fullmatch(str(segment.get('id', ''))):
            raise ValueError('分鏡 ID 格式錯誤')
        if segment.get('template'):
            require(segment['template']['snapshot'])
        for candidate in segment.get('image_candidates', []):
            if not isinstance(candidate, dict) or not isinstance(candidate.get('filename'), str):
                raise ValueError('生成圖片紀錄格式錯誤')
            filename = safe_relative(candidate['filename'])
            if '/' in filename or candidate.get('path') != f'generated_images/{filename}':
                raise ValueError('生成圖片路徑錯誤')
            require(candidate['path'])
        for take in segment.get('takes', []):
            for key in ('local_path', 'workflow_path'):
                if take.get(key):
                    require(take[key])
            for path in take.get('outputs', []):
                require(path)
    for output in project['final_outputs']:
        require(output['path'])


def export_project(store: ProjectStore, project_id: str, destination: Path):
    with store._lock:
        project = store.load(project_id)
        root = store.project_dir(project_id)
        validate_project(project, root)
        files = [p for p in root.rglob('*') if p.is_file() and not p.name.endswith('.tmp')]
        if len(files) > MAX_FILES or sum(p.stat().st_size for p in files) > MAX_ARCHIVE_BYTES:
            raise ValueError('專案超過打包上限（20 GB／20,000 個檔案）')
        with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
            archive.writestr('manifest.json', json.dumps({'format': FORMAT, 'version': 1}))
            for path in files:
                if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
                    raise ValueError('專案包含外部連結，無法打包')
                archive.write(path, 'project/' + safe_relative(path.relative_to(root).as_posix()))


def import_project(store: ProjectStore, source: Path):
    try:
        with zipfile.ZipFile(source) as archive, TemporaryDirectory(prefix='.import-', dir=store.base_dir) as stage:
            entries = archive.infolist()
            if len(entries) > MAX_FILES + 1 or sum(i.file_size for i in entries) > MAX_ARCHIVE_BYTES:
                raise ValueError('壓縮檔超過解壓上限（20 GB／20,000 個檔案）')
            seen = set()
            for info in entries:
                name = safe_relative(info.filename.rstrip('/') if info.is_dir() else info.filename)
                if name.casefold() in seen:
                    raise ValueError('壓縮檔包含重複路徑')
                seen.add(name.casefold())
                if stat.S_ISLNK(info.external_attr >> 16) or info.flag_bits & 1:
                    raise ValueError('不支援連結或加密檔案')
                if name != 'manifest.json' and not name.startswith('project/'):
                    raise ValueError('不是有效的專案包')
            if archive.getinfo('manifest.json').file_size > 4096 or archive.getinfo('project/project.json').file_size > 32 * 1024**2:
                raise ValueError('專案描述檔過大')
            manifest = json.loads(archive.read('manifest.json'))
            if manifest != {'format': FORMAT, 'version': 1}:
                raise ValueError('不支援的專案包版本')
            for info in entries:
                target = Path(stage) / info.filename
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(info) as src, target.open('wb') as dst:
                        shutil.copyfileobj(src, dst, 1024 * 1024)
            root = Path(stage) / 'project'
            project = json.loads((root / 'project.json').read_text(encoding='utf-8'))
            validate_project(project, root)
            project['id'] = new_id('project')
            project['archived'] = False
            project['updated_at'] = utc_now()
            # Imported projects have a new identity. Never resume the source
            # installation's queue or collide with its public job IDs.
            job_ids = {}
            records = project.get('generation_jobs', [])
            if not isinstance(records, list) or any(not isinstance(j, dict) for j in records):
                raise ValueError('專案生成工作紀錄格式錯誤')
            for job in records:
                original_job_id = str(job.get('id', ''))
                job['id'] = new_id('job')
                job_ids[original_job_id] = job['id']
                job['project_id'] = project['id']
                if job.get('status') not in ('completed', 'failed', 'canceled'):
                    job.update(status='failed', error='匯入專案後，請檢查來源 ComfyUI 並明確重新送出未完成工作', finished_at=utc_now())
            for segment in project['segments']:
                if segment.get('status') in ('queued', 'preparing', 'uploading', 'submitted', 'running'):
                    segment['status'] = 'failed'
                for take in segment.get('takes', []):
                    if take.get('job_id'):
                        take['job_id'] = job_ids.get(take['job_id'])
                    if take.get('status') in ('queued', 'preparing', 'uploading', 'submitted', 'running'):
                        take.update(status='failed', prompt_id=None, error='匯入專案後，請重新送出未完成的生成工作')
            (root / 'project.json').write_text(json.dumps(project, ensure_ascii=False, indent=2), encoding='utf-8')
            # Validate through the normal loader before publishing to the project list.
            temporary_store = ProjectStore(Path(stage))
            staged_root = Path(stage) / project['id']
            root.rename(staged_root)
            root = staged_root
            project = temporary_store.load(project['id'])
            root.rename(store.project_dir(project['id']))
            return project
    except (zipfile.BadZipFile, KeyError, TypeError, AttributeError, UnicodeError, RuntimeError, NotImplementedError) as exc:
        raise ValueError('專案包損壞或格式不正確') from exc
