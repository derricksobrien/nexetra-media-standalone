"""Deterministic source, job, and dependency provenance hashes."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIRS = ("pipeline", "jobs", "viewer")
SOURCE_FILES = ("config.yaml", "requirements.txt")
DEPENDENCY_FILES = ("requirements.txt", "viewer/requirements.txt")
SOURCE_MANIFEST = ".nexetra-source-manifest.json"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _hash_paths(paths: Iterable[Path], root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        content = path.read_bytes()
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def source_files(root: Path = ROOT) -> list[Path]:
    manifest = root / SOURCE_MANIFEST
    if manifest.is_file():
        try:
            values = json.loads(manifest.read_text(encoding="utf-8"))
            return [root / value for value in values.get("files", []) if (root / value).is_file()]
        except Exception:
            pass
    files: list[Path] = []
    for directory in SOURCE_DIRS:
        base = root / directory
        if base.exists():
            files.extend(
                path
                for path in base.rglob("*")
                if path.is_file()
                and "__pycache__" not in path.parts
                and not path.name.endswith((".pyc", ".pyo"))
                and not (directory == "pipeline" and (path.name.startswith("qualify_") or path.name == "capture_stage0_baseline.py"))
            )
    files.extend(root / name for name in SOURCE_FILES if (root / name).is_file())
    return files


def source_sha(root: Path = ROOT) -> str:
    return _hash_paths(source_files(root), root)


def dependency_sha(root: Path = ROOT) -> str:
    paths = [root / name for name in DEPENDENCY_FILES if (root / name).is_file()]
    return _hash_paths(paths, root)


def build_provenance(job_path: Path, root: Path = ROOT) -> dict[str, str]:
    return {
        "source_sha": source_sha(root),
        "job_sha": sha256_file(job_path),
        "dependency_sha": dependency_sha(root),
    }
