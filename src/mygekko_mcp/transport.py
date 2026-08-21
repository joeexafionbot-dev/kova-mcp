"""Streamable-HTTP transport for the Claude-app connector (Phase 2).

FastMCP serves the MCP protocol over a Starlette ASGI app. We wrap that app with
a constant-time **bearer-token** check so the connector URL is useless without the
token (treat the token like a password). TLS is terminated by a reverse proxy in
front of this — the server binds to loopback by default and must never be exposed
on a public port directly (see docs/connector.md).
"""

from __future__ import annotations

import hmac
import logging

from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from mygekko_mcp.config import Settings
from mygekko_mcp.credentials import CredentialError, credentials_from_headers
from mygekko_mcp.request_context import reset_current_credentials, set_current_credentials
from mygekko_mcp.tenant_auth import TokenResolver

logger = logging.getLogger("mygekko_mcp.transport")


class BearerAuthMiddleware(BaseHTTPMiddleware):
    """Reject any request without a valid ``Authorization: Bearer <token>``."""

    def __init__(self, app: object, token: str) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self._token = token

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        header = request.headers.get("authorization", "")
        scheme, _, credential = header.partition(" ")
        # constant-time compare; empty configured token is handled by the caller
        if scheme.lower() != "bearer" or not hmac.compare_digest(credential, self._token):
            return JSONResponse(
                {"error": "unauthorized"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
        return await call_next(request)


class TenantCredentialsMiddleware:
    """Pure-ASGI middleware: resolve per-user myGEKKO credentials from headers.

    Runs in the same task that awaits the inner app, so the ContextVar it sets is
    visible to the tool handler downstream. Missing/invalid headers are not
    rejected here — they surface later as a clear CredentialError on the first
    tool that needs them (health checks and listing still work).
    """

    def __init__(self, app: object, prefix: str = "X-MyGEKKO-") -> None:
        self._app = app
        self._prefix = prefix

    async def __call__(self, scope: dict, receive: object, send: object) -> None:
        if scope.get("type") != "http":
            await self._app(scope, receive, send)  # type: ignore[operator]
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        try:
            creds = credentials_from_headers(headers.get, prefix=self._prefix)
        except CredentialError:
            creds = None  # defer the error to the tool call for a clear message
        token = set_current_credentials(creds)
        try:
            await self._app(scope, receive, send)  # type: ignore[operator]
        finally:
            reset_current_credentials(token)


class TokenCredentialsMiddleware:
    """Pure-ASGI: resolve the request's ``Authorization: Bearer`` token → per-user creds.

    The seamless GEKKO BRAIN bridge — the token both authenticates and identifies
    the tenant. **One instance serves both clients:** external agents (a resolvable
    ``gkmcp_…`` token) AND a co-hosted LibreChat (per-user ``X-MyGEKKO-*`` header
    creds), because when the bearer token does not resolve we fall back to the
    header path. A missing/invalid identity is not rejected here; it surfaces as a
    clear CredentialError on the first tool that needs the controller (schema
    listing still works).
    """

    def __init__(self, app: object, resolver: TokenResolver, prefix: str = "X-MyGEKKO-") -> None:
        self._app = app
        self._resolver = resolver
        self._prefix = prefix

    async def __call__(self, scope: dict, receive: object, send: object) -> None:
        if scope.get("type") != "http":
            await self._app(scope, receive, send)  # type: ignore[operator]
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        scheme, _, credential = headers.get("authorization", "").partition(" ")
        creds = None
        if scheme.lower() == "bearer" and credential:
            try:
                creds = await self._resolver.resolve(credential)
            except CredentialError:
                creds = None  # not a valid token → try header creds below
        if creds is None:
            try:
                creds = credentials_from_headers(headers.get, prefix=self._prefix)
            except CredentialError:
                creds = None  # defer to the tool call for a clear message
        token = set_current_credentials(creds)
        try:
            await self._app(scope, receive, send)  # type: ignore[operator]
        finally:
            reset_current_credentials(token)


def build_http_app(mcp: object, settings: Settings) -> Starlette:
    """Return the FastMCP streamable-HTTP ASGI app, guarded by bearer auth.

    ``mcp`` is a FastMCP instance (typed loosely to avoid importing it here).
    """
    mcp.settings.host = settings.http_host  # type: ignore[attr-defined]
    mcp.settings.port = settings.http_port  # type: ignore[attr-defined]
    mcp.settings.streamable_http_path = settings.http_path  # type: ignore[attr-defined]
    mcp.settings.stateless_http = settings.http_stateless  # type: ignore[attr-defined]

    app: Starlette = mcp.streamable_http_app()  # type: ignore[attr-defined]

    # Seamless token auth: the per-tenant bearer token IS both the gate and the
    # identity — it is resolved to that tenant's creds via GEKKO BRAIN. Replaces
    # the shared-bearer + header-credentials path (PRODUCTS.md).
    if settings.token_auth_enabled:
        resolver = TokenResolver(
            settings.resolve_url,
            settings.resolve_secret.get_secret_value(),
            ttl=settings.resolve_cache_ttl,
            cache_max=settings.resolve_cache_max,
            timeout_seconds=settings.timeout_seconds,
        )
        app.add_middleware(
            TokenCredentialsMiddleware,
            resolver=resolver,
            prefix=settings.tenant_header_prefix,
        )
        logger.info(
            "Seamless token auth ON: per-tenant bearer resolved via %s "
            "(falls back to '%s*' headers).",
            settings.resolve_url,
            settings.tenant_header_prefix,
        )
        return app

    # Inner: bind per-user credentials from headers (multi-tenant hosting).
    if settings.multi_tenant:
        app.add_middleware(TenantCredentialsMiddleware, prefix=settings.tenant_header_prefix)
        logger.info(
            "Multi-tenant mode ON: per-user credentials via '%s*' headers.",
            settings.tenant_header_prefix,
        )

    # Outer: bearer-token gate (added last => runs first, rejecting before we
    # even parse credential headers).
    token = settings.http_token.get_secret_value()
    if token:
        app.add_middleware(BearerAuthMiddleware, token=token)
        logger.info("HTTP bearer-token auth enabled.")
    else:
        logger.warning(
            "HTTP transport running WITHOUT auth (GEKKO_HTTP_ALLOW_NO_AUTH). "
            "Use only in a trusted local network."
        )
    return app


def run_http(mcp: object, settings: Settings) -> None:
    """Serve the streamable-HTTP app with uvicorn (blocking)."""
    import uvicorn

    app = build_http_app(mcp, settings)
    logger.info(
        "Serving MCP over streamable-http at http://%s:%d%s (behind TLS proxy)",
        settings.http_host,
        settings.http_port,
        settings.http_path,
    )
    uvicorn.run(
        app,
        host=settings.http_host,
        port=settings.http_port,
        log_level=settings.log_level.lower(),
    )
