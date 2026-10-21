"""Cross-site write guard (docs/decisions.md D-034).

Caddy asks for the basic-auth login only on writes (anything but GET, HEAD,
OPTIONS). Once a visitor has typed it, the browser re-sends it on every
request to this host, including requests a page on another site triggers —
an auto-submitting form or a `fetch(..., {mode: "no-cors"})` to /api/chat
would arrive authenticated and spend model tokens. Basic auth alone can't
tell those apart, so this guard refuses writes that a browser marks as
coming from somewhere else.

Order of evidence:
1. Sec-Fetch-Site, set by the browser and not forgeable by page script.
   Only "same-origin" (our own page) and "none" (the user typed or
   bookmarked it) pass. "same-site" is refused: a sibling *.brianmar.com
   app is a different origin.
2. Otherwise Origin, whose host[:port] must equal the Host header. Caddy's
   reverse_proxy forwards the client's Host unchanged, so both name the
   public site.
3. Neither header: curl, scripts, the deploy probe. Nothing a browser would
   send cross-site, so there is no saved login to ride on. Allowed.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from urllib.parse import urlsplit

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_ALLOWED_FETCH_SITES = frozenset({"same-origin", "none"})


def is_cross_site_write(method: str, headers: Mapping[str, str]) -> bool:
    """True when the request must be refused. `headers` keys are lower-case."""
    if method.upper() in SAFE_METHODS:
        return False

    fetch_site = headers.get("sec-fetch-site")
    if fetch_site is not None:
        return fetch_site.lower() not in _ALLOWED_FETCH_SITES

    origin = headers.get("origin")
    if origin is not None:
        host = headers.get("host")
        if not host:
            return True
        # "null" (sandboxed iframe, file://) and garbage parse to an empty
        # netloc, which never equals a real Host.
        return urlsplit(origin).netloc.lower() != host.lower()

    return False


_REFUSAL = json.dumps({"detail": "Cross-site request refused."}).encode()


class SameOriginWriteGuard:
    """Pure ASGI middleware rather than BaseHTTPMiddleware, so the SSE
    stream from /api/chat passes through untouched once a request is
    allowed."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = {
                key.decode("latin-1").lower(): value.decode("latin-1")
                for key, value in scope["headers"]
            }
            if is_cross_site_write(scope["method"], headers):
                await send(
                    {
                        "type": "http.response.start",
                        "status": 403,
                        "headers": [
                            (b"content-type", b"application/json"),
                            (b"content-length", str(len(_REFUSAL)).encode()),
                        ],
                    }
                )
                await send({"type": "http.response.body", "body": _REFUSAL})
                return
        await self.app(scope, receive, send)
