"""Per-request credential context for multi-tenant hosting (Stage 2).

In the hosted product many users share one process. Each incoming HTTP request
carries its own myGEKKO credentials (as headers); an ASGI middleware resolves
them and stashes them here for the duration of that request. Tool handlers read
them back via :class:`RequestCredentialProvider`.

A ``ContextVar`` is request-scoped and task-safe: concurrent requests each see
their own value, so one user's credentials can never bleed into another's call.
"""

from __future__ import annotations

from contextvars import ContextVar, Token

from mygekko_mcp.credentials import Credentials

_current: ContextVar[Credentials | None] = ContextVar("mygekko_current_credentials", default=None)


def set_current_credentials(creds: Credentials | None) -> Token:
    """Bind credentials to the current context; returns a token to reset with."""
    return _current.set(creds)


def get_current_credentials() -> Credentials | None:
    """The credentials bound to the current request, or None."""
    return _current.get()


def reset_current_credentials(token: Token) -> None:
    """Restore the previous context value (call in a finally block)."""
    _current.reset(token)
