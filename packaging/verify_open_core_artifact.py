"""Fail if a public release artifact contains private product-shell code."""

from __future__ import annotations

import argparse
import tarfile
import zipfile
from collections.abc import Iterable, Iterator
from pathlib import Path, PurePosixPath


PRIVATE_FILENAMES = {
    "license-public-keys.json",
    "licensing.py",
    "post_mortem_panel.lua",
}
PRIVATE_PATH_PARTS = {"licensing", "panel"}
PRIVATE_BYTE_MARKERS = (
    b"postmortem.licensing",
    b"from licensing import",
    b"import licensing",
    b"post_mortem_panel.lua",
    b"license-public-keys.json",
)


def _check_member(name: str, content: bytes) -> None:
    normalized = PurePosixPath(name.replace("\\", "/"))
    lowered_parts = tuple(part.lower() for part in normalized.parts)
    if any(part in PRIVATE_PATH_PARTS for part in lowered_parts[:-1]):
        raise ValueError(f"private product-shell path found: {name}")
    if any(part in PRIVATE_FILENAMES for part in lowered_parts):
        raise ValueError(f"private product-shell file found: {name}")
    is_runtime = "postmortem" in lowered_parts or any(
        part == "postmortem-sidecar" or part.startswith("postmortem-sidecar.")
        for part in lowered_parts
    )
    if is_runtime:
        for marker in PRIVATE_BYTE_MARKERS:
            if marker in content:
                raise ValueError(
                    f"private product-shell marker {marker.decode()} found in {name}"
                )


def _directory_members(path: Path) -> Iterator[tuple[str, bytes]]:
    for member in sorted(item for item in path.rglob("*") if item.is_file()):
        relative = member.relative_to(path).as_posix()
        yield f"{path.name}/{relative}", member.read_bytes()


def _zip_members(path: Path) -> Iterator[tuple[str, bytes]]:
    with zipfile.ZipFile(path) as archive:
        for member in archive.infolist():
            if not member.is_dir():
                yield member.filename, archive.read(member)


def _tar_members(path: Path) -> Iterator[tuple[str, bytes]]:
    with tarfile.open(path, mode="r:*") as archive:
        for member in archive.getmembers():
            if member.isfile():
                extracted = archive.extractfile(member)
                if extracted is None:
                    raise ValueError(f"cannot read release member: {member.name}")
                yield member.name, extracted.read()


def verify_artifact(path: Path) -> None:
    """Verify a directory, wheel/zip, or tar release is open-core only."""

    path = Path(path)
    if path.is_dir():
        for child in sorted(item for item in path.rglob("*") if item.is_file()):
            if zipfile.is_zipfile(child) or tarfile.is_tarfile(child):
                verify_artifact(child)
        members: Iterable[tuple[str, bytes]] = _directory_members(path)
    elif zipfile.is_zipfile(path):
        members = _zip_members(path)
    elif tarfile.is_tarfile(path):
        members = _tar_members(path)
    else:
        raise ValueError(f"unsupported release artifact: {path}")
    for name, content in members:
        _check_member(name, content)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify public artifacts contain no private panel/licensing code."
    )
    parser.add_argument("artifacts", nargs="+", type=Path)
    args = parser.parse_args(argv)
    try:
        for artifact in args.artifacts:
            verify_artifact(artifact)
    except (OSError, tarfile.TarError, ValueError, zipfile.BadZipFile) as exc:
        raise SystemExit(f"open-core artifact check failed: {exc}") from exc
    print(f"Open-core artifact check passed for {len(args.artifacts)} artifact(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
