"""Create a local .mcpb archive from the current workspace."""

from __future__ import annotations

import fnmatch
import json
import os
import shutil
import subprocess
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
IGNORE_FILE = ROOT / ".mcpbignore"
UI_DIR = ROOT / "src" / "mcp_google_workspace" / "apps" / "ui"


def build_apps_ui() -> None:
    """Rebuild the tracked Apps artifact from the exact lock before packaging."""
    npm = shutil.which("npm.cmd") or shutil.which("npm")
    if npm is None:
        raise RuntimeError("npm is required to build the MCP Apps dashboard UI.")
    subprocess.run([npm, "ci"], cwd=UI_DIR, check=True)
    subprocess.run([npm, "run", "build"], cwd=UI_DIR, check=True)
    if not (UI_DIR / "dist" / "index.html").is_file():
        raise RuntimeError("MCP Apps UI build did not produce dist/index.html.")


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
            if fnmatch.fnmatch(normalized, prefix) or fnmatch.fnmatch(
                normalized, f"{prefix}/*"
            ):
                return True
            continue
        if fnmatch.fnmatch(normalized, pattern):
            return True
        if directory and fnmatch.fnmatch(f"{normalized}/", pattern):
            return True
    return False


def iter_bundle_files(patterns: list[str]):
    for current, directories, filenames in os.walk(ROOT, topdown=True):
        current_path = Path(current)
        kept_directories: list[str] = []
        for directory in sorted(directories):
            path = current_path / directory
            relative = path.relative_to(ROOT).as_posix()
            if path == DIST or should_ignore(f"{relative}/", patterns):
                continue
            kept_directories.append(directory)
        directories[:] = kept_directories
        for filename in sorted(filenames):
            path = current_path / filename
            relative = path.relative_to(ROOT).as_posix()
            if not should_ignore(relative, patterns):
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
    build_apps_ui()
    output = build_archive()
    print(output)


if __name__ == "__main__":
    main()
