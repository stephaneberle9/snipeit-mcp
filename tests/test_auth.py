"""Tests for config, auth provider, and per-request token resolution."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest

from snipeit_mcp.auth import SnipeITOAuthProvider, SnipeITTokenVerifier
from snipeit_mcp.config import (
    AuthMode,
    ConfigError,
    SnipeITAuthConfig,
    TransportConfig,
    TransportType,
)


# ----- TransportConfig --------------------------------------------------------


class TestTransportConfig:
    def test_defaults_to_stdio(self):
        with patch.dict(os.environ, {}, clear=False):
            for key in ("MCP_TRANSPORT", "MCP_HOST", "MCP_PORT"):
                os.environ.pop(key, None)
            cfg = TransportConfig.from_env()
            cfg.validate()
            assert cfg.transport == TransportType.STDIO

    def test_http_requires_port(self):
        with patch.dict(os.environ, {"MCP_TRANSPORT": "http"}, clear=False):
            os.environ.pop("MCP_PORT", None)
            cfg = TransportConfig.from_env()
            with pytest.raises(ConfigError, match="MCP_PORT"):
                cfg.validate()

    def test_http_defaults_host_to_localhost(self):
        with patch.dict(
            os.environ, {"MCP_TRANSPORT": "http", "MCP_PORT": "8000"}, clear=False
        ):
            os.environ.pop("MCP_HOST", None)
            cfg = TransportConfig.from_env()
            cfg.validate()
            assert cfg.host == "127.0.0.1"
            assert cfg.port == 8000

    def test_invalid_transport_value(self):
        with patch.dict(os.environ, {"MCP_TRANSPORT": "websocket"}, clear=False):
            with pytest.raises(ConfigError, match="Invalid MCP_TRANSPORT"):
                TransportConfig.from_env()

    def test_invalid_port_value(self):
        with patch.dict(
            os.environ, {"MCP_TRANSPORT": "http", "MCP_PORT": "not-a-number"}, clear=False
        ):
            with pytest.raises(ConfigError, match="MCP_PORT must be a valid integer"):
                TransportConfig.from_env()


# ----- SnipeITAuthConfig ------------------------------------------------------


@pytest.fixture
def clean_snipeit_env():
    """Drop all SNIPEIT_* env vars so each test starts from a known empty state."""
    keys = [
        "SNIPEIT_URL",
        "SNIPEIT_TOKEN",
        "SNIPEIT_OAUTH_CLIENT_ID",
        "SNIPEIT_OAUTH_CLIENT_SECRET",
        "SNIPEIT_MCP_BASE_URL",
        "SNIPEIT_MCP_REDIRECT_PATH",
    ]
    saved = {k: os.environ.pop(k, None) for k in keys}
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


class TestSnipeITAuthConfig:
    def test_api_key_mode(self, clean_snipeit_env):
        os.environ["SNIPEIT_URL"] = "https://snipeit.example.com"
        os.environ["SNIPEIT_TOKEN"] = "static-token"
        cfg = SnipeITAuthConfig.from_env()
        assert cfg.mode == AuthMode.API_KEY
        assert cfg.token == "static-token"

    def test_oauth_mode(self, clean_snipeit_env):
        os.environ.update(
            {
                "SNIPEIT_URL": "https://snipeit.example.com",
                "SNIPEIT_OAUTH_CLIENT_ID": "client-id",
                "SNIPEIT_OAUTH_CLIENT_SECRET": "client-secret",  # noqa: S105 — test fixture
                "SNIPEIT_MCP_BASE_URL": "https://mcp.example.com",
            }
        )
        cfg = SnipeITAuthConfig.from_env()
        assert cfg.mode == AuthMode.OAUTH
        assert cfg.oauth_client_id == "client-id"
        assert cfg.oauth_base_url == "https://mcp.example.com"
        assert cfg.oauth_redirect_path == "/auth/callback"

    def test_oauth_takes_precedence_over_api_key(self, clean_snipeit_env):
        os.environ.update(
            {
                "SNIPEIT_URL": "https://snipeit.example.com",
                "SNIPEIT_OAUTH_CLIENT_ID": "client-id",
                "SNIPEIT_OAUTH_CLIENT_SECRET": "client-secret",  # noqa: S105
                "SNIPEIT_MCP_BASE_URL": "https://mcp.example.com",
                "SNIPEIT_TOKEN": "should-be-ignored-in-oauth-mode",
            }
        )
        cfg = SnipeITAuthConfig.from_env()
        assert cfg.mode == AuthMode.OAUTH

    def test_oauth_requires_base_url(self, clean_snipeit_env):
        os.environ.update(
            {
                "SNIPEIT_URL": "https://snipeit.example.com",
                "SNIPEIT_OAUTH_CLIENT_ID": "client-id",
                "SNIPEIT_OAUTH_CLIENT_SECRET": "client-secret",  # noqa: S105
            }
        )
        with pytest.raises(ConfigError, match="SNIPEIT_MCP_BASE_URL"):
            SnipeITAuthConfig.from_env()

    def test_partial_oauth_client_id_only_fails(self, clean_snipeit_env):
        os.environ.update(
            {
                "SNIPEIT_URL": "https://snipeit.example.com",
                "SNIPEIT_OAUTH_CLIENT_ID": "client-id",
            }
        )
        with pytest.raises(ConfigError, match="CLIENT_SECRET is missing"):
            SnipeITAuthConfig.from_env()

    def test_partial_oauth_client_secret_only_fails(self, clean_snipeit_env):
        os.environ.update(
            {
                "SNIPEIT_URL": "https://snipeit.example.com",
                "SNIPEIT_OAUTH_CLIENT_SECRET": "client-secret",  # noqa: S105
            }
        )
        with pytest.raises(ConfigError, match="CLIENT_ID is missing"):
            SnipeITAuthConfig.from_env()

    def test_no_credentials_fails(self, clean_snipeit_env):
        os.environ["SNIPEIT_URL"] = "https://snipeit.example.com"
        with pytest.raises(ConfigError, match="No Snipe-IT credentials"):
            SnipeITAuthConfig.from_env()

    def test_missing_url_fails(self, clean_snipeit_env):
        os.environ["SNIPEIT_TOKEN"] = "static-token"
        with pytest.raises(ConfigError, match="SNIPEIT_URL is required"):
            SnipeITAuthConfig.from_env()

    def test_oauth_rejects_stdio_transport(self, clean_snipeit_env):
        os.environ.update(
            {
                "SNIPEIT_URL": "https://snipeit.example.com",
                "SNIPEIT_OAUTH_CLIENT_ID": "client-id",
                "SNIPEIT_OAUTH_CLIENT_SECRET": "client-secret",  # noqa: S105
                "SNIPEIT_MCP_BASE_URL": "https://mcp.example.com",
            }
        )
        auth_cfg = SnipeITAuthConfig.from_env()
        transport = TransportConfig(transport=TransportType.STDIO)
        with pytest.raises(ConfigError, match="OAuth mode requires HTTP transport"):
            auth_cfg.validate_with_transport(transport)


# ----- SnipeITTokenVerifier ---------------------------------------------------


class _StubResponse:
    """Tiny stand-in for httpx.Response that supports just what the verifier needs."""

    def __init__(self, status_code: int, json_body: dict | None = None):
        self.status_code = status_code
        self._json = json_body if json_body is not None else {}

    def json(self):
        return self._json


@pytest.mark.asyncio
class TestSnipeITTokenVerifier:
    async def test_verify_valid_token(self):
        verifier = SnipeITTokenVerifier(snipeit_url="https://snipeit.example.com")
        with patch("snipeit_mcp.auth.httpx.AsyncClient") as client_cls:
            client = AsyncMock()
            client.__aenter__.return_value = client
            client.get = AsyncMock(
                return_value=_StubResponse(200, {"id": 42, "username": "alice"})
            )
            client_cls.return_value = client

            result = await verifier.verify_token("good-credential")
        assert result is not None
        assert result.token == "good-credential"
        assert result.client_id == "42"
        assert result.scopes == []

    async def test_verify_rejected_token(self):
        verifier = SnipeITTokenVerifier(snipeit_url="https://snipeit.example.com")
        with patch("snipeit_mcp.auth.httpx.AsyncClient") as client_cls:
            client = AsyncMock()
            client.__aenter__.return_value = client
            client.get = AsyncMock(return_value=_StubResponse(401))
            client_cls.return_value = client

            result = await verifier.verify_token("bad-credential")
        assert result is None

    async def test_verify_network_error(self):
        import httpx

        verifier = SnipeITTokenVerifier(snipeit_url="https://snipeit.example.com")
        with patch("snipeit_mcp.auth.httpx.AsyncClient") as client_cls:
            client = AsyncMock()
            client.__aenter__.return_value = client
            client.get = AsyncMock(side_effect=httpx.ConnectError("boom"))
            client_cls.return_value = client

            result = await verifier.verify_token("any-credential")
        assert result is None


# ----- SnipeITOAuthProvider ---------------------------------------------------


class TestSnipeITOAuthProvider:
    def test_builds_with_passport_endpoints(self):
        provider = SnipeITOAuthProvider(
            snipeit_url="https://snipeit.example.com/",
            client_id="cid",
            client_secret="csecret",  # noqa: S106 — test fixture
            base_url="https://mcp.example.com",
        )
        # The OAuthProxy stores the upstream endpoints as strings; verify the
        # trailing slash was stripped and the Passport path is used.
        assert provider._upstream_authorization_endpoint == (
            "https://snipeit.example.com/oauth/authorize"
        )
        assert provider._upstream_token_endpoint == (
            "https://snipeit.example.com/oauth/token"
        )
        assert isinstance(provider._token_validator, SnipeITTokenVerifier)


# ----- _resolve_token ---------------------------------------------------------


class TestResolveToken:
    def test_falls_back_to_env_when_no_context(self):
        from snipeit_mcp import client as client_mod

        with patch.dict(os.environ, {"SNIPEIT_TOKEN": "env-credential"}, clear=False):
            assert client_mod._resolve_token() == "env-credential"

    def test_prefers_oauth_token_over_env(self):
        from fastmcp.server.auth.auth import AccessToken

        from snipeit_mcp import client as client_mod

        oauth_access = AccessToken(
            token="oauth-credential",
            client_id="42",
            scopes=[],
            expires_at=None,
        )
        with (
            patch.dict(os.environ, {"SNIPEIT_TOKEN": "env-credential"}, clear=False),
            patch(
                "fastmcp.server.dependencies.get_access_token",
                return_value=oauth_access,
            ),
        ):
            assert client_mod._resolve_token() == "oauth-credential"
