import hashlib
import importlib
import sys

import httpx
import pytest
from toolframe_sdk import (
    OwnedResource,
    PluginError,
    Resource,
    WebApp,
    WebResponse,
    WebRoute,
)

from toolframe_web.assets import AssetFile, AssetSet, seal
from toolframe_web.plugin import WebProvider
from toolframe_web.render import render


class Log:
    def bind(self, **values):
        return self

    def error(self, *args, **kwargs):
        pass


class Context:
    def __init__(self, root):
        self.state_directory = str(root)
        self.endpoints = {"web": {"max_body_bytes": 1024, "request_timeout": 1}}
        self.log = Log()
        self.calls = []
        self.denied = False

    async def resource_admit(self, key, route, **values):
        self.calls.append((key, route, values))
        if self.denied or values["access_context"] != "request":
            raise PluginError("forbidden", "Rejected")
        if values.get("asset"):
            return {"visibility": "inherit"}
        return {
            "presentation": {},
            "revision": "r1",
            "csrf": "token",
            "public_base": "/notes",
            "links": {},
        }


@pytest.fixture
async def provider(tmp_path, monkeypatch):
    root = tmp_path / "module" / "provider_example"
    (root / "web/templates").mkdir(parents=True)
    (root / "__init__.py").write_text("")
    (root / "web/templates/index.html").write_text(
        "<html>{{ text }}{{ asset_tags(ctx) }}{{ ctx.form_fields()|safe }}</html>"
    )
    monkeypatch.syspath_prepend(str(root.parent))
    module = importlib.import_module("provider_example")
    asset = AssetFile(
        path="app.js",
        size=4,
        sha256=hashlib.sha256(b"test").hexdigest(),
        media_type="text/javascript",
    )
    assets = AssetSet(seal("web/public", [asset]), {"app.js": b"test"})
    (root / "web/public").mkdir()
    (root / "web/public/app.js").write_bytes(b"test")
    (root / "web/assets.json").write_text(assets.manifest.model_dump_json())
    contexts = []

    async def index(request, ctx):
        contexts.append(ctx)
        return render(ctx, "index.html", text='<img src=x onerror="bad()">')

    async def form(request, ctx):
        value = await request.form()
        return WebResponse(str(value["body"]), headers={"Content-Type": "text/plain"})

    module.app = lambda: WebApp(
        (
            WebRoute("index", "/", ("GET",), index),
            WebRoute("save", "/", ("POST",), form),
        )
    )
    resource = OwnedResource(
        owner="example",
        generation="generation",
        resource=Resource(
            id="main",
            kind="web.app",
            data={
                "contract": 1,
                "entrypoint": "provider_example:app",
                "package": "provider_example",
                "assets": "web/assets.json",
            },
        ),
    )
    context = Context(tmp_path / "cache")
    plugin = WebProvider(context)
    plugin.limiter = __import__("anyio").CapacityLimiter(8)
    from starlette.applications import Starlette
    from starlette.routing import Route

    app = Starlette(
        routes=[
            Route(
                "/{path:path}",
                plugin.handle,
                methods=["GET", "HEAD", "POST", "OPTIONS"],
            )
        ]
    )
    await plugin.prepare([resource], "epoch")
    await plugin.commit("epoch")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://provider",
        headers={
            "x-toolframe-resource": resource.key,
            "x-toolframe-context": "request",
        },
    ) as client:
        yield plugin, client, context, contexts, resource, assets
    sys.modules.pop("provider_example", None)


async def test_render_escapes_data_and_context_ends_with_response(provider):
    _plugin, client, _context, contexts, _resource, _assets = provider
    response = await client.get("/")
    assert response.status_code == 200
    assert "&lt;img" in response.text and "<img" not in response.text
    assert 'name="_tf_csrf"' in response.text
    assert "nonce-" in response.headers["content-security-policy"]
    assert response.headers["cache-control"] == "private, no-store"
    with pytest.raises(PluginError, match="ended"):
        await contexts[0].call("example@1/list")
    denied = await client.get("/", headers={"x-toolframe-context": "forged"})
    assert denied.status_code == 403


async def test_asset_admission_precedes_conditional_response(provider):
    _plugin, client, context, _contexts, _resource, assets = provider
    path = "/_tf/assets/" + assets.manifest.digest + "/app.js"
    response = await client.get(path)
    assert response.content == b"test"
    assert response.headers["content-type"].startswith("text/javascript")
    assert (
        await client.get(path, headers={"if-none-match": response.headers["etag"]})
    ).status_code == 304
    context.denied = True
    assert (
        await client.get(path, headers={"if-none-match": response.headers["etag"]})
    ).status_code == 403


async def test_form_proofs_limits_and_method_responses(provider):
    _plugin, client, context, _contexts, _resource, _assets = provider
    response = await client.post(
        "/", data={"body": "hello", "_tf_csrf": "proof", "_tf_revision": "r1"}
    )
    assert response.status_code == 200 and response.text == "hello"
    assert context.calls[-1][2]["csrf"] == "proof"
    assert context.calls[-1][2]["revision"] == "r1"
    assert (await client.post("/", content=b"x" * 1025)).status_code == 413
    assert (await client.get("/missing")).status_code == 404
    options = await client.options("/")
    assert options.status_code == 204 and "POST" in options.headers["allow"]
    assert (await client.head("/")).content == b""


async def test_failed_prepare_does_not_change_selected_epoch(provider):
    plugin, client, _context, _contexts, resource, _assets = provider
    module = sys.modules["provider_example"]
    module.app = lambda: None
    with pytest.raises(PluginError, match="WebApp"):
        await plugin.prepare([resource], "bad")
    assert plugin.snapshot[0] == "epoch"
    assert (await client.get("/")).status_code == 200


async def test_spa_fallback_is_html_and_does_not_cover_assets_or_non_navigation(
    provider,
):
    plugin, client, context, _contexts, resource, assets = provider
    module = sys.modules["provider_example"]
    original = module.app()
    module.app = lambda: WebApp(original.routes, spa_fallback="app.js")
    with pytest.raises(PluginError, match="HTML"):
        await plugin.prepare([resource], "bad-spa")
    root = __import__("pathlib").Path(module.__file__).parent
    html = b"<!doctype html><title>SPA</title>"
    files = [
        *assets.manifest.files,
        AssetFile(
            path="index.html",
            size=len(html),
            sha256=hashlib.sha256(html).hexdigest(),
            media_type="text/html",
        ),
    ]
    replacement = seal("web/public", files)
    (root / "web/public/index.html").write_bytes(html)
    (root / "web/assets.json").write_text(replacement.model_dump_json())
    module.app = lambda: WebApp(original.routes, spa_fallback="index.html")
    await plugin.prepare([resource], "spa")
    await plugin.commit("spa")
    response = await client.get("/deep/route", headers={"accept": "text/html"})
    assert response.content == html and context.calls[-1][1] == "@spa"
    assert (
        await client.get("/deep/route", headers={"accept": "application/json"})
    ).status_code == 404
    assert (await client.post("/deep/route")).status_code == 404
    assert (
        await client.get("/_tf/assets/missing", headers={"accept": "text/html"})
    ).status_code != 200


async def test_previous_set_survives_republication_but_third_revision_is_collected(
    provider,
):
    import json
    from pathlib import Path

    plugin, client, _context, _contexts, resource, assets = provider
    root = Path(sys.modules["provider_example"].__file__).parent
    checksums = [assets.manifest.digest]
    for epoch, content in [
        ("second", b"next"),
        ("repeat", b"next"),
        ("third", b"last"),
    ]:
        item = AssetFile(
            path="app.js",
            size=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            media_type="text/javascript",
        )
        manifest = seal("web/public", [item])
        (root / "web/public/app.js").write_bytes(content)
        (root / "web/assets.json").write_text(manifest.model_dump_json())
        await plugin.prepare([resource], epoch)
        await plugin.commit(epoch)
        if epoch != "third":
            assert (
                await client.get("/_tf/assets/" + checksums[0] + "/app.js")
            ).content == b"test"
        checksums.append(manifest.digest)
    state = json.loads((plugin.cache / "selected.json").read_text())
    assert state["previous"][resource.key]["digest"] == checksums[1]
    assert not (plugin.cache / checksums[0]).exists()
    assert (plugin.cache / checksums[1]).exists()


async def test_multipart_upload_and_duplicate_fields_are_bounded(provider):
    _plugin, client, _context, _contexts, _resource, _assets = provider
    assert (
        await client.post(
            "/",
            content="body=a&body=b",
            headers={"content-type": "application/x-www-form-urlencoded"},
        )
    ).status_code == 422
    response = await client.post(
        "/",
        files={"body": ("note.txt", b"upload", "text/plain")},
        data={"_tf_csrf": "proof", "_tf_revision": "r1"},
    )
    assert response.status_code == 200 and "upload" in response.text


async def test_sync_timeout_keeps_worker_slot_until_handler_exits(provider):
    import asyncio
    import threading

    plugin, client, context, _contexts, resource, _assets = provider
    context.endpoints["web"]["request_timeout"] = 0.03
    plugin.limiter = __import__("anyio").CapacityLimiter(1)
    entered, release = threading.Event(), threading.Event()

    def slow(request, ctx):
        entered.set()
        release.wait(2)
        return WebResponse("finished")

    sys.modules["provider_example"].app = lambda: WebApp(
        (WebRoute("index", "/", ("GET",), slow),)
    )
    await plugin.prepare([resource], "slow")
    await plugin.commit("slow")
    try:
        assert (await client.get("/")).status_code == 504
        assert entered.is_set() and plugin.handlers
        assert (await client.get("/")).status_code == 429
    finally:
        release.set()
        await asyncio.gather(*plugin.handlers)
    assert not plugin.handlers


async def test_plain_response_application_requires_no_template_directory(provider):
    import shutil
    from pathlib import Path

    plugin, client, _context, _contexts, resource, _assets = provider
    module = sys.modules["provider_example"]
    shutil.rmtree(Path(module.__file__).parent / "web/templates")
    module.app = lambda: WebApp(
        (WebRoute("index", "/", ("GET",), lambda request, ctx: WebResponse("plain")),)
    )
    await plugin.prepare([resource], "plain")
    await plugin.commit("plain")
    assert (await client.get("/")).text == "plain"


@pytest.mark.parametrize("encoding", ["urlencoded", "multipart"])
@pytest.mark.parametrize("count,status", [(128, 200), (129, 422)])
async def test_form_field_limit_is_shared_by_encodings(
    provider, encoding, count, status
):
    _plugin, client, context, *_ = provider
    context.endpoints["web"]["max_body_bytes"] = 64 * 1024
    fields = {"body": "hello", **{f"f{i}": "x" for i in range(count - 1)}}
    options = {"data": fields}
    if encoding == "multipart":
        options["files"] = {"file": ("note.txt", b"upload", "text/plain")}
    response = await client.post("/", **options)
    assert response.status_code == status


@pytest.mark.parametrize("encoding", ["urlencoded", "multipart"])
async def test_denied_post_does_not_read_or_parse_body(provider, monkeypatch, encoding):
    from starlette.requests import Request

    _plugin, client, context, *_ = provider
    context.denied = True

    def forbidden(*args, **kwargs):
        pytest.fail("A denied request must not read or parse its body")

    monkeypatch.setattr(Request, "stream", forbidden)
    monkeypatch.setattr(Request, "form", forbidden)
    options = {"data": {"body": "hello"}}
    if encoding == "multipart":
        options["files"] = {"file": ("note.txt", b"upload", "text/plain")}
    assert (await client.post("/", **options)).status_code == 403
    assert context.calls[-1][2]["phase"] == "authorize"


@pytest.mark.parametrize("body", ["body=%FF", "body=%", "body=%GG", "body=x&body=y"])
async def test_urlencoded_invalid_encoding_and_duplicate_fields_fail(provider, body):
    _plugin, client, *_ = provider
    response = await client.post(
        "/",
        content=body,
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == 422


async def test_script_policy_changes_invalidate_asset_validator(provider):
    plugin, client, context, _contexts, resource, assets = provider
    admit = context.resource_admit

    async def public_asset(*args, **kwargs):
        result = await admit(*args, **kwargs)
        return {"visibility": "public"} if kwargs.get("asset") else result

    context.resource_admit = public_asset
    module = sys.modules["provider_example"]
    original = module.app()
    module.app = lambda: WebApp(original.routes, csp={"worker-src": ("'self'",)})
    await plugin.prepare([resource], "workers")
    await plugin.commit("workers")
    path = "/_tf/assets/" + assets.manifest.digest + "/app.js"
    before = await client.get(path)
    policy = before.headers["content-security-policy"]
    assert "script-src 'self'" in policy and "connect-src 'self'" in policy
    assert "sandbox" not in policy and "nonce-" not in policy
    assert before.headers["cache-control"] == "public, max-age=0, must-revalidate"
    module.app = lambda: WebApp(original.routes, csp={"connect-src": ("'none'",)})
    await plugin.prepare([resource], "restricted")
    await plugin.commit("restricted")
    after = await client.get(path, headers={"if-none-match": before.headers["etag"]})
    assert after.status_code == 200 and after.headers["etag"] != before.headers["etag"]
    assert "connect-src 'none'" in after.headers["content-security-policy"]
    assert (
        await client.get(path, headers={"if-none-match": after.headers["etag"]})
    ).status_code == 304


@pytest.mark.parametrize(
    "path,media_type", [("active.html", "text/html"), ("active.svg", "image/svg+xml")]
)
async def test_active_non_script_assets_keep_sandbox(provider, path, media_type):
    from pathlib import Path

    plugin, client, _context, _contexts, resource, assets = provider
    root = Path(sys.modules["provider_example"].__file__).parent
    content = b"<script>throw Error('must not execute')</script>"
    item = AssetFile(
        path=path,
        size=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        media_type=media_type,
    )
    manifest = seal("web/public", [*assets.manifest.files, item])
    (root / "web/public" / path).write_bytes(content)
    (root / "web/assets.json").write_text(manifest.model_dump_json())
    await plugin.prepare([resource], "active")
    await plugin.commit("active")
    response = await client.get("/_tf/assets/" + manifest.digest + "/" + path)
    assert response.status_code == 200
    assert (
        response.headers["content-security-policy"]
        == "default-src 'none'; frame-ancestors 'none'; sandbox"
    )


@pytest.mark.parametrize("identifier", ["a?b", "a#b", "香港 two words"])
async def test_provider_uses_decoded_asgi_path_without_reparsing_as_url(
    provider, identifier
):
    from toolframe_web import json

    plugin, client, _context, _contexts, resource, _assets = provider

    async def detail(request, ctx):
        return json({"id": request.path_params["id"], "query": dict(request.query)})

    route = WebRoute("detail", "/item/{id}", ("GET",), detail)
    sys.modules["provider_example"].app = lambda: WebApp((route,))
    await plugin.prepare([resource], "detail")
    await plugin.commit("detail")
    response = await client.get(route.spec().url({"id": identifier}) + "?q=a%3Fb%23c")
    assert response.status_code == 200
    assert response.json() == {"id": identifier, "query": {"q": ["a?b#c"]}}


@pytest.mark.parametrize("encoding", ["urlencoded", "multipart"])
@pytest.mark.parametrize("name,value", [("n" * 257, "x"), ("body", "雪" * 21846)])
async def test_form_name_and_value_byte_limits(provider, encoding, name, value):
    _plugin, client, context, *_ = provider
    context.endpoints["web"]["max_body_bytes"] = 1024 * 1024
    options = {"data": {name: value}}
    if encoding == "multipart":
        options["files"] = {"file": ("note.txt", b"x", "text/plain")}
    assert (await client.post("/", **options)).status_code == 422


async def test_multipart_files_close_when_proof_admission_fails(provider, monkeypatch):
    from starlette.datastructures import UploadFile

    _plugin, client, context, *_ = provider
    original_admit, original_close = context.resource_admit, UploadFile.close
    closed = []

    async def admit(*args, **kwargs):
        if kwargs.get("phase") != "authorize":
            raise PluginError("csrf_failed", "Bad proof")
        return await original_admit(*args, **kwargs)

    async def close(upload):
        await original_close(upload)
        closed.append(upload.file.closed)

    context.resource_admit = admit
    monkeypatch.setattr(UploadFile, "close", close)
    response = await client.post("/", files={"body": ("note.txt", b"x", "text/plain")})
    assert response.status_code == 403 and closed == [True]
