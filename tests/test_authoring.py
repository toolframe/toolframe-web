"""The Web distribution owns its complete FSD Notes template and preparation entry."""

import json
import tomllib
from importlib.metadata import distribution
from importlib.resources import files

from toolframe_web.authoring import generate, prepare


def test_manifest_and_generator_are_packaged_without_source_pyproject(tmp_path):
    installed = distribution("toolframe-web")
    manifest = json.loads(
        installed.read_text("extra_metadata/toolframe-authoring.json")
    )
    assert manifest["templates"][0]["id"] == "notes"
    assert manifest["requires_core"] == ">=0.8,<1"
    assert manifest["prepare"]["entrypoint"] == "toolframe_web.authoring:prepare"
    generate(destination=tmp_path, parameters={"name": "customer-notes"})
    project = tomllib.loads((tmp_path / "pyproject.toml").read_text())
    assert project["project"]["name"] == "toolframe-customer-notes"
    assert project["project"]["dependencies"] == ["toolframe-sdk>=0.4.1,<1"]
    assert project["project"]["optional-dependencies"]["web"] == [
        f"toolframe-web=={installed.version}"
    ]
    assert (
        project["tool"]["toolframe"]["authoring"]["source"]
        == f"pypi:toolframe-web=={installed.version}"
    )
    assert (tmp_path / "LICENSE").read_bytes() == files("toolframe_web").joinpath(
        "templates/notes/LICENSE"
    ).read_bytes()
    recipe = tomllib.loads((tmp_path / "web-assets.toml").read_text())
    assert recipe["application"]["package"] == "toolframe_customer_notes"
    assert all(
        "toolframe_customer_notes" in path for path in recipe["tailwind"]["sources"]
    )
    root = tmp_path / "src/toolframe_customer_notes"
    assert (root / "web/app/__init__.py").is_file()
    assert (root / "web/pages/notes/api/__init__.py").is_file()
    assert "customer-notes.write" in (root / "web/app/__init__.py").read_text()
    assert "toolframe_web" not in (root / "service.py").read_text()
    assert not (root / "web/assets.json").exists()


def test_preparation_entry_delegates_to_owner(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        "toolframe_web.prepare.stage",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    prepare(
        source=tmp_path / "source",
        destination=tmp_path / "target",
        cache=tmp_path / "cache",
        offline=True,
    )
    assert calls == [
        (
            (tmp_path / "source", tmp_path / "target", tmp_path / "cache"),
            {"offline": True},
        )
    ]
