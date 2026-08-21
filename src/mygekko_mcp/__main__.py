"""Entrypoint for the myGEKKO MCP server.

Phase 1 ships the ``stdio`` transport (for Claude Code / local clients).
Streamable HTTP (for the Claude-app connector) is added in Phase 2.
"""

from __future__ import annotations

import argparse
import logging
import sys

from mygekko_mcp.backends import RestBackend
from mygekko_mcp.backends.tenant import TenantBackendRouter
from mygekko_mcp.config import load_settings
from mygekko_mcp.credentials import EnvCredentialProvider, RequestCredentialProvider
from mygekko_mcp.server import create_server


def main() -> int:
    parser = argparse.ArgumentParser(prog="mygekko-mcp", description="myGEKKO MCP server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http"],
        default="stdio",
        help="MCP transport: stdio (Claude Code) or streamable-http (Claude-app connector)",
    )
    args = parser.parse_args()

    settings = load_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(levelname)s %(name)s: %(message)s",
    )
    # httpx logs the full request URL (incl. the secret key= param) at INFO.
    # Silence it so credentials never land in logs.
    logging.getLogger("httpx").setLevel(logging.WARNING)

    if settings.backend != "rest":
        raise SystemExit(f"Unsupported backend '{settings.backend}' (only 'rest' in Stage 1).")

    if settings.multi_tenant or settings.token_auth_enabled:
        if args.transport != "streamable-http":
            raise SystemExit("Multi-tenant / token auth requires --transport streamable-http.")
        # Per-user credentials arrive per request (X-MyGEKKO-* headers OR a resolved
        # bearer token); no shared env identity. Both bind via request_context.
        backend: object = TenantBackendRouter(RequestCredentialProvider(), settings)
    else:
        backend = RestBackend(EnvCredentialProvider(settings), settings)
    mcp = create_server(backend, settings)

    if args.transport == "streamable-http":
        problems = settings.validate_http_ready()
        if problems:
            for p in problems:
                print(f"HTTP transport refused to start: {p}", file=sys.stderr)
            return 2
        from mygekko_mcp.transport import run_http

        run_http(mcp, settings)  # blocking
    else:
        mcp.run(transport="stdio")  # blocking
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
