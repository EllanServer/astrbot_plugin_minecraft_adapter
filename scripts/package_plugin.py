from __future__ import annotations

import argparse
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

RUNTIME_PATHS = (
    "__init__.py",
    "_conf_schema.json",
    "main.py",
    "metadata.yaml",
    "requirements.txt",
    "README.md",
    "LICENSE",
    "logo.png",
    "handlers",
    "services",
)

DENIED_PREFIXES = (
    ".github/",
    "data/",
    "rust/",
    "scripts/",
    "tests/",
)

DENIED_FILES = {
    ".gitignore",
    "pyproject.toml",
}


def _as_posix(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _skip_file(path: Path) -> bool:
    parts = path.parts
    return (
        "__pycache__" in parts
        or path.suffix in {".pyc", ".pyo"}
        or path.name.endswith("~")
    )


def iter_runtime_files() -> list[Path]:
    files: list[Path] = []
    for entry in RUNTIME_PATHS:
        path = ROOT / entry
        if not path.exists():
            raise FileNotFoundError(f"required package path is missing: {entry}")
        if path.is_file():
            files.append(path)
            continue
        files.extend(
            child
            for child in sorted(path.rglob("*"))
            if child.is_file() and not _skip_file(child)
        )
    return sorted(files, key=_as_posix)


def validate_members(members: list[str]) -> None:
    forbidden = []
    for member in members:
        if member in DENIED_FILES or any(
            member.startswith(prefix) for prefix in DENIED_PREFIXES
        ):
            forbidden.append(member)
    if forbidden:
        joined = "\n".join(f"  - {member}" for member in forbidden)
        raise RuntimeError(f"plugin package contains development files:\n{joined}")


def build_package(output: Path) -> list[str]:
    output.parent.mkdir(parents=True, exist_ok=True)
    files = iter_runtime_files()
    members = [_as_posix(path) for path in files]
    validate_members(members)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as package:
        for path, member in zip(files, members, strict=True):
            package.write(path, member)
    return members


def main() -> None:
    parser = argparse.ArgumentParser(description="Build MineSentinel plugin zip.")
    parser.add_argument(
        "--output",
        default=str(ROOT / "dist" / "astrbot_plugin_minecraft_adapter.zip"),
        help="Output zip path.",
    )
    args = parser.parse_args()

    output = Path(args.output)
    if not output.is_absolute():
        output = ROOT / output
    members = build_package(output)
    print(f"wrote {output}")
    print(f"files: {len(members)}")


if __name__ == "__main__":
    main()
