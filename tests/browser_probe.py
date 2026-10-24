"""Serve a synthetic browser qualification fixture from the installed provider.

Run with an installed wheel environment, then open the printed URLs in a browser.
Optional font/Alpine inputs are local browser-ready files, never runtime downloads.
"""

import argparse
import asyncio
import hashlib
import importlib
import json
import sys
import tempfile
from pathlib import Path

from starlette.applications import Starlette
from starlette.routing import Route
from toolframe_sdk import OwnedResource, Resource, WebApp, WebRoute
from toolframe_sdk.http import HttpServer

from toolframe_web import asset_tags, html
from toolframe_web.assets import AssetFile, Document, media_type, seal
from toolframe_web.plugin import WebProvider

MAIN = r"""
import { value } from 'probe-value';
const results = { import_map: value === 42 };
const output = document.querySelector('pre');
const show = () => { output.textContent = JSON.stringify(results, null, 2); };
const run = async (name, check) => {
  try { results[name] = await check(); }
  catch (error) { results[name] = { error: error.name, message: error.message }; }
  show();
};
const worker = (path, options) => new Promise(resolve => {
  const worker = new Worker(new URL(path, import.meta.url), options);
  const timeout = setTimeout(() => { worker.terminate(); resolve({ timeout: true }); }, 8000);
  const done = value => { clearTimeout(timeout); worker.terminate(); resolve(value); };
  worker.onmessage = event => done(event.data);
  worker.onerror = event => done({ error: event.message });
});
await Promise.all([
  run('dynamic_import', async () => (await import('./chunks/value.js')).value === 42),
  run('classic_worker', () => worker('./classic.js')),
  run('module_worker', () => worker('./module.js', { type: 'module' })),
  run('wasm', async () => (await WebAssembly.instantiateStreaming(fetch(new URL('./answer.wasm', import.meta.url)))).instance.exports.ans() === 42),
  run('svg_image', () => new Promise(resolve => {
    const image = new Image(); image.onload = () => resolve(image.naturalWidth === 16);
    image.onerror = () => resolve(false); image.src = new URL('./dot.svg', import.meta.url);
  })),
  run('font', async () => document.body.dataset.font === 'true' ? (await document.fonts.load('16px ProbeFont')).length > 0 : 'not requested'),
]);
results.css_url = getComputedStyle(document.querySelector('#asset-background')).backgroundImage.includes('dot.svg');
results.complete = true;
show();
"""
CLASSIC = r"""
importScripts('./chunks/classic-value.js');
(async () => {
  const result = { imported: self.child === 42 };
  try {
    result.fetched = (await (await fetch('./data.json')).json()).value === 42;
    result.wasm = (await WebAssembly.instantiateStreaming(fetch('./answer.wasm'))).instance.exports.ans() === 42;
  } catch (error) { result.blocked = error.name; }
  postMessage(result);
})();
"""
MODULE = r"""
import { value } from './chunks/value.js';
const result = { imported: value === 42 };
try {
  result.fetched = (await (await fetch(new URL('./data.json', import.meta.url))).json()).value === 42;
  result.wasm = (await WebAssembly.instantiateStreaming(fetch(new URL('./answer.wasm', import.meta.url)))).instance.exports.ans() === 42;
} catch (error) { result.blocked = error.name; }
postMessage(result);
"""


class Log:
    def bind(self, **values):
        return self

    def error(self, *args, **kwargs):
        print(args, kwargs, file=sys.stderr)


class Context:
    state_directory = None
    log = Log()

    def __init__(self):
        self.mounts = {}
        self.endpoints = {"web": {"request_timeout": 10}}

    async def resource_admit(self, key, route, **kwargs):
        if kwargs.get("asset"):
            return {"visibility": "public"}
        return {
            "presentation": {},
            "revision": "r1",
            "csrf": "synthetic",
            "public_base": self.mounts[key],
            "links": {},
        }


async def main(args):
    args.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=args.output) as directory:
        base = Path(directory)
        package = base / "browser_probe_app"
        public = package / "web/public"
        public.mkdir(parents=True)
        (package / "__init__.py").write_text("")
        content = {
            "main.js": MAIN.encode(),
            "classic.js": CLASSIC.encode(),
            "module.js": MODULE.encode(),
            "chunks/classic-value.js": b"self.child = 42;",
            "chunks/value.js": b"export const value = 42;",
            "data.json": b'{"value":42}',
            "answer.wasm": bytes.fromhex(
                "0061736d010000000105016000017f0302010007070103616e7300000a06010400412a0b"
            ),
            "dot.svg": b'<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16"><circle cx="8" cy="8" r="7" fill="#6366f1"/></svg>',
            "active.html": b"<script>document.title='unexpected-script'</script>",
            "app.css": b"body{font-family:ProbeFont,sans-serif;max-width:900px;margin:40px auto;background:#f8fafc;color:#172033}pre{padding:24px;background:#e2e8f0}#asset-background{width:32px;height:32px;background-image:url('./dot.svg')}",
        }
        scripts = ()
        if args.font:
            content["font.woff2"] = args.font.read_bytes()
            content["app.css"] += (
                b"@font-face{font-family:ProbeFont;src:url('./font.woff2')}"
            )
        if args.alpine:
            content["alpine.js"] = args.alpine.read_bytes()
            content["alpine-init.js"] = (
                b"document.addEventListener('alpine:init',()=>{Alpine.data('counter',()=>({count:0,increment(){this.count++}}))});"
            )
            scripts = ("alpine-init.js", "alpine.js")
        manifest = seal(
            "web/public",
            [
                AssetFile(
                    path=name,
                    size=len(data),
                    sha256=hashlib.sha256(data).hexdigest(),
                    media_type=media_type(name),
                )
                for name, data in content.items()
            ],
            document=Document(
                styles=("app.css",),
                scripts=scripts,
                modules=("main.js",),
                imports={"probe-value": "chunks/value.js"},
                visibility="public",
            ),
        )
        (package / "web/assets.json").write_text(manifest.model_dump_json())
        for name, data in content.items():
            target = public / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        sys.path.insert(0, str(base))
        module = importlib.import_module("browser_probe_app")

        async def index(request, ctx):
            return html(
                f'<!doctype html><title>Web asset qualification</title>{asset_tags(ctx)}<body data-font="{str(bool(args.font)).lower()}"><h1>Web asset qualification</h1><p>Synthetic data · {ctx.public_base}</p><div id="asset-background"></div><div x-data="counter"><button x-on:click="increment">Alpine CSP counter</button><span x-text="count">Not loaded</span></div><pre>waiting</pre></body>'
            )

        csp = {
            "worker-src": ("'self'",),
            "script-src": ("'self'", "'wasm-unsafe-eval'"),
        }
        module.app = lambda: WebApp((WebRoute("index", "/", ("GET",), index),), csp=csp)
        module.restricted = lambda: WebApp(
            (WebRoute("index", "/", ("GET",), index),),
            csp={**csp, "connect-src": ("'none'",)},
        )
        context = Context()
        resources = []
        for name in ("app", "restricted"):
            resource = OwnedResource(
                owner="probe",
                generation="fixture",
                resource=Resource(
                    id=name,
                    kind="web.app",
                    data={
                        "contract": 1,
                        "entrypoint": "browser_probe_app:" + name,
                        "package": "browser_probe_app",
                        "assets": "web/assets.json",
                    },
                ),
            )
            resources.append(resource)
            context.mounts[resource.key] = "/" + name
        provider = WebProvider(context)
        await provider.prepare(resources, "probe")
        await provider.commit("probe")

        async def handle(request):
            name = request.scope["path"].split("/")[1]
            resource = next(
                (value for value in resources if value.resource.id == name), None
            )
            if resource is None:
                from starlette.responses import Response

                return Response(status_code=404)
            request.scope["path"] = request.scope["path"].removeprefix("/" + name)
            request.scope["headers"] += [
                (b"x-toolframe-resource", resource.key.encode()),
                (b"x-toolframe-context", b"synthetic"),
            ]
            return await provider.handle(request)

        server = HttpServer(Starlette(routes=[Route("/{path:path}", handle)]))
        await server.start()
        print(
            json.dumps(
                {
                    "allowed_url": server.url + "/app/",
                    "restricted_url": server.url + "/restricted/",
                    "provider": str(
                        Path(importlib.import_module("toolframe_web").__file__)
                    ),
                    "asset_digest": manifest.digest,
                }
            ),
            flush=True,
        )
        try:
            await asyncio.Event().wait()
        finally:
            await server.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("build/browser-probe"))
    parser.add_argument("--font", type=Path)
    parser.add_argument("--alpine", type=Path)
    asyncio.run(main(parser.parse_args()))
