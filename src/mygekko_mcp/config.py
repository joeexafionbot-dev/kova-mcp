"""Configuration loaded from environment / .env via pydantic-settings.

The connection secret (``key`` for cloud, ``password`` for local) is wrapped in a
type that redacts itself in ``repr`` / logs. Never log ``settings`` naively — use
``settings.redacted()`` if you need a dict.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

CLOUD_BASE_URL = "https://live.my-gekko.com/api/v1"


class Mode(StrEnum):
    cloud = "cloud"
    local = "local"


class Settings(BaseSettings):
    """Runtime configuration. Fields map to ``GEKKO_*`` environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="GEKKO_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- backend / mode ---
    backend: Literal["rest"] = "rest"
    mode: Mode = Mode.cloud

    # --- shared identity ---
    username: str = ""

    # --- cloud ---
    key: SecretStr = SecretStr("")
    gekkoid: str = ""

    # --- local ---
    local_host: str = ""
    local_scheme: Literal["http", "https"] = "http"
    local_password: SecretStr = SecretStr("")

    # --- safety / write gate ---
    allow_write: bool = False
    # NoDecode: hand the raw env string to ``_split_csv`` instead of letting
    # pydantic-settings JSON-decode it first — otherwise a comma-separated env
    # value (as shipped in .env.example) crashes startup with a JSON error.
    deny_systems: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["alarmsystem", "accessdoors", "door_intercom"]
    )
    allow_systems: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # --- http tuning ---
    timeout_seconds: float = 10.0
    repoll_delay_seconds: float = 2.0
    repoll_max_attempts: int = 3

    # --- logging / audit ---
    log_level: str = "INFO"
    audit_path: str = "audit/audit.jsonl"

    # --- multi-tenant hosting (Stage 2: LibreChat) ---
    # When true, credentials are NOT taken from env; each request carries its own
    # myGEKKO username/key/gekkoid as HTTP headers (per-user, cloud only). The
    # transport bearer token still guards the endpoint (LibreChat <-> MCP trust).
    multi_tenant: bool = False
    # Header name prefix carrying per-user credentials, e.g. "X-MyGEKKO-Username".
    tenant_header_prefix: str = "X-MyGEKKO-"
    # Safety cap on cached per-tenant backends (bounds memory under many users).
    tenant_cache_max: int = 256

    # --- seamless token auth (GEKKO BRAIN bridge, docs/concept/PRODUCTS.md) ---
    # When both are set, the client presents ONE opaque per-tenant token as
    # "Authorization: Bearer <gkmcp_…>". This server resolves it to that tenant's
    # myGEKKO creds by POSTing {token} to resolve_url with the shared secret — so
    # the user never re-enters credentials. Falls back to X-MyGEKKO-* headers when unset.
    resolve_url: str = ""  # e.g. https://app.kova.casa/api/mcp-auth/resolve
    resolve_secret: SecretStr = SecretStr("")  # MUST equal GEKKO BRAIN's MCP_RESOLVE_SECRET
    resolve_cache_ttl: float = 300.0  # seconds a resolved identity is cached (revocation latency)
    resolve_cache_max: int = 512

    # --- history export (GEKKO BRAIN's own tsdb/events, NOT the live myGEKKO controller) ---
    # Base URL for GET {base}/history and GET {base}/event-summary. Usually left unset — derived
    # from resolve_url (same GEKKO BRAIN host) via history_base_url_effective below.
    history_base_url: str = ""  # e.g. https://app.kova.casa/api/mcp

    # --- streamable-http transport (Phase 2) ---
    # Bind to loopback by default; a TLS reverse proxy (nginx) terminates HTTPS
    # and forwards to this. Never expose this port directly to the internet.
    http_host: str = "127.0.0.1"
    http_port: int = 8000
    http_path: str = "/mcp"
    # Bearer token the connector must present. Treat like a password.
    http_token: SecretStr = SecretStr("")
    # Safety guard: refuse to start HTTP without a token unless this is set true.
    http_allow_no_auth: bool = False
    # Stateless mode is friendlier for horizontally-scaled/proxied deployments.
    http_stateless: bool = True

    @field_validator("deny_systems", "allow_systems", mode="before")
    @classmethod
    def _split_csv(cls, v: object) -> object:
        """Accept comma-separated env strings as lists."""
        if isinstance(v, str):
            return [s.strip() for s in v.split(",") if s.strip()]
        return v

    @field_validator("gekkoid")
    @classmethod
    def _upper_gekkoid(cls, v: str) -> str:
        return v.strip().upper()

    # --- helpers ---
    @property
    def base_url(self) -> str:
        if self.mode is Mode.cloud:
            return CLOUD_BASE_URL
        return f"{self.local_scheme}://{self.local_host}/api/v1"

    def auth_params(self) -> dict[str, str]:
        """Query params carrying credentials for the selected mode."""
        if self.mode is Mode.cloud:
            return {
                "username": self.username,
                "key": self.key.get_secret_value(),
                "gekkoid": self.gekkoid,
            }
        return {
            "username": self.username,
            "password": self.local_password.get_secret_value(),
        }

    def validate_ready(self) -> list[str]:
        """Return a list of human-readable problems; empty means ready to connect."""
        problems: list[str] = []
        if not self.username:
            problems.append("GEKKO_USERNAME is empty")
        if self.mode is Mode.cloud:
            if not self.key.get_secret_value():
                problems.append("GEKKO_KEY is empty (cloud mode)")
            if not self.gekkoid or self.gekkoid == "XXXX-XXXX-XXXX-XXXX":
                problems.append("GEKKO_GEKKOID is empty/placeholder (cloud mode)")
        else:
            if not self.local_host:
                problems.append("GEKKO_LOCAL_HOST is empty (local mode)")
            if not self.local_password.get_secret_value():
                problems.append("GEKKO_LOCAL_PASSWORD is empty (local mode)")
        return problems

    @property
    def token_auth_enabled(self) -> bool:
        """Seamless per-tenant token auth: resolve each request's bearer token via GEKKO BRAIN."""
        return bool(self.resolve_url and self.resolve_secret.get_secret_value())

    @property
    def history_base_url_effective(self) -> str:
        """The history-endpoint base URL: explicit override, else derived from resolve_url."""
        if self.history_base_url:
            return self.history_base_url
        suffix = "/mcp-auth/resolve"
        if self.resolve_url.endswith(suffix):
            return self.resolve_url[: -len(suffix)] + "/mcp"
        return ""

    def validate_http_ready(self) -> list[str]:
        """Problems that would make the HTTP transport unsafe/unable to start."""
        problems: list[str] = []
        # Token-auth (per-tenant bearer resolved against GEKKO BRAIN) is itself the gate.
        if self.token_auth_enabled:
            return problems
        if not self.http_token.get_secret_value() and not self.http_allow_no_auth:
            problems.append(
                "GEKKO_HTTP_TOKEN is empty - set a bearer token, or set "
                "GEKKO_HTTP_ALLOW_NO_AUTH=true only for a trusted local test"
            )
        return problems

    def redacted(self) -> dict[str, object]:
        """A log-safe view of the settings (secrets masked)."""
        return {
            "backend": self.backend,
            "mode": self.mode.value,
            "base_url": self.base_url,
            "username": _mask_user(self.username),
            "gekkoid": self.gekkoid,
            "key": "***" if self.key.get_secret_value() else "(empty)",
            "allow_write": self.allow_write,
            "deny_systems": self.deny_systems,
            "allow_systems": self.allow_systems,
        }


def _mask_user(user: str) -> str:
    """Mask the local part of an email but keep the domain for debugging."""
    if "@" in user:
        local, _, domain = user.partition("@")
        head = local[:2] if len(local) > 2 else local[:1]
        return f"{head}***@{domain}"
    return user[:2] + "***" if len(user) > 2 else "***"


def load_settings() -> Settings:
    return Settings()
