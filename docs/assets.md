# Browser asset preparation

`toolframe web prepare SOURCE` captures a plugin source tree, prepares Web files
in a separate staging directory, then builds a Python wheel. Source `add`,
`validate` and `update` use the same capture/build boundary. A direct `uv build`
does not run preparation. Runtime publication loads the sealed manifest and
verifies the wheel's bytes; it never fetches or compiles browser assets.

The Web distribution owns preparation and exposes it through the wheel's static
authoring manifest. Generated projects declare the selected package version:

```toml
[tool.toolframe.authoring]
source = "pypi:toolframe-web==0.1.1"
```

Core resolves that distribution in an isolated authoring environment, invokes its
preparation entry and builds from the resulting staged source. Use matching local
wheels when the version's authoring metadata has not been published. Older Web
recipes retain `web prepare` compatibility using the `web` extra's requirement.

## Explicit browser files

Place `web-assets.toml` at the project root:

```toml
schema_version = 1
[application]
resource = "main"
package = "example"
[document]
styles = ["app.css"]
modules = ["app.js"]
visibility = "inherit"
[[files]]
path = "app.css"
source = "web_src/app.css"
[[files]]
path = "app.js"
source = "web_src/app.js"
dependencies = ["chunks/panel.js"]
[[files]]
path = "chunks/panel.js"
source = "web_src/chunks/panel.js"
```

The import package must use a `src/` layout. Outputs default to
`src/example/web/assets.json` and `src/example/web/public/` inside staging.
Multiple apps can list separate manifest paths in
`[tool.toolframe.web] manifests = ["main-assets.toml", "admin-assets.toml"]`.
Generated paths must be distinct.

A file chooses either a local `source` or HTTPS `url` with an explicit `sha256`.
Remote inputs use versioned URLs; mutable `latest`, `main` and `master` paths are
rejected. Downloads verify TLS, resolve only public addresses, pin the checked IP
on the actual connection, and verify every byte against the hash. Declare each
third-party license as an included file and reference it through `license`.
The source URL, hash, MIME type, license and dependencies travel in the manifest.

List the complete browser-ready graph, including CSS URLs, fonts, images, workers,
WASM, dynamic imports and license files. Relative paths are preserved under
`<mount>/_tf/assets/<set-digest>/...`. The preparer verifies that declared graph
references exist; it does not parse JavaScript/CSS or fetch transitive npm
packages. Bare module names can map to declared paths using `[document.imports]`.
Classic scripts preserve manifest order; modules use the browser's module loader.

Files are limited to 8 MiB, a set to 32 MiB and 2048 paths, and the whole Web
cohort to 128 MiB. MIME types follow a bounded extension allowlist. Absolute,
ambiguous, encoded and symlink paths are refused. Only allowlisted files are
served, even if another file exists in the installed package.

## Tailwind without Node

The optional Tailwind recipe uses version 4.3.3 standalone, pinned by platform and
SHA-256 in the provider. Supported targets are macOS ARM64/x86-64 and glibc Linux
ARM64/x86-64. It invokes a fixed argument list with no shell and an empty PATH.
It never installs Node or npm. A prebuilt CSS-only manifest never requests the
compiler.

```toml
[tailwind]
version = "4.3.3"
input = "web_src/input.css"
sources = ["src/example/web/templates/index.html"]
output = "app.css"
```

Use `@import "tailwindcss" source(none);` in the CSS input. The recipe generates
source directives only for the listed files. JavaScript configs/plugins and
additional imports are excluded. Put complete class names in those files;
interpolated fragments such as `bg-${color}-500` cannot be discovered reliably.
Include the Tailwind license in the declared asset set.

Compiler output caches include input content, scanned files, compiler hash and
version. Cached downloads and outputs are rehashed before reuse. A changed input
produces a different identity. Compilation has a 60-second deadline and an 8 MiB
output limit. Missing offline inputs, unsupported platforms, hash errors or failed
compilation abort staging without selecting a new application generation.

`toolframe web prepare SOURCE --offline` requires cached remote assets and the
selected standalone compiler. Prepared wheels can run without any of them.
Python package/environment resolution has its own offline cache requirements.

## Asset revisions and access

The set digest covers exact files, metadata, dependency graph and visibility.
The selected and immediately previous sets are retained for a ten-minute grace
period during updates. Repeated publication does not extend that grace. The old
set obeys the current resource access policy and is removed when no longer
referenced. Core restart may end previous-set admission early.

`visibility="inherit"` authenticates before both bytes and 304 responses and uses
private no-store caching. Explicit `public` assets can be read without a session
while the app is enabled. Non-script files use immutable caching; JavaScript
revalidates on every use with an ETag covering bytes and current response policy.
Never place secrets or user
records in a public asset set. Handler HTML and fragments always use private no-store.

## Worker response policy

Enable `worker-src: ("'self'",)` in `WebApp.csp` to create same-origin classic or
module Workers. Declare each Worker, import, fetched asset and license in the
manifest. Worker scripts use the current app's script/connect policy because the
Worker executes under its script response's CSP. This applies to all JavaScript
responses and does not vary with `Sec-Fetch-Dest`, avoiding ambiguous cache
variants. A policy-only change changes the ETag even when the asset digest stays
the same. Retained scripts also use the current policy. WASM compilation requires
an explicit `'wasm-unsafe-eval'` script capability.

HTML, SVG and other non-script asset responses keep `default-src 'none'` and
`sandbox`; enabling a Worker does not enable active documents. Service Workers
remain outside Web contract v1. Verify selected library recipes in a browser;
declaring an asset does not establish that a library works under its CSP.
