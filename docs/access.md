# Access and operational limits

Core owns identity, canonical mount selection, CSRF, page revision, resource
policy and business service authorization. The provider receives an opaque
reference bound to the selected resource, route, method, worker, epoch and
request deadline. Direct requests to the provider's loopback listener fail
without a current Core-issued reference. Browser cookies, Authorization,
forwarded identity and client-supplied Core context headers are stripped.

Before reading or parsing a handler's body, the provider checks identity,
resource, route, method and scopes with Core. This authorization phase does not
allow service calls. Unsafe requests then parse within fixed limits and complete
admission with CSRF/revision proofs, rechecking authorization. The displayed
permissions use that admission's principal, including authenticated resources in
public mode. Each `ctx.call` checks the method's business scopes and the owner's declared
service relationship again. GET/HEAD calls must target read-only methods. Internal
and system service audiences cannot be called through Web. Resource
`access="authenticated"` additionally requires a signed-in visitor even when the
application's business mode is public. Use protected access with closed failure
policy for confidential applications.

Unsafe forms include `ctx.form_fields()`; JSON clients send `X-CSRF-Token` and
`X-Web-Revision`. Core rejects foreign Origin requests, forged references and
stale revision/epoch proofs. Session revocation is checked at each new admission.
Already-admitted domain operations can finish after the browser disconnects;
application services own idempotency and optimistic concurrency.

The provider defaults to a strict CSP: sealed same-origin scripts/styles/fonts,
same-origin connections and no frames, base element, objects or workers. App
catalogs can explicitly enable supported eval/WASM, data or blob capabilities.
External/wildcard sources and arbitrary directives are rejected. Nonces are
per-document. Choose libraries compatible with the actual selected policy and
validate them in a browser. CSP and worker processes do not sandbox trusted
plugins sharing the same origin.

Default limits are 8 MiB request bodies, 30 seconds and 64 in-flight Core Web
requests. URL-encoded and multipart forms both allow 128 ordinary fields, eight
uploads and 4 MiB per file. Names are limited to 256 UTF-8 bytes and ordinary
values to 64 KiB. Invalid URL-encoded UTF-8/percent encoding and duplicate form
names are rejected; use JSON arrays. Rendered responses are limited to 8 MiB.
At most eight synchronous handlers execute; timed-out threads retain their slots
until they exit, and their request contexts close immediately. Use asynchronous
handlers for business calls. Errors include a request ID without exposing handler
tracebacks or submitted secrets.

An unavailable app keeps its selected mount and returns 503. A Web cohort failure
does not replace the independent Streamlit cohort. Failed asset/template/dependency
preparation leaves the selected app intact. Inspect normal Core status and
operation results; never infer success from an open socket alone.

Web v1 supports finite HTTP documents, fragments, JSON, forms and static assets.
Generic WebSockets, SSE, streaming and arbitrary ASGI mounts are outside this
contract. A SPA fallback is restricted to unmatched HTML navigation within its
selected mount; reserved Core/protocol routes always retain Core ownership.
