"""Authorized delivery of current and retained sealed assets."""

import asyncio
import hashlib

from starlette.responses import Response
from toolframe_sdk import PluginError

from .assets import AssetSet
from .policy import asset_policy


async def deliver_asset(provider, request, app, key, epoch, reference):
    asset = request.scope["path"].removeprefix("/_tf/assets/")
    admission = await provider.context.resource_admit(
        key, None, epoch=epoch, access_context=reference, asset=asset
    )
    checksum, _, path = asset.partition("/")
    assets = app.assets
    if checksum != assets.manifest.digest:
        if provider.cache is None:
            raise PluginError("web_asset_missing", "Previous asset set is unavailable")
        assets = await asyncio.to_thread(AssetSet.cached, provider.cache, checksum)
    item = next((file for file in assets.manifest.files if file.path == path), None)
    if item is None:
        raise PluginError("web_asset_missing", "Asset is not declared")
    policy = asset_policy(app.catalog, item)
    # Capabilities may change without changing asset bytes. Revalidate scripts
    # with a validator that includes the current policy, including retained sets.
    script = item.media_type == "text/javascript"
    etag = '"' + hashlib.sha256((item.sha256 + policy).encode()).hexdigest() + '"'
    cache_control = "private, no-store"
    if admission["visibility"] == "public":
        cache_control = (
            "public, max-age=0, must-revalidate"
            if script
            else "public, max-age=31536000, immutable"
        )
    headers = {
        "ETag": etag,
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": cache_control,
        "Content-Security-Policy": policy,
    }
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(
        b"" if request.method == "HEAD" else assets.content[path],
        media_type=item.media_type,
        headers=headers,
    )
