"""Download the exact Windows wheelhouse recorded in native-wheels.lock.json."""

import hashlib
import json
from pathlib import Path
import shutil
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.desktop.slim_native_runtime import REMOVED_DISTRIBUTIONS, canonical_name

LOCK = ROOT / "scripts/desktop/native-wheels.lock.json"


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main() -> None:
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    destination = ROOT / ".desktop-runtime-tests/native-build/wheels"
    destination.mkdir(parents=True, exist_ok=True)
    expected = {item["filename"]: item["sha256"] for item in lock["wheels"]
                if canonical_name(item["filename"].split("-", 1)[0]) not in REMOVED_DISTRIBUTIONS}
    if len(expected) != sum(canonical_name(item["filename"].split("-", 1)[0]) not in REMOVED_DISTRIBUTIONS
                            for item in lock["wheels"]):
        raise ValueError("duplicate wheel in lock")
    for existing in destination.glob("*.whl"):
        if existing.name not in expected:
            raise ValueError(f"unexpected wheel in build directory: {existing.name}")

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for filename, checksum in expected.items():
        target = destination / filename
        if target.exists():
            if digest(target) != checksum:
                raise ValueError(f"cached wheel hash mismatch: {filename}")
            continue
        distribution, version = filename.split("-", 2)[:2]
        request = urllib.request.Request(
            f"https://pypi.org/pypi/{distribution}/{version}/json",
            headers={"User-Agent": "RecruitOps-release-builder"},
        )
        with opener.open(request, timeout=60) as response:
            releases = json.load(response)["urls"]
        matches = [item for item in releases if item["filename"] == filename]
        if len(matches) != 1 or matches[0]["digests"]["sha256"] != checksum:
            raise ValueError(f"pinned wheel unavailable or changed: {filename}")
        partial = target.with_suffix(".partial")
        try:
            with opener.open(matches[0]["url"], timeout=120) as source, partial.open("xb") as output:
                shutil.copyfileobj(source, output, 1024 * 1024)
            if digest(partial) != checksum:
                raise ValueError(f"downloaded wheel hash mismatch: {filename}")
            partial.replace(target)
        finally:
            partial.unlink(missing_ok=True)
        print(f"Verified {filename}", flush=True)
    selected = {**lock, "wheels": [item for item in lock["wheels"] if item["filename"] in expected]}
    (destination.parent / "wheels.json").write_text(json.dumps(selected, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
