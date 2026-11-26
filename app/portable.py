"""Initialize a portable Windows installation beside the executable."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import NamedTemporaryFile
from time import sleep
from urllib.parse import quote
from urllib.request import urlopen

from .config import RESOURCE_ROOT, ROOT


def install_template(entry, revision):
    name, blob = entry
    path = ROOT / name
    if path.is_file():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    url = f"https://raw.githubusercontent.com/LBK888/story_prompt_maker/{revision}/{quote(name)}"
    for attempt in range(3):
        temp = None
        try:
            with NamedTemporaryFile(dir=path.parent, delete=False) as output:
                temp = Path(output.name)
                with urlopen(url, timeout=60) as response:
                    shutil.copyfileobj(response, output)
            size = temp.stat().st_size
            digest = hashlib.sha1(f"blob {size}\0".encode())
            with temp.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            if digest.hexdigest() != blob:
                raise ValueError(f"Template integrity check failed: {name}")
            if not path.exists():
                temp.rename(path)
            return
        except OSError:
            if attempt == 2:
                raise
            sleep(2 ** attempt)
        finally:
            if temp:
                temp.unlink(missing_ok=True)


def initialize(profile):
    os.chdir(ROOT)
    os.environ["VAP_INITIAL_PROVIDER"] = "openrouter" if profile == "remote" else "local"
    manifest = json.loads((RESOURCE_ROOT / "template-index.json").read_text(encoding="utf-8"))
    missing = [item for item in manifest["files"] if not (ROOT / item[0]).is_file()]
    if missing:
        print(f"Downloading {len(missing)} missing template files from GitHub. Full character library is ~1.9 GB.", flush=True)
        with ThreadPoolExecutor(max_workers=6) as pool:
            for count, _ in enumerate(pool.map(lambda item: install_template(item, manifest["revision"]), missing), 1):
                if count % 10 == 0:
                    print(f"Templates: {count}/{len(missing)}", flush=True)
    tools = ["ffmpeg/bin/ffmpeg.exe", "ffmpeg/bin/ffprobe.exe"]
    if profile == "gpu":
        tools.append("llama.cpp/llama-server.exe")
    if any(not (ROOT / "runtime" / name).is_file() for name in tools):
        command = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                   str(RESOURCE_ROOT / "scripts/install_runtime.ps1"), "-ProjectRoot", str(ROOT)]
        if profile == "remote":
            command.append("-FfmpegOnly")
        subprocess.run(command, check=True)


def launch(profile):
    try:
        initialize(profile)
        from run import main
        main()
    except (Exception, SystemExit) as exc:
        if isinstance(exc, SystemExit) and not exc.code:
            return
        print(f"Startup failed: {exc}", file=sys.stderr)
        if sys.stdin.isatty():
            input("Press Enter to exit.")
        raise
