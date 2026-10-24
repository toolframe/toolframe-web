# Toolframe Web

Toolframe Web serves Python-authored HTML applications through Toolframe's public
URL. Each application owns its templates, browser libraries and styles, and calls
business services with the visitor's existing permissions.

## Quick start

Use Python 3.14+, Core 0.8 and the matching local SDK `0.4.0+web.1` wheel. Point
Core's package settings and `TOOLFRAME_WHEELHOUSE` at the supplied wheelhouse.
Against an existing running application named `APP`, create and prepare a business plugin:

```sh
toolframe --home APP plugin new notes --presentation web
toolframe --home APP web prepare APP/plugins/notes
toolframe --home APP plugin add pypi:toolframe-sql-sqlite==0.4.0 --enable
toolframe --home APP plugin add pypi:toolframe-web==0.1.0 --enable
# Install the wheel path printed by prepare:
toolframe --home APP plugin add APP/plugins/notes/build/web/toolframe_notes-0.1.0-py3-none-any.whl --enable
```

Select `schema_version = 2` and explicitly mount `web.notes.main` in the
application's `toolframe.toml`, then apply it:

```toml
[gateway.mounts."web.notes.main"]
prefix = "/"
```

```sh
toolframe --home APP apply
```

See [authoring](docs/authoring.md) for routes and forms, and
[browser assets](docs/assets.md) for preparation and optional Tailwind.

Web applications share a dedicated Python environment, independently of business
workers and Streamlit. They are trusted code on the application's origin. This
provider supports finite HTTP handlers, forms, JSON and sealed static assets.
General WebSockets, SSE, streaming responses and arbitrary ASGI mounts are outside
Web contract v1. See [access and limits](docs/access.md).

Browser-ready files need no Node or npm. The recommended new-app recipe uses
htmx and an optional pinned Tailwind standalone compiler. Installed wheels serve
local assets without compilation or CDN requests. Applications may instead use
ordinary CSS and other browser-ready libraries.

This package is Apache-2.0 licensed; see [LICENSE](LICENSE) and [NOTICE](NOTICE).
Third-party assets retain their own licenses. Development instructions are in
[Contributing](CONTRIBUTING.md).
