import hashlib
import json
import sys

import pytest

from toolframe_web.assets import (
    AssetFile,
    AssetSet,
    Document,
    Manifest,
    safe_file,
    seal,
)
from toolframe_web.prepare import stage


def source_tree(tmp_path):
    source = tmp_path / "source"
    package = source / "src/example_web"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (source / "pyproject.toml").write_text(
        '[project]\nname="example-web"\nversion="1.0"\n'
    )
    (source / "web_src/chunks").mkdir(parents=True)
    (source / "web_src/app.js").write_text(
        'export const load = () => import("./chunks/panel.js");'
    )
    (source / "web_src/chunks/panel.js").write_text('export const text = "Ready";')
    (source / "web-assets.toml").write_text("""schema_version=1
[application]
resource="main"
package="example_web"
[document]
modules=["app.js"]
[[files]]
path="app.js"
source="web_src/app.js"
dependencies=["chunks/panel.js"]
[[files]]
path="chunks/panel.js"
source="web_src/chunks/panel.js"
""")
    return source


def test_preparation_preserves_graph_and_source_and_is_deterministic(
    tmp_path, monkeypatch
):
    source = source_tree(tmp_path)
    first = stage(source, tmp_path / "first", tmp_path / "cache", offline=True)
    second = stage(source, tmp_path / "second", tmp_path / "cache", offline=True)
    assert first["applications"] == second["applications"]
    assert not (source / "src/example_web/web/assets.json").exists()
    monkeypatch.syspath_prepend(str(tmp_path / "first/src"))
    assets = AssetSet.load("example_web", "web/assets.json")
    assert b"./chunks/panel.js" in assets.content["app.js"]
    assert set(assets.content) == {"app.js", "chunks/panel.js"}
    assets.persist(tmp_path / "sealed")
    assert (
        AssetSet.cached(tmp_path / "sealed", assets.manifest.digest).content
        == assets.content
    )
    (tmp_path / "sealed" / assets.manifest.digest / "public/app.js").write_bytes(
        b"tampered"
    )
    with pytest.raises(ValueError, match="size mismatch"):
        AssetSet.cached(tmp_path / "sealed", assets.manifest.digest)
    sys.modules.pop("example_web", None)


def test_manifest_hash_includes_visibility_and_declared_dependencies():
    file = AssetFile(
        path="app.js",
        size=0,
        sha256=hashlib.sha256(b"").hexdigest(),
        media_type="text/javascript",
    )
    private = seal("web/public", [file])
    public = seal("web/public", [file], document=Document(visibility="public"))
    assert private.digest != public.digest
    value = json.loads(private.model_dump_json())
    value["document"]["visibility"] = "public"
    with pytest.raises(ValueError, match="digest mismatch"):
        Manifest.model_validate(value)
    with pytest.raises(ValueError, match="undeclared"):
        seal("web/public", [file], document=Document(modules=("missing.js",)))


def test_public_files_cannot_escape_or_lie_about_mime(tmp_path):
    (tmp_path / "outside").write_text("secret")
    root = tmp_path / "root"
    root.mkdir()
    (root / "link.txt").symlink_to(tmp_path / "outside")
    for path in ("../outside", "link.txt", "%2e%2e/outside", "a//b"):
        with pytest.raises(ValueError):
            safe_file(root, path)
    with pytest.raises(ValueError, match="MIME"):
        AssetFile(path="app.js", sha256="a" * 64, size=0, media_type="text/html")
    with pytest.raises(ValueError, match="distinct"):
        seal(
            "web/public",
            [
                AssetFile(
                    path=name, sha256="a" * 64, size=0, media_type="text/javascript"
                )
                for name in ("a.js", "A.js")
            ],
        )


def test_failed_preparation_keeps_source_and_removes_partial_output(tmp_path):
    source = source_tree(tmp_path)
    (source / "web_src/chunks/panel.js").unlink()
    with pytest.raises(OSError):
        stage(source, tmp_path / "staging", tmp_path / "cache")
    assert not (tmp_path / "staging").exists()
    assert (source / "web_src/app.js").is_file()


def test_source_symlink_is_rejected(tmp_path):
    source = source_tree(tmp_path)
    (source / "escape").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        stage(source, tmp_path / "staging", tmp_path / "cache")


def test_downloads_fail_closed_offline_and_for_private_addresses(tmp_path, monkeypatch):
    from toolframe_web.prepare import _address, fetch

    with pytest.raises(ValueError, match="Offline"):
        fetch("https://example.com/v1/app.js", "a" * 64, tmp_path, offline=True)
    for url in (
        "file:///tmp/secret",
        "http://example.com/x",
        "https://user:secret@example.com/x",
        "https://example.com:444/x",
    ):
        with pytest.raises(ValueError):
            _address(url)
    monkeypatch.setattr(
        "socket.getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("127.0.0.1", 443))]
    )
    with pytest.raises(ValueError, match="private"):
        _address("https://example.com/v1/x")


def test_cached_download_is_rehashed(tmp_path):
    from toolframe_web.prepare import fetch

    target = tmp_path / "downloads" / ("a" * 64)
    target.parent.mkdir()
    target.write_text("wrong bytes")
    with pytest.raises(ValueError, match="integrity"):
        fetch("https://example.com/v1/x", "a" * 64, tmp_path, offline=True)
