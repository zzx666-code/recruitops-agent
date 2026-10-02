"""Download release-pinned public Codex Windows assets, never user installations."""

import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from packages.desktop_runtime.resources import inside


def main():
    root = ROOT / ".desktop-runtime-tests/native-build/codex"
    root.mkdir(parents=True, exist_ok=True)
    proxy = os.environ.get("RECRUITOPS_PACKAGE_PROXY", "").strip()
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": proxy, "https": proxy} if proxy else {})
    )
    with opener.open("https://api.github.com/repos/openai/codex/releases/tags/rust-v0.149.0", timeout=60) as response:
        release = json.load(response)
    wanted = {"codex", "codex-app-server", "codex-command-runner", "codex-windows-sandbox-setup", "codex-code-mode-host"}
    records = []
    for asset in release["assets"]:
        if not asset["name"].endswith("-x86_64-pc-windows-msvc.exe.zip"):
            continue
        prefix = asset["name"].removesuffix("-x86_64-pc-windows-msvc.exe.zip")
        if prefix not in wanted:
            continue
        expected = asset["digest"].removeprefix("sha256:")
        archive = root.parent / asset["name"]
        if not archive.exists():
            partial = archive.with_suffix(archive.suffix + ".partial")
            try:
                with opener.open(asset["browser_download_url"], timeout=120) as src, partial.open("xb") as dst:
                    shutil.copyfileobj(src, dst, 1024 * 1024)
                partial.replace(archive)
            finally:
                partial.unlink(missing_ok=True)
        with archive.open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        if actual != expected:
            raise ValueError("publisher digest mismatch")
        with zipfile.ZipFile(archive) as source:
            for item in source.infolist():
                path = inside(root.resolve(), item.filename)
                if not path.exists():
                    with source.open(item) as src, path.open("xb") as dst:
                        shutil.copyfileobj(src, dst)
        records.append({"name": asset["name"], "source": asset["browser_download_url"], "sha256": actual})
        print(json.dumps(records[-1]), flush=True)
    if {record["name"].removesuffix("-x86_64-pc-windows-msvc.exe.zip") for record in records} != wanted:
        raise ValueError("incomplete pinned Codex release assets")
    for name in ("LICENSE", "NOTICE"):
        url = f"https://api.github.com/repos/openai/codex/contents/{name}?ref=rust-v0.149.0"
        with opener.open(url, timeout=60) as source:
            content = json.load(source)
        (root / name).write_bytes(base64.b64decode(content["content"], validate=False))
    (root.parent / "codex-acquisition.json").write_text(json.dumps(records, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
