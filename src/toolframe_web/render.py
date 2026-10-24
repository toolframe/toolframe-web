"""Autoescaped templates and optional document helpers."""

import html as escaping
import json as encoding

from markupsafe import Markup
from toolframe_sdk import PluginError, WebResponse


def html(body, *, status=200, headers=None):
    return WebResponse(
        body, status, {"Content-Type": "text/html; charset=utf-8", **(headers or {})}
    )


def json(value, *, status=200):
    return WebResponse(
        encoding.dumps(value, ensure_ascii=False, allow_nan=False),
        status,
        {"Content-Type": "application/json"},
    )


def redirect(url, *, status=303):
    if (
        not url.startswith("/")
        or url.startswith("//")
        or any(c in url for c in "\\\r\n\x00")
    ):
        raise PluginError("invalid_request", "Redirects must use a same-origin path")
    return WebResponse(status=status, headers={"Location": url})


def asset_tags(context):
    document = context._assets.manifest.document
    escape = lambda value: escaping.escape(value, quote=True)
    tags = [
        f'<meta name="tf-csrf" content="{escape(context.csrf_token)}">',
        f'<meta name="tf-revision" content="{escape(context.revision)}">',
    ]
    tags += [
        f'<link rel="stylesheet" href="{escape(context.asset(path))}">'
        for path in document.styles
    ]
    if document.imports:
        data = (
            encoding.dumps(
                {
                    "imports": {
                        key: context.asset(value)
                        for key, value in document.imports.items()
                    }
                }
            )
            .replace("<", "\\u003c")
            .replace(">", "\\u003e")
            .replace("&", "\\u0026")
        )
        tags.append(
            f'<script type="importmap" nonce="{escape(context.nonce)}">{data}</script>'
        )
    tags += [
        f'<script defer src="{escape(context.asset(path))}"></script>'
        for path in document.scripts
    ]
    tags += [
        f'<script type="module" src="{escape(context.asset(path))}"></script>'
        for path in document.modules
    ]
    return Markup("\n".join(tags))


def render(context, template, *, status=200, **values):
    return html(
        context._templates.get_template(template).render(
            ctx=context, asset_tags=asset_tags, **values
        ),
        status=status,
    )
