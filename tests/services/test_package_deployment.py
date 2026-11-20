from concurrent.futures import ThreadPoolExecutor
from io import StringIO
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

import pytest
from django.core.management import call_command
from lx_dtypes.models.knowledge_base.finding import Finding
from lx_dtypes.packages import create_package, load_package

from endoreg_db.services.reference_data.package_deployment import (
    build_terminology_package,
    export_terminology_package,
    import_terminology_package,
)
from endoreg_db.utils.paths import get_runtime_paths

pytestmark = pytest.mark.no_db


@pytest.fixture
def manifest(tmp_path: Path) -> Path:
    return create_package(
        tmp_path / "source",
        name="deployment_test",
        version="1.0.0",
        records=[Finding(name="lesion", name_de="Läsion", name_en="Lesion")],
    )


def test_folder_zip_roundtrip_and_command(manifest: Path) -> None:
    output = StringIO()
    call_command("terminology_packages", "build", str(manifest), "built", stdout=output)
    built = Path(output.getvalue().strip())
    assert built == get_runtime_paths().terminology / "built" / "config.yaml"
    archive = export_terminology_package(built, name="deployment.zip")
    assert archive.parent == get_runtime_paths().export_dir
    imported = import_terminology_package(archive, name="imported")
    package = load_package(imported)
    assert package.config.name == "deployment_test"
    assert "lesion" in package.finding
    with pytest.raises(FileExistsError):
        import_terminology_package(archive, name="imported")
    with pytest.raises(FileExistsError):
        export_terminology_package(built, name="deployment.zip")


def test_failed_publication_preserves_root(manifest: Path) -> None:
    root = get_runtime_paths().terminology
    before = set(root.iterdir())
    with (
        patch(
            "endoreg_db.services.reference_data.package_deployment.file_operations.atomic_create_file",
            side_effect=OSError("injected write failure"),
        ),
        pytest.raises(OSError, match="injected"),
    ):
        build_terminology_package(manifest, name="failed")
    assert set(root.iterdir()) == before


@pytest.mark.parametrize("name", ["../escape", "/escape", ".", "..", r"a\b"])
def test_rejects_unsafe_names(manifest: Path, name: str) -> None:
    with pytest.raises(ValueError, match="single path component"):
        build_terminology_package(manifest, name=name)


def test_rejects_symlink_destination(manifest: Path, tmp_path: Path) -> None:
    destination = get_runtime_paths().terminology / "linked"
    destination.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(FileExistsError):
        build_terminology_package(manifest, name="linked")


def test_concurrent_publish_has_one_winner(manifest: Path) -> None:
    def publish(_: int) -> bool:
        try:
            build_terminology_package(manifest, name="concurrent")
            return True
        except FileExistsError:
            return False

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(publish, range(2))) == [False, True]
    assert (
        load_package(
            get_runtime_paths().terminology / "concurrent" / "config.yaml"
        ).config.name
        == "deployment_test"
    )


def test_invalid_archive_never_publishes(tmp_path: Path) -> None:
    archive = tmp_path / "unsafe.zip"
    with ZipFile(archive, "w") as output:
        output.writestr("../escaped.yml", "invalid")
    with pytest.raises(ValueError):
        import_terminology_package(archive, name="unsafe")
    assert not (get_runtime_paths().terminology / "unsafe").exists()
