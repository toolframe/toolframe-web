"""Response CSP derived exclusively from the validated application catalog."""

RESTRICTED_ASSET_POLICY = "default-src 'none'; frame-ancestors 'none'; sandbox"


def application_policy(catalog, *, nonce=None):
    policy = {
        "default-src": ("'none'",),
        "script-src": ("'self'",),
        "style-src": ("'self'",),
        "img-src": ("'self'", "data:"),
        "font-src": ("'self'",),
        "connect-src": ("'self'",),
        "worker-src": ("'none'",),
        "base-uri": ("'none'",),
        "object-src": ("'none'",),
        "form-action": ("'self'",),
        "frame-ancestors": ("'none'",),
        **dict(catalog.csp),
    }
    if nonce and "'none'" not in policy["script-src"]:
        policy["script-src"] = (*policy["script-src"], f"'nonce-{nonce}'")
    return "; ".join(key + " " + " ".join(values) for key, values in policy.items())


def asset_policy(catalog, item):
    # Workers use their script response's CSP. A stable policy for every script
    # avoids Sec-Fetch-Dest cache variants; HTML/SVG stay sandboxed.
    if item.media_type == "text/javascript":
        return application_policy(catalog)
    return RESTRICTED_ASSET_POLICY
