"""Create a local .mcpb archive from the current workspace."""

from __future__ import annotations

import fnmatch
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
IGNORE_FILE = ROOT / ".mcpbignore"


def load_manifest_version() -> str:
    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    return manifest["version"]


def load_ignore_patterns() -> list[str]:
    patterns: list[str] = []
    for line in IGNORE_FILE.read_text(encoding="utf-8").splitlines():
        candidate = line.strip()
        if not candidate or candidate.startswith("#"):
            continue
        patterns.append(candidate.replace("\\", "/"))
    return patterns


def should_ignore(relative_path: str, patterns: list[str]) -> bool:
    normalized = relative_path.replace("\\", "/")
    directory = normalized.endswith("/")
    for pattern in patterns:
        if pattern.endswith("/"):
            prefix = pattern.rstrip("/")
            if normalized == prefix or normalized.startswith(f"{prefix}/"):
                return True
            continue
        if fnmatch.fnmatch(normalized, pattern):
            return True
        if directory and fnmatch.fnmatch(f"{normalized}/", pattern):
            return True
    return False


def iter_bundle_files(patterns: list[str]):
    for path in sorted(ROOT.rglob("*")):
        if path == DIST:
            continue
        relative = path.relative_to(ROOT).as_posix()
        if path.is_dir():
            if should_ignore(f"{relative}/", patterns):
                continue
            continue
        if should_ignore(relative, patterns):
            continue
        yield path, relative


def build_archive() -> Path:
    DIST.mkdir(exist_ok=True)
    output = DIST / f"mcp-google-workspace-{load_manifest_version()}.mcpb"
    patterns = load_ignore_patterns()
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        for path, relative in iter_bundle_files(patterns):
            archive.write(path, arcname=relative)
    return output


def main() -> None:
    output = build_archive()
    print(output)


if __name__ == "__main__":
    main()
