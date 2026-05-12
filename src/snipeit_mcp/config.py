"""Configuration objects driven by environment variables.

Two independent concerns:

* :class:`TransportConfig` — how the MCP server is reached (stdio vs HTTP).
  Modelled after the corresponding object in Zammad-MCP, using the same generic
  ``MCP_TRANSPORT`` / ``MCP_HOST`` / ``MCP_PORT`` variables.
* :class:`SnipeITAuthConfig` — how the server authenticates to Snipe-IT. This
  is the mode-detection logic: OAuth provider config (interactive) takes
  precedence over the static personal-access-token fallback.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum

# Port validation constants
MIN_PORT = 1
MAX_PORT = 65535


class ConfigError(ValueError):
    """Raised when configuration is missing or inconsistent."""


class TransportType(str, Enum):
    """Supported MCP transport types."""

    STDIO = "stdio"
    HTTP = "http"


class AuthMode(str, Enum):
    """How tools authenticate to the upstream Snipe-IT instance."""

    OAUTH = "oauth"
    API_KEY = "api_key"


@dataclass
class TransportConfig:
    """Configuration for the MCP transport layer."""

    transport: TransportType = TransportType.STDIO
    host: str | None = None
    port: int | None = None

    @classmethod
    def from_env(cls) -> TransportConfig:
        """Read transport configuration from MCP_TRANSPORT / MCP_HOST / MCP_PORT."""
        transport_str = os.getenv("MCP_TRANSPORT", "stdio").lower()
        try:
            transport = TransportType(transport_str)
        except ValueError as exc:
            raise ConfigError(
                f"Invalid MCP_TRANSPORT '{transport_str}'. "
                f"Must be one of: {', '.join(t.value for t in TransportType)}"
            ) from exc

        host = os.getenv("MCP_HOST")
        port_str = os.getenv("MCP_PORT")
        port: int | None = None
        if port_str:
            try:
                port = int(port_str)
            except ValueError as exc:
                raise ConfigError(f"MCP_PORT must be a valid integer, got: {port_str}") from exc

        return cls(transport=transport, host=host, port=port)

    def validate(self) -> None:
        """Apply defaults and raise on invalid combinations."""
        if self.transport == TransportType.HTTP:
            if self.port is None:
                raise ConfigError("HTTP transport requires MCP_PORT to be set")
            if not MIN_PORT <= self.port <= MAX_PORT:
                raise ConfigError(
                    f"MCP_PORT must be between {MIN_PORT} and {MAX_PORT}, got: {self.port}"
                )
            if self.host is None:
                self.host = "127.0.0.1"


@dataclass
class SnipeITAuthConfig:
    """How the MCP server authenticates to the upstream Snipe-IT instance.

    Two mutually-exclusive modes:

    * **OAuth** — both ``SNIPEIT_OAUTH_CLIENT_ID`` and ``SNIPEIT_OAUTH_CLIENT_SECRET``
      are set. The MCP server runs an OAuth proxy that hands users off to Snipe-IT's
      Laravel Passport for interactive login; each request to a tool uses the
      authenticated user's own access token. Requires HTTP transport.
    * **API key** — ``SNIPEIT_TOKEN`` is set (and OAuth vars are not). The MCP
      server uses a single static personal-access token for every request. Works
      with both stdio and HTTP transports. Preserves upstream behaviour.
    """

    mode: AuthMode
    url: str
    # API-key mode
    token: str | None = None
    # OAuth mode
    oauth_client_id: str | None = None
    oauth_client_secret: str | None = None
    oauth_base_url: str | None = None
    oauth_redirect_path: str = "/auth/callback"

    @classmethod
    def from_env(cls) -> SnipeITAuthConfig:
        """Build auth config from SNIPEIT_* environment variables."""
        url = os.getenv("SNIPEIT_URL")
        if not url:
            raise ConfigError("SNIPEIT_URL is required")

        client_id = os.getenv("SNIPEIT_OAUTH_CLIENT_ID")
        client_secret = os.getenv("SNIPEIT_OAUTH_CLIENT_SECRET")
        token = os.getenv("SNIPEIT_TOKEN")

        # Helpful misconfiguration hints
        if client_id and not client_secret:
            raise ConfigError(
                "SNIPEIT_OAUTH_CLIENT_ID is set but SNIPEIT_OAUTH_CLIENT_SECRET is missing"
            )
        if client_secret and not client_id:
            raise ConfigError(
                "SNIPEIT_OAUTH_CLIENT_SECRET is set but SNIPEIT_OAUTH_CLIENT_ID is missing"
            )

        if client_id and client_secret:
            base_url = os.getenv("SNIPEIT_MCP_BASE_URL")
            if not base_url:
                raise ConfigError(
                    "OAuth mode requires SNIPEIT_MCP_BASE_URL — the public URL where "
                    "this MCP server is reachable (used to build the OAuth callback URL)"
                )
            return cls(
                mode=AuthMode.OAUTH,
                url=url,
                oauth_client_id=client_id,
                oauth_client_secret=client_secret,
                oauth_base_url=base_url,
                oauth_redirect_path=os.getenv("SNIPEIT_MCP_REDIRECT_PATH", "/auth/callback"),
            )

        if token:
            return cls(mode=AuthMode.API_KEY, url=url, token=token)

        raise ConfigError(
            "No Snipe-IT credentials configured. Set either:\n"
            "  - SNIPEIT_OAUTH_CLIENT_ID + SNIPEIT_OAUTH_CLIENT_SECRET + SNIPEIT_MCP_BASE_URL "
            "(interactive OAuth), or\n"
            "  - SNIPEIT_TOKEN (static personal-access token)"
        )

    def validate_with_transport(self, transport: TransportConfig) -> None:
        """Cross-validate auth config against the chosen transport."""
        if self.mode == AuthMode.OAUTH and transport.transport != TransportType.HTTP:
            raise ConfigError(
                "OAuth mode requires HTTP transport. "
                "Set MCP_TRANSPORT=http and MCP_PORT (the OAuth flow needs HTTP routes "
                "for /authorize and the callback)."
            )
