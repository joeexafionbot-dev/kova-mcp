"""Pluggable credential sources.

Stage 1 (Claude Code, stdio, single user): credentials come from ``.env`` via
:class:`EnvCredentialProvider`.

Stage 2 (hosted LibreChat, multi-user): each user enters their myGEKKO
``username`` / ``key`` / ``gekkoid`` in an onboarding form; LibreChat forwards
them per session as HTTP headers. A ``RequestCredentialProvider`` (added in
Phase 2) will read those headers and return per-user credentials — the server
core here is already agnostic to *where* credentials come from.

Security invariant: a resolved :class:`Credentials` names exactly one ``gekkoid``.
The backend must only ever address that gekkoid, so a hosted user can never reach
another user's installation.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from mygekko_mcp.config import CLOUD_BASE_URL, Mode, Settings


@dataclass(frozen=True)
class Credentials:
    """Resolved credentials + connection target for a single myGEKKO."""

    mode: Mode
    base_url: str
    auth_params: dict[str, str]
    gekkoid: str  # empty in local mode
    username: str
    # The gkmcp_... bearer token that resolved this identity (token-auth mode only; None
    # otherwise). Lets a history tool call GEKKO BRAIN's own history endpoints as the SAME
    # tenant without any new context-var plumbing — see history_client.py. Deliberately
    # excluded from redacted()/fingerprint(): it must never be logged, and rotating it must
    # not change the tenant's identity for per-tenant backend caching purposes.
    raw_token: str | None = None

    def redacted(self) -> dict[str, str]:
        return {
            "mode": self.mode.value,
            "base_url": self.base_url,
            "username": self.username,
            "gekkoid": self.gekkoid or "(local)",
            "secret": "***",
        }

    def fingerprint(self) -> str:
        """A stable, non-reversible id for this exact identity.

        Used as a per-tenant cache key so that two distinct credential sets never
        share a backend — even if they point at the same installation. The secret
        is hashed, never stored or logged in the clear.
        """
        secret = self.auth_params.get("key") or self.auth_params.get("password") or ""
        blob = f"{self.mode.value}|{self.base_url}|{self.username}|{self.gekkoid}|{secret}"
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


class CredentialError(RuntimeError):
    """Raised when credentials are missing or malformed."""


@runtime_checkable
class CredentialProvider(Protocol):
    """Supplies :class:`Credentials` for the current context.

    Implementations must be cheap to call and must never log secrets.
    """

    def get(self) -> Credentials: ...


class EnvCredentialProvider:
    """Credentials from process env / ``.env`` (Stage 1, single identity)."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def get(self) -> Credentials:
        s = self._settings
        problems = s.validate_ready()
        if problems:
            raise CredentialError("; ".join(problems))
        return Credentials(
            mode=s.mode,
            base_url=s.base_url,
            auth_params=s.auth_params(),
            gekkoid=s.gekkoid if s.mode is Mode.cloud else "",
            username=s.username,
        )


class StaticCredentialProvider:
    """A provider wrapping already-resolved credentials.

    Useful for tests and as the shape a per-request provider will produce.
    """

    def __init__(self, credentials: Credentials) -> None:
        self._credentials = credentials

    def get(self) -> Credentials:
        return self._credentials


def credentials_from_headers(
    get_header: Callable[[str], str | None], *, prefix: str = "X-MyGEKKO-"
) -> Credentials:
    """Build cloud :class:`Credentials` from per-user request headers.

    ``get_header`` is a case-insensitive lookup (``header_name -> value|None``).
    Hosted multi-tenant is **cloud only** — a hosted server cannot reach a user's
    LAN, so only ``username`` / ``key`` / ``gekkoid`` are accepted. Raises
    :class:`CredentialError` (never leaking the key) if anything is missing.
    """

    def h(name: str) -> str:
        # Tolerate both case-preserving and lower-cased header getters (ASGI
        # scope headers arrive lower-cased).
        full = f"{prefix}{name}"
        value = get_header(full)
        if value is None:
            value = get_header(full.lower())
        return (value or "").strip()

    username, key, gekkoid = h("Username"), h("Key"), h("Gekkoid").upper()
    missing = [n for n, v in (("Username", username), ("Key", key), ("Gekkoid", gekkoid)) if not v]
    if missing:
        raise CredentialError(
            "missing myGEKKO credential header(s): " + ", ".join(f"{prefix}{m}" for m in missing)
        )
    if gekkoid == "XXXX-XXXX-XXXX-XXXX":
        raise CredentialError(f"{prefix}Gekkoid is a placeholder")
    return Credentials(
        mode=Mode.cloud,
        base_url=CLOUD_BASE_URL,
        auth_params={"username": username, "key": key, "gekkoid": gekkoid},
        gekkoid=gekkoid,
        username=username,
    )


def credentials_from_resolve(
    payload: dict[str, object], *, raw_token: str | None = None
) -> Credentials:
    """Build cloud :class:`Credentials` from a GEKKO BRAIN resolve response.

    ``payload`` is the JSON returned by ``POST /api/mcp-auth/resolve``:
    ``{username, key, gekkoid}``. Hosted = cloud only. Raises
    :class:`CredentialError` (never leaking the key) if a field is missing.

    ``raw_token``: the bearer token that was presented to resolve this identity — threaded
    through by :class:`~mygekko_mcp.tenant_auth.TokenResolver` so history tools can reuse it.
    """
    username = str(payload.get("username") or "").strip()
    key = str(payload.get("key") or "").strip()
    gekkoid = str(payload.get("gekkoid") or "").strip().upper()
    missing = [n for n, v in (("username", username), ("key", key), ("gekkoid", gekkoid)) if not v]
    if missing:
        raise CredentialError("resolve response missing field(s): " + ", ".join(missing))
    if gekkoid == "XXXX-XXXX-XXXX-XXXX":
        raise CredentialError("resolved gekkoid is a placeholder")
    return Credentials(
        mode=Mode.cloud,
        base_url=CLOUD_BASE_URL,
        auth_params={"username": username, "key": key, "gekkoid": gekkoid},
        gekkoid=gekkoid,
        username=username,
        raw_token=raw_token,
    )


class RequestCredentialProvider:
    """Per-request credentials pulled from the current request context.

    Used in multi-tenant hosting: an ASGI middleware resolves each request's
    credentials from headers and binds them via ``request_context``; this
    provider reads them back for the tool call in flight.
    """

    def get(self) -> Credentials:
        # imported lazily to avoid a module import cycle (request_context imports
        # Credentials from here).
        from mygekko_mcp.request_context import get_current_credentials

        creds = get_current_credentials()
        if creds is None:
            raise CredentialError(
                "no myGEKKO credentials for this request — the client must send "
                "the per-user credential headers (complete the onboarding form)."
            )
        return creds
