# Authoring an HTML application

A business plugin owns one or more SDK `web.app` resources. Its entrypoint factory
returns a `WebApp`. Core selects the provider and mount; handlers execute in the
shared Web cohort with the owner's `web` extra. Business services stay in their
own workers and own data, permissions and idempotency.

## Routes and templates

```python
from toolframe_sdk import WebApp, WebRoute
from toolframe_web import render, redirect

async def index(request, ctx):
    notes = await ctx.call("notes.notes@1/list")
    return render(ctx, "index.html", notes=notes["notes"])

async def create(request, ctx):
    fields = await request.form()
    await ctx.call("notes.notes@1/create", {
        "body": fields["body"], "request_id": fields["request_id"],
    })
    return redirect(ctx.url("index"))

def app():
    return WebApp((
        WebRoute("index", "/", ("GET",), index),
        WebRoute("create", "/", ("POST",), create, ("notes.write",)),
    ))
```

Declare the matching resource with contract `1`, `entrypoint="notes.webapp:app"`,
`package="notes"` and `assets="web/assets.json"`. Put templates under
`notes/web/templates` by default, or select another package-relative directory
with `WebApp(templates=...)`. Declare this provider in the owner's `web` extra.
Use asynchronous handlers for business calls. Synchronous handlers can produce
finite responses; at most eight execute simultaneously.

```html
<!doctype html>
<html><head>{{ asset_tags(ctx) }}</head><body>
<form method="post" action="{{ ctx.url('create') }}">
  {{ ctx.form_fields()|safe }}
  <input type="hidden" name="request_id" value="{{ request_id }}">
  <textarea name="body" required>{{ value }}</textarea>
  <button>Save</button>
</form>
{% for note in notes %}<p>{{ note.body }}</p>{% endfor %}
</body></html>
```

`render` uses Jinja autoescaping and strict undefined values. Compile errors fail
preparation before publication. Keep user content escaped; the `safe` marker is
only appropriate for trusted helper output such as `form_fields`.
`asset_tags` emits the manifest's styles, import map, classic scripts and modules,
plus CSRF and Web revision metadata. `html`, `json` and `redirect` are small
response helpers. `redirect` requires a same-origin absolute path.

Use `ctx.url` for routes, `ctx.asset` for declared files and `ctx.links` for
available authorized entrances. A hardcoded `/` form action breaks under a
non-root mount. Set `expected_public_base` on a resource if a prebuilt router
requires one particular prefix. Route parameters occupy full segments; ambiguous
route/method combinations fail validation.

## Documents, fragments and libraries

Recommend htmx plus Tailwind for new forms/lists, with other libraries chosen for
the requested interaction. Framework choice remains per app. For existing apps,
keep the chosen CSS/JavaScript libraries unless the feature requires a change.
No library is injected globally by the provider.

Return a complete document for normal navigation and a fragment for `HX-Request:
true`. Disable htmx evaluation, swapped script execution, indicator inline styles
and private history snapshots when using the default CSP. The generated Notes
starter demonstrates these settings, load/dispose events for repeated swaps,
and a relative dynamic import. Keep browser Back and full-page navigation usable.

Handle 401/403, validation 422 and revision 409 explicitly. A fetch/fragment login
failure is JSON with a login URL, never an inserted login document. A revision
change requires a complete reload; do not combine a new fragment with old assets.
Unsafe JSON requests send `X-CSRF-Token` and `X-Web-Revision` from the current
context. A retry after disconnect must reuse a domain idempotency key when the
original mutation might have completed.

A prebuilt SPA can name a declared HTML asset as `spa_fallback`. It handles only
unmatched HTML GET/HEAD requests within that app. Asset misses, non-HTML requests
and Core reserved routes do not enter the fallback. Script/style references in a
prebuilt document must fit its selected base and sealed asset URLs; the provider
does not rewrite arbitrary HTML or infer client-router configuration.
