"""Local-origin enforcement and browser security headers."""

from __future__ import annotations

from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import JSONResponse

LOCAL_BROWSER_HOSTS = {"127.0.0.1", "::1", "localhost", "testserver"}


async def enforce_local_browser_origin(request: Request, call_next):
    """Reject DNS rebinding and cross-site writes, then harden every response."""
    request_host = request.url.hostname
    if request_host not in LOCAL_BROWSER_HOSTS:
        response = JSONResponse(
            {"detail": "Photo Shopping accepts requests only on a local address."},
            status_code=400,
        )
    elif request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin")
        if origin:
            parsed = urlsplit(origin)
            request_port = request.url.port or (
                443 if request.url.scheme == "https" else 80
            )
            origin_port = parsed.port or (443 if parsed.scheme == "https" else 80)
            if (
                parsed.hostname not in LOCAL_BROWSER_HOSTS
                or origin_port != request_port
            ):
                response = JSONResponse(
                    {"detail": "Cross-origin requests to the local app are blocked."},
                    status_code=403,
                )
            else:
                response = await call_next(request)
        else:
            response = await call_next(request)
    else:
        response = await call_next(request)

    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "base-uri 'self'; "
        "connect-src 'self'; "
        "font-src 'self'; "
        "form-action 'self'; "
        "frame-ancestors 'none'; "
        "img-src 'self' data: https:; "
        "object-src 'none'; "
        "script-src 'self'; "
        "style-src 'self'"
    )
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin-allow-popups"
    response.headers["Permissions-Policy"] = (
        "camera=(), microphone=(), geolocation=(), payment=()"
    )
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response
