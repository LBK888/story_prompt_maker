"""Local preset catalog. Selected media are copied into the project."""
from pathlib import Path
import hashlib
import shutil
from functools import lru_cache
from io import BytesIO
from PIL import Image, ImageOps

from .storage import ProjectStore, sha256_file
from .domain import utc_now

MEDIA = ("headshot.png", "turnaround.png", "voice.wav")


@lru_cache(maxsize=512)
def thumbnail(path: str, modified: int) -> bytes:
    with Image.open(path) as image:
        image = ImageOps.exif_transpose(image)
        image.thumbnail((360, 360))
        buffer = BytesIO()
        image.convert('RGB').save(buffer, 'JPEG', quality=82)
        return buffer.getvalue()


class CharacterCatalog:
    def __init__(self, root: Path):
        self.root = root

    def folders(self):
        if not self.root.exists():
            return {}
        return {hashlib.sha256(p.name.encode()).hexdigest()[:20]: p
                for p in sorted(self.root.iterdir())
                if p.is_dir() and not p.is_symlink() and not p.name.startswith(('.', '_'))}

    def folder(self, character_id: str) -> Path:
        folder = self.folders().get(character_id)
        if folder is None:
            raise ValueError("找不到內建角色")
        return folder

    def file(self, character_id: str, filename: str) -> Path:
        if filename not in (*MEDIA, "profile.txt", "line.txt"):
            raise ValueError("不支援的角色檔案")
        folder = self.folder(character_id)
        path = folder / filename
        if not path.is_file() or path.resolve().parent != folder.resolve():
            raise ValueError("角色檔案不存在")
        return path

    def item(self, character_id, folder):
        def read(name):
            try:
                path = folder / name
                if path.resolve().parent != folder.resolve():
                    return ''
                return path.read_text(encoding="utf-8-sig").strip()
            except (OSError, UnicodeError):
                return ""
        profile = read("profile.txt")
        name = folder.name.partition('_')[2] or folder.name
        for line in profile.splitlines():
            if line.startswith(("姓名：", "姓名:")):
                name = line[3:].strip() or name
        introduction = next((line.strip()[3:].strip() for line in profile.splitlines()
                             if line.strip().startswith(("介紹：", "介紹:"))), "")
        return {"id": character_id, "name": name, "category": folder.name.split('_')[0],
                "profile": profile, "introduction": introduction, "line": read("line.txt"),
                "media": [name for name in MEDIA if (folder / name).is_file()]}

    def list(self):
        return [self.item(key, folder) for key, folder in self.folders().items()]

    def add(self, store: ProjectStore, project_id: str, ids: list[str]):
        if not ids or len(ids) > 30 or any(not isinstance(key, str) for key in ids):
            raise ValueError("請選取 1–30 個角色")
        items = [self.item(key, self.folder(key)) for key in dict.fromkeys(ids)]
        # A character is usable only with both generation components.
        for item in items:
            for name in ('turnaround.png', 'voice.wav'):
                self.file(item['id'], name)
        with store._lock:
            for item in items:
                project = store.load(project_id)
                if any(a.get('character_template_id') == item['id'] and a.get('media_type') == 'character' for a in project['assets']):
                    continue
                files = {}
                for name in item['media']:
                    source = self.file(item['id'], name)
                    filename = f"character_{item['id']}_{name}"
                    destination = store.project_dir(project_id) / 'ref_img' / filename
                    shutil.copy2(source, destination)
                    files[name] = filename
                project['assets'].append({
                    'id': f"character_{item['id']}", 'original_name': item['name'],
                    'filename': files['turnaround.png'], 'audio_filename': files['voice.wav'],
                    'headshot_filename': files.get('headshot.png'), 'media_type': 'character',
                    'mime_type': 'image/png', 'sha256': sha256_file(self.file(item['id'], 'turnaround.png')),
                    'character_template_id': item['id'], 'character_name': item['name'],
                    'roles': ['character'], 'bindings': [], 'notes': item['profile'],
                    'voice_line': item['line'], 'missing': False, 'created_at': utc_now(),
                })
                dest = store.project_dir(project_id) / 'character_templates' / item['id']
                dest.mkdir(parents=True, exist_ok=True)
                for name in ('profile.txt', 'line.txt'):
                    try:
                        source = self.file(item['id'], name)
                    except ValueError:
                        continue
                    shutil.copy2(source, dest / name)
                store.save(project)
            return store.load(project_id)
