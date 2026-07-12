"""Create a release archive and adjacent SHA-256 file for an onedir bundle."""

import argparse
import hashlib
import shutil
from pathlib import Path


def archive(bundle_dir, output_base, archive_format):
    bundle_dir = Path(bundle_dir).resolve()
    output_base = Path(output_base).resolve()
    output_base.parent.mkdir(parents=True, exist_ok=True)
    archive_path = Path(shutil.make_archive(
        str(output_base), archive_format,
        root_dir=bundle_dir.parent,
        base_dir=bundle_dir.name,
    ))
    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    checksum_path = archive_path.with_name(archive_path.name + ".sha256")
    checksum_path.write_text(f"{digest}  {archive_path.name}\n", encoding="utf-8")
    return archive_path, checksum_path


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("bundle_dir")
    parser.add_argument("output_base")
    parser.add_argument("--format", choices=("gztar", "zip"), required=True)
    args = parser.parse_args(argv)
    archive_path, checksum_path = archive(
        args.bundle_dir, args.output_base, args.format
    )
    print(archive_path)
    print(checksum_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
