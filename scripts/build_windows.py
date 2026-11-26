"""Build both self-contained Windows launchers from a pinned template revision."""
import argparse
import json
from pathlib import Path
import subprocess
import sys


def build(revision):
    root = Path(__file__).resolve().parents[1]
    output = root / "outputs/windows"
    output.mkdir(exist_ok=True, parents=True)
    commit = subprocess.check_output(["git", "rev-parse", f"{revision}^{{commit}}"], cwd=root, text=True).strip()
    tree = subprocess.check_output(["git", "ls-tree", "-rz", commit, "--",
                                    "character_template", "workflow_templates", "prompt_templates"], cwd=root)
    files = []
    for record in tree.decode("utf-8").split("\0"):
        if record:
            meta, path = record.split("\t", 1)
            files.append((path, meta.split()[2]))
    index = output / "template-index.json"
    index.write_text(json.dumps({"revision": commit, "files": files}), encoding="utf-8")
    for profile in ("remote", "gpu"):
        entry = output / f"launch_{profile}.py"
        entry.write_text(f"from app.portable import launch\nlaunch({profile!r})\n", encoding="utf-8")
        command = [sys.executable, "-m", "PyInstaller", "--onefile", "--noconfirm", "--noupx",
                   "--name", f"VidAutoPrompt-{profile}", "--distpath", str(output),
                   "--workpath", str(output / "build"), "--specpath", str(output), "--paths", str(root),
                   "--hidden-import", "app.main", "--collect-data", "opencc",
                   "--collect-data", "jsonschema_specifications"]
        for source, target in [("static", "static"), ("templates", "templates"), ("skills", "skills"),
                               ("scripts/install_runtime.ps1", "scripts"), (index, ".")]:
            command.extend(["--add-data", f"{root / source};{target}"])
        subprocess.run([*command, str(entry)], cwd=root, check=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--template-revision", default="HEAD")
    build(parser.parse_args().template_revision)
