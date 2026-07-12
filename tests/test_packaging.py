import hashlib
import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_release_archive_has_an_adjacent_verifiable_checksum(tmp_path):
    module = _load("archive_bundle", ROOT / "packaging" / "archive_bundle.py")
    bundle = tmp_path / "postmortem-sidecar"
    bundle.mkdir()
    (bundle / "postmortem-sidecar").write_bytes(b"packaged-binary")

    archive, checksum = module.archive(bundle, tmp_path / "release", "gztar")

    assert archive.is_file()
    assert checksum.is_file()
    digest, filename = checksum.read_text(encoding="utf-8").split()
    assert filename == archive.name
    assert digest == hashlib.sha256(archive.read_bytes()).hexdigest()


def test_pyinstaller_spec_and_release_workflow_are_checked_in():
    spec = (ROOT / "packaging" / "postmortem-sidecar.spec").read_text(encoding="utf-8")
    workflow = (ROOT / ".github" / "workflows" / "release-sidecar.yml").read_text(
        encoding="utf-8"
    )
    assert 'name="postmortem-sidecar"' in spec
    assert 'tags: ["v*"]' in workflow
    assert "Smoke bundled binary without system Python on PATH" in workflow
    assert "archive_bundle.py" in workflow
