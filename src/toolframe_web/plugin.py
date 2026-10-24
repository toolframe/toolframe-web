"""HTTP adapter for prepared SDK Web applications."""

from __future__ import annotations

import asyncio
import importlib.resources
import inspect
import secrets
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from urllib.parse import parse_qs

import anyio
from jinja2 import DictLoader, Environment, PackageLoader, StrictUndefined
from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from toolframe_sdk import (
    AuthorizationSpec,
    Plugin,
    PluginDefinition,
    PluginError,
    WebApp,
    WebContext,
    WebRequest,
    WebResponse,
    load_object,
)
from toolframe_sdk.http import HttpServer
from toolframe_sdk.web import WebAppCatalog

from .asset_delivery import deliver_asset
from .assets import AssetSet
from .policy import application_policy
from .request_body import FORM_TYPES, RequestBody


@dataclass(frozen=True)
class Application:
    resource: object
    app: WebApp
    assets: AssetSet
    catalog: WebAppCatalog
    templates: Environment


ERROR_STATUS = {
    "authentication_required": 401,
    "session_invalid": 401,
    "forbidden": 403,
    "csrf_failed": 403,
    "web_asset_missing": 404,
    "web_revision_stale": 409,
    "stale_catalog": 409,
    "web_context_closed": 409,
    "invalid_request": 422,
    "request_too_large": 413,
    "rate_limited": 429,
    "presentation_unavailable": 503,
    "auth_unavailable": 503,
    "auth_transition": 503,
    "plugin_reloading": 503,
}


class WebProvider(Plugin):
    def __init__(self, context):
        super().__init__(context)
        self.server = None
        self.snapshot = (None, {})
        self.staged = {}
        self.limiter = None
        self.handlers = set()
        self.closing = False
        self.cache = Path(context.state_directory) if context.state_directory else None

    async def activate(self):
        self.limiter = anyio.CapacityLimiter(8)
        self.server = HttpServer(
            Starlette(
                routes=[
                    Route(
                        "/{path:path}",
                        self.handle,
                        methods=[
                            "GET",
                            "HEAD",
                            "POST",
                            "PUT",
                            "PATCH",
                            "DELETE",
                            "OPTIONS",
                        ],
                    )
                ]
            )
        )
        await self.server.start()

    async def health(self):
        return {
            "runtime": "ready",
            "business": "not_provided",
            "http_url": self.server.url if self.server else None,
            "capabilities": ["web-app-v1", "gateway-endpoints-v1"],
            "epoch": self.snapshot[0],
            "apps": sorted(self.snapshot[1]),
        }

    async def dispose(self):
        self.closing = True
        if self.server:
            await self.server.close()
        await super().dispose()

    async def prepare(self, resources, epoch):
        apps = {}
        total = 0
        for resource in resources:
            if resource.resource.kind != "web.app" or resource.key in apps:
                raise PluginError(
                    "unsupported_resource",
                    "Web provider requires distinct web.app resources",
                )
            data = resource.resource.data
            value = load_object(data["entrypoint"])
            app = value if isinstance(value, WebApp) else value()
            if inspect.isawaitable(app):
                app = await app
            if not isinstance(app, WebApp):
                raise PluginError(
                    "web_contract_unsupported", "Web entrypoint must return SDK WebApp"
                )
            assets = AssetSet.load(data["package"], data["assets"])
            if app.spa_fallback and not any(
                item.path == app.spa_fallback and item.media_type == "text/html"
                for item in assets.manifest.files
            ):
                raise PluginError(
                    "web_contract_unsupported",
                    "SPA fallback must name a declared HTML asset",
                )
            total += sum(item.size for item in assets.manifest.files)
            if total > 128 * 1024 * 1024:
                raise PluginError(
                    "web_assets_too_large", "Web cohort assets exceed 128 MiB"
                )
            catalog = WebAppCatalog(
                routes=tuple(route.spec() for route in app.routes),
                asset_digest=assets.manifest.digest,
                asset_paths=tuple(item.path for item in assets.manifest.files),
                asset_visibility=assets.manifest.document.visibility,
                csp=dict(app.csp),
                spa_fallback=app.spa_fallback,
            )
            template_root = importlib.resources.files(data["package"]).joinpath(
                app.templates
            )
            templates = Environment(
                loader=PackageLoader(data["package"], app.templates)
                if template_root.is_dir()
                else DictLoader({}),
                autoescape=True,
                undefined=StrictUndefined,
                auto_reload=False,
                enable_async=False,
            )
            # Compile templates before publication, so syntax errors preserve the old app.
            for name in templates.list_templates():
                templates.get_template(name)
            apps[resource.key] = Application(resource, app, assets, catalog, templates)
        self.staged[epoch] = apps
        return {
            "epoch": epoch,
            "web_apps": {
                key: app.catalog.model_dump(mode="json") for key, app in apps.items()
            },
        }

    async def commit(self, epoch):
        if epoch not in self.staged:
            raise PluginError("unknown_epoch", "No prepared Web cohort")
        apps = self.staged.pop(epoch)
        if self.cache:
            for app in apps.values():
                await asyncio.to_thread(app.assets.persist, self.cache)
            # Keep the selected and immediately preceding set for each application.
            import json

            self.cache.mkdir(parents=True, exist_ok=True)
            index = self.cache / "selected.json"
            prior = json.loads(index.read_text()) if index.exists() else {}
            old = prior.get("current", {})
            selected = {key: app.catalog.asset_digest for key, app in apps.items()}
            previous = {
                key: value
                for key, value in prior.get("previous", {}).items()
                if key in selected and value["expires"] > time.time()
            }
            for key, checksum in old.items():
                if key in selected and selected[key] != checksum:
                    previous[key] = {"digest": checksum, "expires": time.time() + 600}
            keep = set(selected.values()) | {
                value["digest"] for value in previous.values()
            }
            for item in self.cache.iterdir():
                if item.is_dir() and len(item.name) == 64 and item.name not in keep:
                    await asyncio.to_thread(shutil.rmtree, item)
            temporary = index.with_suffix(".tmp")
            temporary.write_text(
                json.dumps({"current": selected, "previous": previous})
            )
            temporary.replace(index)
        self.snapshot = (epoch, apps)
        return {"epoch": epoch}

    async def abort(self, epoch):
        self.staged.pop(epoch, None)
        return {"epoch": epoch}

    async def run_sync(self, handler, request, context):
        # A timed-out thread keeps its slot until it actually exits. New requests
        # cannot build an unbounded queue behind abandoned synchronous handlers.
        token = object()
        try:
            self.limiter.acquire_on_behalf_of_nowait(token)
        except anyio.WouldBlock:
            raise PluginError(
                "rate_limited", "Synchronous Web handlers are busy"
            ) from None

        async def execute():
            try:
                return await anyio.to_thread.run_sync(handler, request, context)
            finally:
                self.limiter.release_on_behalf_of(token)

        task = asyncio.create_task(execute())
        self.handlers.add(task)

        def completed(future):
            self.handlers.discard(future)
            if not future.cancelled():
                future.exception()

        task.add_done_callback(completed)
        return await asyncio.shield(task)

    async def handle(self, request: Request):
        epoch, apps = self.snapshot
        key = request.headers.get("x-toolframe-resource")
        reference = request.headers.get("x-toolframe-context")
        app = apps.get(key)
        if not reference or not app or self.closing:
            return JSONResponse(
                {
                    "error": {
                        "code": "forbidden",
                        "message": "Open this app through the Toolframe public URL",
                    }
                },
                status_code=403,
            )
        request_id = request.headers.get(
            "x-toolframe-request-id", secrets.token_hex(12)
        )
        context = None
        body = None
        try:
            if request.scope["path"].startswith("/_tf/assets/"):
                return await deliver_asset(self, request, app, key, epoch, reference)
            matches = [
                (route, route.spec().match(request.scope["path"]))
                for route in app.app.routes
            ]
            matches = [
                (route, params) for route, params in matches if params is not None
            ]
            if not matches:
                # Fallback remains within the selected app and never handles asset misses.
                if (
                    app.catalog.spa_fallback
                    and request.method in {"GET", "HEAD"}
                    and "text/html" in request.headers.get("accept", "")
                ):
                    route = SimpleNamespace(
                        id="@spa",
                        spec=lambda: SimpleNamespace(allowed_methods={"GET", "HEAD"}),
                        handler=lambda request, ctx: WebResponse(
                            app.assets.content[app.catalog.spa_fallback],
                            headers={"Content-Type": "text/html; charset=utf-8"},
                        ),
                    )
                    matches = [(route, {})]
                else:
                    return JSONResponse(
                        {
                            "error": {
                                "code": "web_route_missing",
                                "message": "Unknown app route",
                            }
                        },
                        status_code=404,
                    )
            selected = next(
                (
                    (route, params)
                    for route, params in matches
                    if request.method in route.spec().allowed_methods
                ),
                None,
            )
            if request.method == "OPTIONS":
                selected = matches[0]
            if selected is None:
                return Response(
                    status_code=405,
                    headers={
                        "Allow": ", ".join(
                            sorted(
                                {
                                    method
                                    for route, _ in matches
                                    for method in route.spec().allowed_methods
                                }
                                | {"OPTIONS"}
                            )
                        )
                    },
                )
            route, params = selected
            settings = self.context.endpoints.get("web", {})
            safe = request.method in {"GET", "HEAD", "OPTIONS"}
            admission = await self.context.resource_admit(
                key,
                route.id,
                epoch=epoch,
                access_context=reference,
                phase="complete" if safe else "authorize",
            )
            body = RequestBody(request, settings.get("max_body_bytes", 8 * 1024 * 1024))
            await body.read()
            if not safe:
                csrf = request.headers.get("x-csrf-token", "")
                revision = request.headers.get("x-web-revision", "")
                if body.media_type in FORM_TYPES:
                    values = await body.form()
                    csrf = csrf or values.get("_tf_csrf", "")
                    revision = revision or values.get("_tf_revision", "")
                admission = await self.context.resource_admit(
                    key,
                    route.id,
                    epoch=epoch,
                    access_context=reference,
                    csrf=csrf,
                    revision=revision,
                )
            if request.method == "OPTIONS":
                return Response(
                    status_code=204,
                    headers={
                        "Allow": ", ".join(
                            sorted(
                                {
                                    method
                                    for item, _ in matches
                                    for method in item.spec().allowed_methods
                                }
                                | {"OPTIONS"}
                            )
                        )
                    },
                )
            context = WebContext(
                self.context, app.resource, epoch, reference, admission, app.catalog
            )
            context.nonce = secrets.token_urlsafe(24)
            context._assets, context._templates = app.assets, app.templates
            value = WebRequest(
                request.method,
                request.scope["path"],
                MappingProxyType(
                    {
                        key: tuple(values)
                        for key, values in parse_qs(
                            request.scope.get("query_string", b"").decode("ascii"),
                            keep_blank_values=True,
                        ).items()
                    }
                ),
                MappingProxyType(params),
                MappingProxyType(
                    {
                        key: value
                        for key, value in request.headers.items()
                        if key not in {"cookie", "authorization"}
                        and not key.startswith("x-toolframe-")
                    }
                ),
                body.content,
                body.form,
            )
            async with asyncio.timeout(settings.get("request_timeout", 30)):
                if inspect.iscoroutinefunction(route.handler):
                    result = await route.handler(value, context)
                else:
                    result = await self.run_sync(route.handler, value, context)
            if not isinstance(result, WebResponse):
                raise TypeError("Web handler must return WebResponse")
            response_body = (
                result.body.encode() if isinstance(result.body, str) else result.body
            )
            if len(response_body) > 8 * 1024 * 1024:
                raise ValueError("Web response exceeds 8 MiB")
            headers = {
                key: value
                for key, value in result.headers.items()
                if key.lower()
                not in {
                    "content-security-policy",
                    "content-length",
                    "transfer-encoding",
                    "connection",
                }
            }
            headers.update(
                {
                    "Content-Security-Policy": application_policy(
                        app.catalog, nonce=context.nonce
                    ),
                    "Cache-Control": "private, no-store",
                    "Vary": "HX-Request, Accept",
                    "X-Web-Revision": context.revision,
                    "X-Content-Type-Options": "nosniff",
                    "Referrer-Policy": "same-origin",
                }
            )
            return Response(
                b"" if request.method == "HEAD" else response_body,
                status_code=result.status,
                headers=headers,
            )
        except PluginError as error:
            return JSONResponse(
                {
                    "error": {"code": error.code, "message": error.message},
                    "login_url": "/_toolframe/auth/login",
                    "request_id": request_id,
                },
                status_code=ERROR_STATUS.get(error.code, 400),
                headers={"Cache-Control": "no-store"},
            )
        except TimeoutError:
            return JSONResponse(
                {
                    "error": {
                        "code": "web_timeout",
                        "message": "Web request timed out",
                    },
                    "request_id": request_id,
                },
                status_code=504,
            )
        except HTTPException:
            return JSONResponse(
                {
                    "error": {
                        "code": "invalid_request",
                        "message": "Invalid form encoding or field limits",
                    },
                    "request_id": request_id,
                },
                status_code=422,
            )
        except Exception as error:  # noqa: BLE001 - never expose handler errors or filesystem paths over HTTP
            self.context.log.error(
                "Web request failed",
                extra={
                    "error.type": type(error).__name__,
                    "toolframe.web.request_id": request_id,
                },
            )
            return JSONResponse(
                {
                    "error": {"code": "web_error", "message": "Web request failed"},
                    "request_id": request_id,
                },
                status_code=500,
            )
        finally:
            if context:
                context.close()
            if body is not None:
                await body.close()


plugin = PluginDefinition(
    id="web",
    kind="provider",
    provider_kind="web",
    factory="toolframe_web.plugin:WebProvider",
    authorization=AuthorizationSpec(),
)
