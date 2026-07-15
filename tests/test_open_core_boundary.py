import importlib
import importlib.util
import pkgutil
import tarfile
from pathlib import Path

import postmortem
import pytest


ROOT = Path(__file__).resolve().parents[1]


def _artifact_verifier():
    path = ROOT / "packaging" / "verify_open_core_artifact.py"
    spec = importlib.util.spec_from_file_location("verify_open_core_artifact", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_free_runtime_imports_without_private_licensing_code():
    package_root = Path(postmortem.__file__).parent

    assert not (package_root / "licensing.py").exists()
    for module in pkgutil.walk_packages(
        postmortem.__path__, prefix=f"{postmortem.__name__}."
    ):
        if module.name.endswith(".__main__"):
            continue
        importlib.import_module(module.name)


def test_public_sources_never_import_the_private_license_module():
    package_root = Path(postmortem.__file__).parent

    for source in package_root.rglob("*.py"):
        text = source.read_text(encoding="utf-8")
        assert "import licensing" not in text
        assert "from licensing" not in text
        assert "postmortem.licensing" not in text


def test_public_release_artifact_verifier_accepts_free_runtime(tmp_path):
    bundle = tmp_path / "postmortem-sidecar"
    (bundle / "postmortem").mkdir(parents=True)
    (bundle / "postmortem" / "service.py").write_text(
        "FREE_RUNTIME = True\n", encoding="utf-8"
    )

    _artifact_verifier().verify_artifact(bundle)


@pytest.mark.parametrize(
    "private_path",
    [
        Path("postmortem") / "licensing.py",
        Path("panel") / "strings.lua",
        Path("licensing") / "public_keys.json",
    ],
)
def test_public_release_artifact_verifier_rejects_private_code(
    tmp_path, private_path
):
    source = tmp_path / "source"
    target = source / private_path
    target.parent.mkdir(parents=True)
    target.write_text("PRIVATE = True\n", encoding="utf-8")
    archive = tmp_path / "release.tar.gz"
    with tarfile.open(archive, "w:gz") as output:
        output.add(source, arcname="release")

    with pytest.raises(ValueError, match="private product-shell (file|path)"):
        _artifact_verifier().verify_artifact(archive)


def test_artifact_directory_recursively_checks_built_archives(tmp_path):
    source = tmp_path / "source"
    (source / "panel").mkdir(parents=True)
    (source / "panel" / "strings.lua").write_text(
        "return {}\n", encoding="utf-8"
    )
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    archive = artifacts / "public-release.tar.gz"
    with tarfile.open(archive, "w:gz") as output:
        output.add(source, arcname="release")

    with pytest.raises(ValueError, match="private product-shell path"):
        _artifact_verifier().verify_artifact(artifacts)
